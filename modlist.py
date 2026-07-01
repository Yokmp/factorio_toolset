"""
Manage the local Factorio mod-list.json profiles.

Examples:
    python tools/toolset/modlist.py show
    python tools/toolset/modlist.py apply vanilla
    python tools/toolset/modlist.py apply vanilla_dlc
    python tools/toolset/modlist.py save my_profile
    python tools/toolset/modlist.py delete my_profile
    python tools/toolset/modlist.py list
    python tools/toolset/modlist.py gui

An optional JSON file can add or override profiles:
{
  "profiles": {
    "example": {
      "label": "Example Mod Set",
      "mods": ["some-mod", "some-dependency"]
    }
  }
}
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_FACTORIO = Path(r"F:/Games/Factorio_ModTest/bin/x64/factorio.exe")
DEFAULT_PROFILES_JSON = TOOL_DIR / "modlist-profiles.json"
TOOL_UI_CONFIG = TOOL_DIR / "tool-ui.json"
APP_VERSION = "1.0.0"
TOOLSET_URL = "https://github.com/Yokmp/factorio_toolset"

VANILLA_MODS = ["base"]
DLC_MODS = ["elevated-rails", "quality", "space-age"]

BUILTIN_PROFILES: dict[str, dict[str, object]] = {
    "vanilla": {
        "label": "Vanilla",
        "mods": VANILLA_MODS,
    },
    "vanilla_dlc": {
        "label": "Vanilla + DLCs",
        "mods": VANILLA_MODS + DLC_MODS,
    },
    "ingredient_scrap": {
        "label": "Ingredient Scrap + DLCs",
        "mods": VANILLA_MODS + DLC_MODS + ["Ingredient_Scrap"],
    },
}


def load_tool_config(path: Path = TOOL_UI_CONFIG) -> dict[str, Any]:
    """Load local UI preferences such as paths, window geometry, and last profile."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_tool_config(config: dict[str, Any], path: Path = TOOL_UI_CONFIG) -> None:
    """Persist local UI preferences; this file is user-specific and generated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def update_tool_config(path: Path = TOOL_UI_CONFIG, **updates: Any) -> dict[str, Any]:
    """Merge selected UI config keys and remove keys whose update value is None."""
    config = load_tool_config(path)
    for key, value in updates.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    save_tool_config(config, path)
    return config


def config_path_value(config: dict[str, Any], key: str, default: Path | None = None) -> Path | None:
    """Read a path-like config value while keeping call sites tolerant of missing keys."""
    value = config.get(key)
    if isinstance(value, str) and value.strip():
        return Path(value)
    return default


def factorio_root(factorio_exe: Path) -> Path:
    """Return the Factorio install root from a path to factorio.exe."""
    if factorio_exe.parent.name.lower() == "x64" and factorio_exe.parent.parent.name.lower() == "bin":
        return factorio_exe.parent.parent.parent
    return factorio_exe.parent.parent


def default_mods_dir(factorio_exe: Path) -> Path:
    """Return Factorio's mods directory for a portable/local install."""
    return factorio_root(factorio_exe) / "mods"


def default_mod_list_file(factorio_exe: Path) -> Path:
    """Return Factorio's mod-list.json path for a portable/local install."""
    return default_mods_dir(factorio_exe) / "mod-list.json"


def mod_name_from_zip(zip_path: Path) -> str | None:
    """Read a mod name from a zipped mod by locating its inner info.json."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            info_names = [name for name in archive.namelist() if name.endswith("/info.json")]
            if not info_names:
                return None
            with archive.open(info_names[0]) as info_file:
                info = json.loads(info_file.read().decode("utf-8-sig"))
                return info.get("name")
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError, UnicodeDecodeError):
        return None


def mod_name_from_directory(directory: Path) -> str | None:
    """Read a mod name from an unpacked mod or Factorio data directory."""
    info_path = directory / "info.json"
    if not info_path.exists():
        return None
    try:
        info = json.loads(info_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return info.get("name")


def installed_mod_names(factorio_exe: Path, mods_dir: Path | None = None) -> set[str]:
    """Collect all installed mod names from Factorio data folders and the mods directory."""
    mods_dir = mods_dir or default_mods_dir(factorio_exe)
    names = {"base"}
    data_dir = factorio_root(factorio_exe) / "data"
    if data_dir.exists():
        for path in data_dir.iterdir():
            if path.is_dir() and (path / "info.json").exists():
                name = mod_name_from_directory(path)
                if isinstance(name, str) and name:
                    names.add(name)

    if mods_dir.exists():
        for path in mods_dir.iterdir():
            name = None
            if path.is_dir():
                name = mod_name_from_directory(path)
            elif path.suffix.lower() == ".zip":
                name = mod_name_from_zip(path)
            if isinstance(name, str) and name:
                names.add(name)
    return names


def load_profiles(profiles_json: Path | None = None) -> dict[str, dict[str, object]]:
    """Load built-in profiles plus any user-defined profiles from JSON."""
    profiles = {name: dict(profile) for name, profile in BUILTIN_PROFILES.items()}
    if profiles_json is None or not profiles_json.exists():
        return profiles

    data = json.loads(profiles_json.read_text(encoding="utf-8"))
    for name, profile in (data.get("profiles") or {}).items():
        if not isinstance(name, str) or not isinstance(profile, dict):
            continue
        profiles[name] = {
            "label": profile.get("label", name),
            "mods": list(profile.get("mods") or []),
            "settings": dict(profile.get("settings") or {}),
        }
    return profiles


def profile_label(profile_name: str, profiles: dict[str, dict[str, object]] | None = None) -> str:
    """Return the display label for a profile, falling back to the profile key."""
    profiles = profiles or load_profiles()
    return str(profiles[profile_name].get("label", profile_name))


def enabled_mods_for_profile(profile_name: str, profiles: dict[str, dict[str, object]] | None = None) -> set[str]:
    """Resolve the enabled mod set for a profile; base is always included."""
    profiles = profiles or load_profiles()
    if profile_name not in profiles:
        raise KeyError(f"Unknown mod profile: {profile_name}")
    return set(VANILLA_MODS) | set(profiles[profile_name].get("mods") or [])


def write_mod_list(enabled_mods: set[str], installed_mods: set[str], mod_list_file: Path) -> None:
    """Write Factorio's mod-list.json with every installed mod marked on or off."""
    all_mods = sorted(installed_mods | enabled_mods, key=str.lower)
    payload = {
        "mods": [
            {"name": name, "enabled": name in enabled_mods}
            for name in all_mods
        ]
    }
    mod_list_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_mod_list_entries(mod_list_file: Path) -> list[dict[str, Any]]:
    """Read valid mod entries from Factorio's mod-list.json."""
    if not mod_list_file.exists():
        return []
    data = json.loads(mod_list_file.read_text(encoding="utf-8"))
    entries = data.get("mods", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("name"), str)]


def read_enabled_mods(mod_list_file: Path) -> list[str]:
    """Return currently enabled mod names from mod-list.json."""
    return [entry["name"] for entry in read_mod_list_entries(mod_list_file) if entry.get("enabled")]


def current_mod_states(factorio_exe: Path, mods_dir: Path | None = None, mod_list_file: Path | None = None) -> dict[str, bool]:
    """Combine installed mods and mod-list.json into a name -> enabled mapping."""
    mod_list_file = mod_list_file or default_mod_list_file(factorio_exe)
    installed_mods = installed_mod_names(factorio_exe, mods_dir)
    states = {name: False for name in installed_mods}
    for entry in read_mod_list_entries(mod_list_file):
        states[entry["name"]] = bool(entry.get("enabled"))
    return dict(sorted(states.items(), key=lambda item: item[0].lower()))


def apply_enabled_mods(
    factorio_exe: Path,
    enabled_mods: set[str],
    mods_dir: Path | None = None,
    mod_list_file: Path | None = None,
) -> dict[str, Any]:
    """Validate and apply an explicit set of enabled mods to mod-list.json."""
    mod_list_file = mod_list_file or default_mod_list_file(factorio_exe)
    installed_mods = installed_mod_names(factorio_exe, mods_dir)
    missing = sorted(enabled_mods - installed_mods, key=str.lower)
    if missing:
        raise RuntimeError(f"Missing selected mods: {', '.join(missing)}")

    write_mod_list(enabled_mods, installed_mods, mod_list_file)
    return {
        "enabled": sorted(enabled_mods, key=str.lower),
        "disabled": sorted(installed_mods - enabled_mods, key=str.lower),
    }


def profile_key_from_label(label: str) -> str:
    """Create a stable JSON profile key from a user-facing label."""
    key = re.sub(r"[^a-z0-9]+", "_", label.strip().lower())
    return key.strip("_") or "custom_profile"


def save_profile(
    profiles_json: Path,
    label: str,
    enabled_mods: set[str],
    profile_name: str | None = None,
) -> str:
    """Save the current mod selection as a named profile in the profiles JSON."""
    profiles_json.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if profiles_json.exists():
        data = json.loads(profiles_json.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}

    profiles = data.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        data["profiles"] = profiles

    base_name = profile_name or profile_key_from_label(label)
    name = base_name
    suffix = 2
    while name in BUILTIN_PROFILES or name in profiles:
        name = f"{base_name}_{suffix}"
        suffix += 1

    profiles[name] = {
        "label": label.strip() or name,
        "mods": sorted(set(enabled_mods) | set(VANILLA_MODS), key=str.lower),
    }
    profiles_json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return name


def profile_settings(profile_name: str, profiles: dict[str, dict[str, object]] | None = None) -> dict[str, Any]:
    """Return saved startup settings for a profile, or an empty dict if none exist."""
    profiles = profiles or load_profiles()
    if profile_name not in profiles:
        raise KeyError(f"Unknown mod profile: {profile_name}")
    return dict(profiles[profile_name].get("settings") or {})


def update_profile_settings(profiles_json: Path, profile_name: str, settings: dict[str, Any]) -> None:
    """Store startup settings under a profile without discarding its mod selection."""
    profiles_json.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if profiles_json.exists():
        data = json.loads(profiles_json.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}

    profiles = data.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        data["profiles"] = profiles

    builtin_profile = BUILTIN_PROFILES.get(profile_name, {})
    profile = profiles.setdefault(
        profile_name,
        {
            "label": builtin_profile.get("label", profile_name),
            "mods": list(builtin_profile.get("mods") or []),
        },
    )
    if not isinstance(profile, dict):
        profile = {
            "label": builtin_profile.get("label", profile_name),
            "mods": list(builtin_profile.get("mods") or []),
        }
        profiles[profile_name] = profile
    profile.setdefault("label", builtin_profile.get("label", profile_name))
    profile.setdefault("mods", list(builtin_profile.get("mods") or []))
    profile["settings"] = dict(settings)
    profiles_json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def delete_profile(profiles_json: Path, profile_name: str) -> None:
    """Delete a user profile from JSON; built-in profiles are intentionally protected."""
    if profile_name in BUILTIN_PROFILES:
        raise ValueError(f"Built-in profile cannot be deleted: {profile_name}")
    if not profiles_json.exists():
        raise FileNotFoundError(f"Profiles JSON does not exist: {profiles_json}")

    data = json.loads(profiles_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        raise KeyError(f"Profile not found: {profile_name}")

    profiles = data["profiles"]
    if profile_name not in profiles:
        raise KeyError(f"Profile not found: {profile_name}")

    del profiles[profile_name]
    profiles_json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")



def apply_profile(
    factorio_exe: Path,
    profile_name: str = "vanilla",
    profiles_json: Path | None = None,
    mods_dir: Path | None = None,
    mod_list_file: Path | None = None,
) -> dict[str, Any]:
    """Apply the enabled mod list for a named profile."""
    mod_list_file = mod_list_file or default_mod_list_file(factorio_exe)
    profiles = load_profiles(profiles_json)
    enabled_mods = enabled_mods_for_profile(profile_name, profiles)
    installed_mods = installed_mod_names(factorio_exe, mods_dir)
    missing = sorted(enabled_mods - installed_mods, key=str.lower)
    if missing:
        raise RuntimeError(f"Missing mods for profile '{profile_name}': {', '.join(missing)}")

    write_mod_list(enabled_mods, installed_mods, mod_list_file)
    return {
        "profile": profile_name,
        "label": profile_label(profile_name, profiles),
        "enabled": sorted(enabled_mods, key=str.lower),
        "disabled": sorted(installed_mods - enabled_mods, key=str.lower),
    }


def fallback_profile_for_install(factorio_exe: Path) -> str:
    """Choose vanilla_dlc when Space Age is installed, otherwise vanilla."""
    installed_mods = installed_mod_names(factorio_exe)
    return "vanilla_dlc" if "space-age" in installed_mods else "vanilla"


def apply_last_and_launch(config_path: Path = TOOL_UI_CONFIG) -> dict[str, Any]:
    """Apply the last UI profile, fall back safely, then launch Factorio."""
    config = load_tool_config(config_path)
    factorio_exe = config_path_value(config, "factorio", DEFAULT_FACTORIO)
    profiles_json = config_path_value(config, "profiles_json", DEFAULT_PROFILES_JSON)
    if factorio_exe is None:
        raise RuntimeError(f"No factorio path stored in {config_path}")

    profile_name = config.get("last_profile")
    if not isinstance(profile_name, str) or not profile_name:
        profile_name = fallback_profile_for_install(factorio_exe)
        update_tool_config(config_path, last_profile=profile_name)

    result = apply_profile(factorio_exe, profile_name, profiles_json)
    subprocess.Popen([str(factorio_exe)], cwd=str(factorio_exe.parent))
    return result


def main() -> int:
    """Command line entry point for profile management and quick launching."""
    parser = argparse.ArgumentParser(
        description="Manage Factorio mod-list.json profiles. Part of Factorio Toolset.",
        epilog=f"Factorio Toolset: {TOOLSET_URL}",
    )
    parser.add_argument("--factorio", type=Path, help="Factorio executable path")
    parser.add_argument("--profiles-json", type=Path, help="optional JSON profile file")
    parser.add_argument("--config", type=Path, default=TOOL_UI_CONFIG, help="tool UI config JSON")
    parser.add_argument("--last", action="store_true", help="apply the last stored profile and launch Factorio")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="list available mod profiles")
    subparsers.add_parser("show", help="show currently enabled mods")
    subparsers.add_parser("gui", help="open the Tkinter UI")
    apply_parser = subparsers.add_parser("apply", help="write mod-list.json for a profile")
    apply_parser.add_argument("profile")
    save_parser = subparsers.add_parser("save", help="save the current enabled mods as a custom profile")
    save_parser.add_argument("profile", help="profile key to save")
    save_parser.add_argument("--label", help="display label for the profile")
    delete_parser = subparsers.add_parser("delete", help="delete a custom profile")
    delete_parser.add_argument("profile")

    args = parser.parse_args()
    config = load_tool_config(args.config)
    factorio_exe = args.factorio or config_path_value(config, "factorio", DEFAULT_FACTORIO)
    profiles_json = args.profiles_json or config_path_value(config, "profiles_json", DEFAULT_PROFILES_JSON)

    if args.last:
        result = apply_last_and_launch(args.config)
        print(f"Applied {result['profile']}: {result['label']}")
        print("enabled: " + ", ".join(result["enabled"]))
        print("Launched Factorio")
        return 0

    if args.command is None:
        parser.error("a command is required unless --last is used")

    profiles = load_profiles(profiles_json)

    if args.command == "list":
        for name in sorted(profiles):
            print(f"{name}: {profile_label(name, profiles)}")
        return 0

    if args.command == "show":
        enabled = read_enabled_mods(default_mod_list_file(factorio_exe))
        print("enabled: " + (", ".join(enabled) if enabled else "<none>"))
        return 0

    if args.command == "gui":
        from ui import run

        run(factorio_exe=factorio_exe, profiles_json=profiles_json)
        return 0

    if args.command == "apply":
        result = apply_profile(factorio_exe, args.profile, profiles_json)
        update_tool_config(args.config, factorio=str(factorio_exe), profiles_json=str(profiles_json), last_profile=args.profile)
        print(f"Applied {result['profile']}: {result['label']}")
        print("enabled: " + ", ".join(result["enabled"]))
        return 0

    if args.command == "save":
        if profiles_json is None:
            parser.error("--profiles-json is required for save")
        profile_name = save_profile(
            profiles_json,
            args.label or args.profile,
            set(read_enabled_mods(default_mod_list_file(factorio_exe))),
            profile_name=args.profile,
        )
        update_tool_config(args.config, profiles_json=str(profiles_json), last_profile=profile_name)
        print(f"Saved {profile_name}: {profile_label(profile_name, load_profiles(profiles_json))}")
        return 0

    if args.command == "delete":
        if profiles_json is None:
            parser.error("--profiles-json is required for delete")
        delete_profile(profiles_json, args.profile)
        if config.get("last_profile") == args.profile:
            update_tool_config(args.config, last_profile=None)
        print(f"Deleted {args.profile}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
