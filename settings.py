"""
Read and update the Factorio mod-settings.dat startup settings file.

Examples:
    python tools/toolset/settings.py --mod MODNAME --setting SETTING
    python tools/toolset/settings.py --mod MODNAME --setting SETTING --value true

The CLI defaults to the mod-settings.dat next to this mod folder. Factorio stores
startup settings by setting name inside the file; --mod is kept as a required
label so commands stay explicit when shared with other mod authors.
"""

from __future__ import annotations

import argparse
import json
import locale as system_locale
import re
import struct
import zipfile
from pathlib import Path
from typing import Any


DEFAULT_HEADER = bytes([2, 0, 0, 0, 77, 0, 0, 0, 0])
TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS_FILE = Path("mod-settings.dat")
DEFAULT_SETTINGS_LUA = TOOL_DIR / "settings.lua"
APP_VERSION = "1.0.0"
TOOLSET_URL = "https://github.com/Yokmp/factorio_toolset"


class PropertyTreeReader:
    """Minimal reader for Factorio's binary PropertyTree format."""

    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read_u8(self) -> int:
        value = self.data[self.offset]
        self.offset += 1
        return value

    def read_u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def read_f64(self) -> float:
        value = struct.unpack_from("<d", self.data, self.offset)[0]
        self.offset += 8
        return value

    def read_s64(self) -> int:
        value = struct.unpack_from("<q", self.data, self.offset)[0]
        self.offset += 8
        return value

    def read_string(self) -> str:
        is_nil = self.read_u8()
        if is_nil:
            return ""
        length = self.read_u8()
        if length == 255:
            length = self.read_u32()
        value = self.data[self.offset:self.offset + length].decode("utf-8")
        self.offset += length
        return value

    def read_node(self) -> Any:
        node_type = self.read_u8()
        if node_type == 0:
            return None
        self.read_u8()
        if node_type == 1:
            return self.read_u8() != 0
        if node_type == 2:
            return self.read_f64()
        if node_type == 3:
            return self.read_string()
        if node_type == 4:
            return [self.read_node() for _ in range(self.read_u32())]
        if node_type == 5:
            return {self.read_string(): self.read_node() for _ in range(self.read_u32())}
        if node_type == 6:
            return self.read_s64()
        raise ValueError(f"Unknown property tree node type {node_type} at byte {self.offset - 1}")


class PropertyTreeWriter:
    """Minimal writer for the subset of PropertyTree values used by mod-settings.dat."""

    def __init__(self):
        self.out = bytearray()

    def write_u8(self, value: int) -> None:
        self.out.append(value)

    def write_u32(self, value: int) -> None:
        self.out.extend(struct.pack("<I", value))

    def write_f64(self, value: float) -> None:
        self.out.extend(struct.pack("<d", value))

    def write_s64(self, value: int) -> None:
        self.out.extend(struct.pack("<q", value))

    def write_string(self, value: str) -> None:
        encoded = value.encode("utf-8")
        self.write_u8(0)
        if len(encoded) < 255:
            self.write_u8(len(encoded))
        else:
            self.write_u8(255)
            self.write_u32(len(encoded))
        self.out.extend(encoded)

    def write_node(self, value: Any) -> None:
        if value is None:
            self.write_u8(0)
        elif isinstance(value, bool):
            self.write_u8(1)
            self.write_u8(0)
            self.write_u8(1 if value else 0)
        elif isinstance(value, int):
            self.write_u8(6)
            self.write_u8(0)
            self.write_s64(value)
        elif isinstance(value, float):
            self.write_u8(2)
            self.write_u8(0)
            self.write_f64(float(value))
        elif isinstance(value, str):
            self.write_u8(3)
            self.write_u8(0)
            self.write_string(value)
        elif isinstance(value, list):
            self.write_u8(4)
            self.write_u8(0)
            self.write_u32(len(value))
            for item in value:
                self.write_node(item)
        elif isinstance(value, dict):
            self.write_u8(5)
            self.write_u8(0)
            self.write_u32(len(value))
            for key, item in value.items():
                self.write_string(str(key))
                self.write_node(item)
        else:
            raise TypeError(f"Unsupported property tree value: {value!r}")


def read_mod_settings_bytes(data: bytes) -> tuple[bytes, dict[str, Any]]:
    """Split a mod-settings.dat byte stream into Factorio's header and root dict."""
    header = data[:9]
    reader = PropertyTreeReader(data[9:])
    root = reader.read_node()
    if not isinstance(root, dict):
        raise ValueError("mod-settings.dat root is not a dictionary")
    return header, root


def write_mod_settings_bytes(header: bytes, root: dict[str, Any]) -> bytes:
    """Serialize a root dict back to bytes while preserving Factorio's file header."""
    writer = PropertyTreeWriter()
    writer.write_node(root)
    return header + bytes(writer.out)


def read_mod_settings(path: Path) -> tuple[bytes, dict[str, Any]]:
    """Read mod-settings.dat, or return an empty tree with a default header."""
    if not path.exists():
        return DEFAULT_HEADER, {}
    return read_mod_settings_bytes(path.read_bytes())


def write_mod_settings(path: Path, header: bytes, root: dict[str, Any]) -> None:
    """Write the full mod-settings.dat tree back to disk."""
    path.write_bytes(write_mod_settings_bytes(header, root))


def parse_value(value: str) -> Any:
    """Parse CLI/UI strings into bool, nil, int, float, or plain string values."""
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "nil":
        return None
    try:
        if "." not in value:
            return int(value)
        return float(value)
    except ValueError:
        return value


def parse_lua_value(value: str) -> Any:
    """Parse simple Lua literals from settings.lua default/min/max fields."""
    value = value.strip().rstrip(",")
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def parse_allowed_values(value: str) -> list[str]:
    """Extract string options from a Factorio allowed_values Lua array."""
    return re.findall(r'"([^"]+)"', value)


def preferred_language() -> str:
    """Return the system language code used to prefer locale/<lang> labels."""
    language, _encoding = system_locale.getlocale()
    if not language:
        return "en"
    return language.split("_", 1)[0].lower()


def read_locale_sections(language: str, fallback_language: str = "en", mod_dir: Path = TOOL_DIR) -> dict[str, dict[str, str]]:
    """Read Factorio-style locale cfg sections, with fallback loaded first."""
    sections: dict[str, dict[str, str]] = {}
    for lang in (fallback_language, language):
        locale_dir = mod_dir / "locale" / lang
        if not locale_dir.exists():
            continue
        for cfg_path in sorted(locale_dir.glob("*.cfg")):
            current_section: str | None = None
            for raw_line in cfg_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw_line.strip()
                if not line or line.startswith(";") or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current_section = line[1:-1]
                    sections.setdefault(current_section, {})
                    continue
                if current_section is None or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                sections.setdefault(current_section, {})[key.strip()] = value.strip()
    return sections


def read_locale_sections_from_zip(zip_path: Path, language: str, fallback_language: str = "en") -> dict[str, dict[str, str]]:
    """Read Factorio-style locale cfg sections from a zipped mod."""
    sections: dict[str, dict[str, str]] = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        for lang in (fallback_language, language):
            marker = f"/locale/{lang}/"
            cfg_names = sorted(name for name in names if marker in name and name.endswith(".cfg"))
            for cfg_name in cfg_names:
                current_section: str | None = None
                text = archive.read(cfg_name).decode("utf-8-sig")
                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith(";") or line.startswith("#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        current_section = line[1:-1]
                        sections.setdefault(current_section, {})
                        continue
                    if current_section is None or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    sections.setdefault(current_section, {})[key.strip()] = value.strip()
    return sections


def localised_key(block: str, section: str) -> str | None:
    """Find an explicit localised_name/localised_description key in a Lua block."""
    match = re.search(rf'"{re.escape(section)}\.([^"]+)"', block)
    return match.group(1) if match else None


def display_text(name: str, block: str, sections: dict[str, dict[str, str]], section: str) -> str:
    """Resolve a localized display string, falling back to the raw setting name."""
    key = localised_key(block, section) or name
    return sections.get(section, {}).get(key, name)


def read_static_startup_settings(
    settings_lua: Path = DEFAULT_SETTINGS_LUA,
    language: str | None = None,
    mod_name: str | None = None,
    locale_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Parse startup setting definitions from settings.lua for the Settings UI."""
    if not settings_lua.exists():
        return []
    mod_dir = locale_dir or settings_lua.parent
    sections = read_locale_sections(language or preferred_language(), mod_dir=mod_dir)
    return read_static_startup_settings_text(settings_lua.read_text(encoding="utf-8"), sections, mod_name)


def read_static_startup_settings_text(
    settings_text: str,
    sections: dict[str, dict[str, str]],
    mod_name: str | None = None,
) -> list[dict[str, Any]]:
    """Parse startup setting definitions from settings.lua text."""
    lines = settings_text.splitlines()
    definitions: list[dict[str, Any]] = []
    blocks: list[str] = []
    in_extend = False
    current: list[str] | None = None
    depth = 0
    for line in lines:
        stripped = line.strip()
        if not in_extend:
            if stripped.startswith("data:extend({"):
                in_extend = True
            continue
        if stripped.startswith("})"):
            break
        if current is None:
            if stripped.startswith("{"):
                current = [line]
                depth = line.count("{") - line.count("}")
                if depth == 0:
                    blocks.append("\n".join(current))
                    current = None
            continue
        current.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            blocks.append("\n".join(current))
            current = None

    for block in blocks:
        if 'setting_type = "startup"' not in block:
            continue
        name_match = re.search(r'name\s*=\s*"([^"]+)"', block)
        type_match = re.search(r'type\s*=\s*"([^"]+)"', block)
        default_match = re.search(r"default_value\s*=\s*([^,\n]+)", block)
        if not name_match or not type_match:
            continue
        definition: dict[str, Any] = {
            "name": name_match.group(1),
            "type": type_match.group(1),
            "default": parse_lua_value(default_match.group(1)) if default_match else None,
            "hidden": "hidden = true" in block,
        }
        if mod_name:
            definition["mod"] = mod_name
        definition["display_name"] = display_text(definition["name"], block, sections, "mod-setting-name")
        definition["description"] = display_text(definition["name"], block, sections, "mod-setting-description")
        for field in ("minimum_value", "maximum_value"):
            match = re.search(rf"{field}\s*=\s*([^,\n]+)", block)
            if match:
                definition[field] = parse_lua_value(match.group(1))
        allowed_match = re.search(r"allowed_values\s*=\s*\{([^}]+)\}", block, flags=re.DOTALL)
        if allowed_match:
            definition["allowed_values"] = parse_allowed_values(allowed_match.group(1))
        definitions.append(definition)
    return definitions


def factorio_root(factorio_exe: Path) -> Path:
    """Return the Factorio install root from a path to factorio.exe."""
    if factorio_exe.parent.name.lower() == "x64" and factorio_exe.parent.parent.name.lower() == "bin":
        return factorio_exe.parent.parent.parent
    return factorio_exe.parent.parent


def default_mods_dir(factorio_exe: Path) -> Path:
    """Return Factorio's mods directory for a portable/local install."""
    return factorio_root(factorio_exe) / "mods"


def default_settings_file(factorio_exe: Path | None = None) -> Path:
    """Return the best default mod-settings.dat path."""
    if factorio_exe is not None:
        return factorio_root(factorio_exe) / "mod-settings.dat"
    return DEFAULT_SETTINGS_FILE


def mod_name_from_directory(directory: Path) -> str | None:
    """Read a mod name from an unpacked mod or Factorio data directory."""
    info_path = directory / "info.json"
    if not info_path.exists():
        return None
    try:
        info = json.loads(info_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    name = info.get("name")
    return name if isinstance(name, str) and name else None


def mod_name_from_zip(zip_path: Path) -> str | None:
    """Read a mod name from a zipped mod by locating its inner info.json."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            info_names = [name for name in archive.namelist() if name.endswith("/info.json")]
            if not info_names:
                return None
            info = json.loads(archive.read(info_names[0]).decode("utf-8-sig"))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError):
        return None
    name = info.get("name")
    return name if isinstance(name, str) and name else None


def read_static_startup_settings_from_zip(
    zip_path: Path,
    language: str | None = None,
    mod_name: str | None = None,
) -> list[dict[str, Any]]:
    """Parse startup setting definitions from a zipped mod."""
    with zipfile.ZipFile(zip_path) as archive:
        settings_names = [name for name in archive.namelist() if name.endswith("/settings.lua")]
        if not settings_names:
            return []
        settings_text = archive.read(settings_names[0]).decode("utf-8-sig")
    sections = read_locale_sections_from_zip(zip_path, language or preferred_language())
    return read_static_startup_settings_text(settings_text, sections, mod_name)


def read_startup_settings_for_mods(
    mod_names: set[str],
    factorio_exe: Path | None = None,
    mods_dir: Path | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Parse startup setting definitions for all enabled installed mods."""
    definitions: list[dict[str, Any]] = []
    found: set[str] = set()

    data_dirs: list[Path] = []
    if factorio_exe is not None:
        data_root = factorio_root(factorio_exe) / "data"
        if data_root.exists():
            data_dirs.extend(path for path in data_root.iterdir() if path.is_dir())
        if mods_dir is None:
            mods_dir = default_mods_dir(factorio_exe)

    for directory in data_dirs:
        name = mod_name_from_directory(directory)
        if name in mod_names:
            definitions.extend(read_static_startup_settings(directory / "settings.lua", language, name, directory))
            found.add(name)

    if mods_dir is not None and mods_dir.exists():
        for path in sorted(mods_dir.iterdir(), key=lambda item: item.name.lower()):
            if path.is_dir():
                name = mod_name_from_directory(path)
                if name in mod_names:
                    definitions.extend(read_static_startup_settings(path / "settings.lua", language, name, path))
                    found.add(name)
            elif path.suffix.lower() == ".zip":
                name = mod_name_from_zip(path)
                if name in mod_names:
                    definitions.extend(read_static_startup_settings_from_zip(path, language, name))
                    found.add(name)

    return definitions


def get_startup_settings(path: Path, definitions: list[dict[str, Any]]) -> dict[str, Any]:
    """Read current startup values, using definition defaults for missing entries."""
    values: dict[str, Any] = {}
    for definition in definitions:
        name = definition["name"]
        value = get_startup_setting(path, name)
        values[name] = definition.get("default") if value is None else value
    return values


def set_startup_settings(path: Path, settings: dict[str, Any]) -> None:
    """Write multiple startup settings into mod-settings.dat."""
    header, root = read_mod_settings(path)
    startup = root.setdefault("startup", {})
    if not isinstance(startup, dict):
        raise ValueError("mod-settings.dat startup section is not a dictionary")
    for setting, value in settings.items():
        startup[setting] = {"value": value}
    write_mod_settings(path, header, root)


def set_startup_setting(path: Path, setting: str, value: Any) -> None:
    """Write one startup setting into mod-settings.dat."""
    header, root = read_mod_settings(path)
    startup = root.setdefault("startup", {})
    if not isinstance(startup, dict):
        raise ValueError("mod-settings.dat startup section is not a dictionary")
    startup[setting] = {"value": value}
    write_mod_settings(path, header, root)


def get_startup_setting(path: Path, setting: str) -> Any:
    """Read one startup setting value; returns None when it is not present."""
    _, root = read_mod_settings(path)
    startup = root.get("startup", {})
    if not isinstance(startup, dict):
        return None
    entry = startup.get(setting)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def main() -> int:
    """Small CLI for reading or updating one startup setting."""
    parser = argparse.ArgumentParser(
        description="Read or update the local Factorio mod-settings.dat startup setting.",
        epilog=(
            "Default settings file: derived from --factorio when provided; otherwise ./mod-settings.dat\n"
            "Values are parsed as true, false, nil, integers, floats, or strings. "
            "Factorio stores startup settings by setting name; --mod is required "
            "for command clarity but is not a separate namespace in mod-settings.dat.\n"
            f"Part of Factorio Toolset: {TOOLSET_URL}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mod", required=True, metavar="MODNAME", help="Mod name label, for example Ingredient_Scrap")
    parser.add_argument("--setting", required=True, metavar="SETTING", help="Startup setting name")
    parser.add_argument("--value", metavar="VALUE", help="Value to write. Omit to print the current value.")
    parser.add_argument("--factorio", type=Path, help="Factorio executable path used to locate mod-settings.dat when --file is omitted")
    parser.add_argument("--file", type=Path, default=DEFAULT_SETTINGS_FILE, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    args = parser.parse_args()

    settings_file = args.file if args.file != DEFAULT_SETTINGS_FILE else default_settings_file(args.factorio)

    if args.value is None:
        print(json.dumps(get_startup_setting(settings_file, args.setting), ensure_ascii=False))
    else:
        value = parse_value(args.value)
        set_startup_setting(settings_file, args.setting, value)
        print(f"{args.setting} = {json.dumps(value, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
