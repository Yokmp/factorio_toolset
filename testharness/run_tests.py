"""
Run Factorio integration tests for a target mod.

The target mod writes a JSON report from control.lua to:
    script-output/<mod-name>/test-report.json

Usage:
    python tools/test/run_tests.py --profile default
    python tools/test/run_tests.py --all
    python tools/test/run_tests.py --mod-root F:/Games/Factorio_ModTest/mods/Some_Mod --profile default
    python tools/test/run_tests.py --factorio F:/Games/Factorio_ModTest/bin/x64/factorio.exe --all
    python tools/test/run_tests.py --mod-profile angels_is --check-unused-prototype-data --keep-mod-list
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def find_mod_root(start: Path) -> Path | None:
    """Return the nearest parent directory containing a Factorio mod info.json."""
    current = start.resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / "info.json").is_file():
            return candidate
    return None


def default_mod_root() -> Path:
    """Return the target mod root for the classic in-mod or external Toolsets layout."""
    return find_mod_root(Path.cwd()) or find_mod_root(SCRIPT_DIR) or SCRIPT_DIR.parent.parent


def default_toolset_dir() -> Path:
    """Return the sibling toolset folder for both supported layouts."""
    candidates = [
        SCRIPT_DIR.parent / "toolset",
        SCRIPT_DIR.parent / "factorio-toolset",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


MOD_DIR = default_mod_root()
TOOLS_DIR = MOD_DIR / "tools"
TOOLSET_DIR = default_toolset_dir()
sys.path.insert(0, str(TOOLSET_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import settings
import modlist

MODS_DIR = MOD_DIR.parent
DEFAULT_FACTORIO = Path(r"F:/Games/Factorio_ModTest/bin/x64/factorio.exe")
DEFAULT_IS_MOD_NAME = "Ingredient_Scrap"
DEFAULT_IS_TEST_MOD_PROFILE = "ingredient_scrap"
DEFAULT_IS_DEBUG_SETTING = "yis-IS_DEBUG"
DEFAULT_IS_ARTIFACTS = [
    "test-report.json",
    "data-table.lua",
    "material-flow.json",
    "production-flow.json",
    "technology-flow.json",
    "ancestry-runtime.json",
    "recipe-forms.json",
]
REPORT_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "test-report.json"
DATA_TABLE_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "data-table.lua"
MATERIAL_FLOW_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "material-flow.json"
MATERIAL_FLOW_STATE_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "material-flow-data.js"
PRODUCTION_FLOW_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "production-flow.json"
PRODUCTION_FLOW_STATE_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "production-flow-data.js"
TECHNOLOGY_FLOW_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "technology-flow.json"
TECHNOLOGY_FLOW_STATE_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "technology-flow-data.js"
ANCESTRY_RUNTIME_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "ancestry-runtime.json"
RECIPE_FORMS_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "recipe-forms.json"
ICON_ASSETS_RELATIVE = Path(DEFAULT_IS_MOD_NAME) / "icon-assets"
PROFILE_FILE = SCRIPT_DIR / "profile.lua"
TMP_DIR = SCRIPT_DIR / "tmp"
MOD_SETTINGS_FILE = MODS_DIR / "mod-settings.dat"
MOD_SETTINGS_BACKUP_FILE = MODS_DIR / "mod-settings.dat.codex-test-backup"
TIMEOUT = 180
DEFAULT_TEST_MOD_PROFILE = DEFAULT_IS_TEST_MOD_PROFILE
DEFAULT_DEBUG_SETTING = DEFAULT_IS_DEBUG_SETTING
DEFAULT_SETTINGS_MOD = DEFAULT_IS_MOD_NAME
HARNESS_MOD_NAME = DEFAULT_IS_MOD_NAME
HARNESS_ARTIFACTS = list(DEFAULT_IS_ARTIFACTS)
HARNESS_CONFIG: dict[str, object] = {}
SETTING_PROFILES: dict[str, dict[str, object]] = {"default": {}}
SETTING_PROFILE_GROUPS: dict[str, list[str]] = {}


DEFAULT_IS_PROFILES = {
    "default": {},
    "fixed_amount": {"fixed_amount": True},
    "limit_off": {"limit": False},
    "probability_min": {"probability": 1},
    "probability_full": {"probability": 100},
    "needed_min": {"needed": 1},
    "needed_high": {"needed": 20},
    "hide_tech_quiet": {"hide_tech": True, "shallow_log": False},
    "show_tech_quiet": {"hide_tech": False, "shallow_log": False},
    "show_tech_debug": {"hide_tech": False, "shallow_log": True},
    "hide_tech_debug": {"hide_tech": True, "shallow_log": True},
    "recipe_chain_targets": {"recipe_chain_targets": True},
    "ancestry_component_heavy": {"ancestry_mode": "component-heavy"},
    "ancestry_material_heavy": {"ancestry_mode": "material-heavy"},
    "ancestry_width_1": {"ancestry_mixed_limit": 1},
    "ancestry_width_2": {"ancestry_mixed_limit": 2},
    "ancestry_depth_3": {"ancestry_max_depth": 3},
    "toggles_off": {"limit": False, "fluids": False},
}
PROFILES = dict(DEFAULT_IS_PROFILES)


def load_mod_info(mod_root: Path) -> dict[str, object]:
    """Read the target mod's info.json when available."""
    info_path = mod_root / "info.json"
    if not info_path.exists():
        return {}
    try:
        info = json.loads(info_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return info if isinstance(info, dict) else {}


def load_harness_config(mod_root: Path) -> dict[str, object]:
    """Read tools/test/harness.json from the target mod when present."""
    config_path = mod_root / "tools" / "test" / "harness.json"
    if not config_path.exists():
        return {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid harness config {config_path}: {exc}") from exc
    return config if isinstance(config, dict) else {}


def path_value(value: object, fallback: Path) -> Path:
    """Convert a config path value into a normalized relative Path."""
    if isinstance(value, str) and value.strip():
        return Path(value.replace("\\", "/"))
    return fallback


def optional_config_path(value: object, base_dir: Path) -> Path | None:
    """Return an absolute config path from a string value, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.replace("\\", "/"))
    if not path.is_absolute():
        path = base_dir / path
    return path


def normalize_profiles(value: object, fallback: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    """Return a profile dictionary with only object-like profile settings."""
    if not isinstance(value, dict):
        return dict(fallback)
    profiles: dict[str, dict[str, object]] = {}
    for name, settings_value in value.items():
        if not isinstance(name, str):
            continue
        if settings_value is None:
            profiles[name] = {}
        elif isinstance(settings_value, dict):
            profiles[name] = dict(settings_value)
    return profiles or dict(fallback)


def normalize_artifacts(value: object, fallback: list[str]) -> list[str]:
    """Return configured output artifact names or relative paths."""
    if not isinstance(value, list):
        return list(fallback)
    artifacts: list[str] = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            artifacts.append(entry.strip())
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            artifacts.append(str(entry["path"]).strip())
    return artifacts or list(fallback)


def normalize_setting_profiles(value: object) -> dict[str, dict[str, object]]:
    """Return startup setting profiles from harness config."""
    if not isinstance(value, dict):
        return {"default": {}}
    profiles: dict[str, dict[str, object]] = {}
    for name, settings_value in value.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if settings_value is None:
            profiles[name] = {}
        elif isinstance(settings_value, dict):
            profiles[name] = dict(settings_value)
    profiles.setdefault("default", {})
    return profiles


def normalize_setting_profile_groups(value: object) -> dict[str, list[str]]:
    """Return startup setting profile groups from harness config."""
    if not isinstance(value, dict):
        return {}
    groups: dict[str, list[str]] = {}
    for name, entries in value.items():
        if not isinstance(name, str) or not isinstance(entries, list):
            continue
        groups[name] = [entry for entry in entries if isinstance(entry, str) and entry]
    return groups


def default_profiles_for_mod(mod_name: str) -> dict[str, dict[str, object]]:
    """Keep the rich Ingredient Scrap defaults, but stay generic for other mods."""
    if mod_name == DEFAULT_IS_MOD_NAME:
        return dict(DEFAULT_IS_PROFILES)
    return {"default": {}}


def default_artifacts_for_mod(mod_name: str) -> list[str]:
    """Only Ingredient Scrap expects extra debug artifacts by default."""
    if mod_name == DEFAULT_IS_MOD_NAME:
        return list(DEFAULT_IS_ARTIFACTS)
    return ["test-report.json"]


def artifact_relative_path(artifact: str) -> Path:
    """Map a configured artifact name to its script-output relative path."""
    normalized = artifact.replace("\\", "/").strip()
    path = Path(normalized)
    if "/" in normalized:
        return path
    if normalized == "test-report.json":
        return REPORT_RELATIVE
    return REPORT_RELATIVE.parent / normalized


def set_output_relative_paths(report_relative: Path, mod_name: str) -> None:
    """Set the report path and all standard sibling artifact paths."""
    global REPORT_RELATIVE, DATA_TABLE_RELATIVE, MATERIAL_FLOW_RELATIVE, MATERIAL_FLOW_STATE_RELATIVE
    global PRODUCTION_FLOW_RELATIVE, PRODUCTION_FLOW_STATE_RELATIVE
    global TECHNOLOGY_FLOW_RELATIVE, TECHNOLOGY_FLOW_STATE_RELATIVE
    global ANCESTRY_RUNTIME_RELATIVE, RECIPE_FORMS_RELATIVE, ICON_ASSETS_RELATIVE

    output_dir = report_relative.parent if str(report_relative.parent) != "." else Path(mod_name)
    REPORT_RELATIVE = report_relative
    DATA_TABLE_RELATIVE = output_dir / "data-table.lua"
    MATERIAL_FLOW_RELATIVE = output_dir / "material-flow.json"
    MATERIAL_FLOW_STATE_RELATIVE = output_dir / "material-flow-data.js"
    PRODUCTION_FLOW_RELATIVE = output_dir / "production-flow.json"
    PRODUCTION_FLOW_STATE_RELATIVE = output_dir / "production-flow-data.js"
    TECHNOLOGY_FLOW_RELATIVE = output_dir / "technology-flow.json"
    TECHNOLOGY_FLOW_STATE_RELATIVE = output_dir / "technology-flow-data.js"
    ANCESTRY_RUNTIME_RELATIVE = output_dir / "ancestry-runtime.json"
    RECIPE_FORMS_RELATIVE = output_dir / "recipe-forms.json"
    ICON_ASSETS_RELATIVE = output_dir / "icon-assets"


def refresh_harness_config() -> None:
    """Resolve active harness defaults from info.json and tools/test/harness.json."""
    global HARNESS_MOD_NAME, HARNESS_ARTIFACTS, HARNESS_CONFIG, PROFILES
    global SETTING_PROFILES, SETTING_PROFILE_GROUPS
    global DEFAULT_TEST_MOD_PROFILE, DEFAULT_DEBUG_SETTING, DEFAULT_SETTINGS_MOD

    info = load_mod_info(MOD_DIR)
    config = load_harness_config(MOD_DIR)
    HARNESS_CONFIG = dict(config)
    info_name = str(info.get("name") or MOD_DIR.name)
    mod_name = str(config.get("mod_name") or info_name)
    report_relative = path_value(config.get("report_path"), Path(mod_name) / "test-report.json")

    HARNESS_MOD_NAME = mod_name
    set_output_relative_paths(report_relative, mod_name)

    default_profiles = default_profiles_for_mod(mod_name)
    PROFILES = normalize_profiles(config.get("profiles"), default_profiles)
    HARNESS_ARTIFACTS = normalize_artifacts(config.get("artifacts"), default_artifacts_for_mod(mod_name))
    SETTING_PROFILES = normalize_setting_profiles(config.get("setting_profiles"))
    SETTING_PROFILE_GROUPS = normalize_setting_profile_groups(config.get("setting_profile_groups"))

    DEFAULT_SETTINGS_MOD = str(config.get("settings_mod") or mod_name)
    DEFAULT_DEBUG_SETTING = str(config.get("debug_setting") or (DEFAULT_IS_DEBUG_SETTING if mod_name == DEFAULT_IS_MOD_NAME else ""))
    config_mod_profile = config.get("mod_profile")
    if isinstance(config_mod_profile, str) and config_mod_profile.strip():
        DEFAULT_TEST_MOD_PROFILE = config_mod_profile.strip()
    elif mod_name == DEFAULT_IS_MOD_NAME:
        DEFAULT_TEST_MOD_PROFILE = DEFAULT_IS_TEST_MOD_PROFILE
    else:
        DEFAULT_TEST_MOD_PROFILE = ""


def configure_mod_root(mod_root: Path) -> None:
    """Point the harness at the Factorio mod that contains the Lua test files."""
    global MOD_DIR, TOOLS_DIR, MODS_DIR, PROFILE_FILE, TMP_DIR, MOD_SETTINGS_FILE, MOD_SETTINGS_BACKUP_FILE
    MOD_DIR = mod_root.resolve()
    TOOLS_DIR = MOD_DIR / "tools"
    MODS_DIR = MOD_DIR.parent
    PROFILE_FILE = MOD_DIR / "tools" / "test" / "profile.lua"
    TMP_DIR = MOD_DIR / "tools" / "test" / "tmp"
    MOD_SETTINGS_FILE = MODS_DIR / "mod-settings.dat"
    MOD_SETTINGS_BACKUP_FILE = MODS_DIR / "mod-settings.dat.codex-test-backup"
    refresh_harness_config()


refresh_harness_config()

COLOR = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "gray": "\033[90m",
}


def colored(text: str, color: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    return f"{COLOR[color]}{text}{COLOR['reset']}"


def status_label(status: str | None, color: bool = True) -> str:
    if status == "pass":
        return colored("PASS", "green", color)
    if status == "fail":
        return colored("FAIL", "red", color)
    return colored(str(status or "UNKNOWN").upper(), "yellow", color)


def lua_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)


def write_profile(profile_name: str, settings: dict[str, object]) -> None:
    lines = ["return {", f"  name = {json.dumps(profile_name)},", "  settings = {"]
    for key, value in sorted(settings.items()):
        lines.append(f"    {key} = {lua_value(value)},")
    lines.extend(["  },", "}", ""])
    PROFILE_FILE.write_text("\n".join(lines), encoding="utf-8")


def apply_startup_settings(profile_settings: dict[str, object], debug_setting: str | None = None) -> bytes | None:
    """Write startup settings for one Factorio run and return exact previous bytes."""
    original = MOD_SETTINGS_FILE.read_bytes() if MOD_SETTINGS_FILE.exists() else None
    merged = dict(profile_settings)
    if debug_setting:
        merged[debug_setting] = True
    if not merged:
        return original
    try:
        settings.set_startup_settings(MOD_SETTINGS_FILE, merged)
    except Exception:
        if original is not None:
            MOD_SETTINGS_FILE.write_bytes(original)
        else:
            try:
                MOD_SETTINGS_FILE.unlink()
            except FileNotFoundError:
                pass
        raise
    return original


def with_debug_setting_enabled(setting_name: str) -> bytes | None:
    return apply_startup_settings({}, setting_name)


def restore_mod_settings(original: bytes | None) -> None:
    if original is None:
        try:
            MOD_SETTINGS_FILE.unlink()
        except FileNotFoundError:
            pass
    else:
        MOD_SETTINGS_FILE.write_bytes(original)
    try:
        MOD_SETTINGS_BACKUP_FILE.unlink()
    except FileNotFoundError:
        pass


def remove_profile() -> None:
    try:
        PROFILE_FILE.unlink()
    except FileNotFoundError:
        pass


def remove_temp_saves() -> None:
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)


def remove_settings_cache() -> None:
    for cache_dir in (TOOLSET_DIR / "__pycache__", TOOLS_DIR / "__pycache__"):
        if not cache_dir.exists():
            continue
        for pattern in ("settings.cpython-*.pyc", "settingsparser.cpython-*.pyc"):
            for cache_file in cache_dir.glob(pattern):
                try:
                    cache_file.unlink()
                except FileNotFoundError:
                    pass


def target_mod_profiles_json() -> Path | None:
    """Return the target mod's local mod-list profile file when it exists."""
    path = MOD_DIR / "tools" / "test" / "modlist-profiles.json"
    return path if path.exists() else None


def resolve_mod_profiles_json(cli_value: Path | None, tool_config: dict[str, object]) -> Path | None:
    """Resolve the mod-list profile source in the harness-defined precedence order."""
    if cli_value is not None:
        return cli_value
    configured = optional_config_path(HARNESS_CONFIG.get("mod_profiles_json"), MOD_DIR)
    if configured is not None:
        return configured
    local_profiles = target_mod_profiles_json()
    if local_profiles is not None:
        return local_profiles
    return modlist.config_path_value(tool_config, "profiles_json", modlist.DEFAULT_PROFILES_JSON)


def profile_source_label(profiles_json: Path | None) -> str:
    """Return a concise display label for the active mod-list profile source."""
    if profiles_json is None:
        return "built-in/toolset default"
    return str(profiles_json)


def factorio_root(factorio_exe: Path) -> Path:
    # Portable Factorio layout: root/bin/x64/factorio.exe
    if factorio_exe.parent.name.lower() == "x64" and factorio_exe.parent.parent.name.lower() == "bin":
        return factorio_exe.parent.parent.parent
    return factorio_exe.parent.parent


def script_output_path(factorio_exe: Path, relative_path: Path) -> Path:
    root = factorio_root(factorio_exe)
    candidates = [
        root / "script-output" / relative_path,
        Path.home() / "AppData" / "Roaming" / "Factorio" / "script-output" / relative_path,
    ]
    for candidate in candidates:
        if candidate.parent.exists():
            return candidate
    return candidates[0]


def report_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, REPORT_RELATIVE)


def data_table_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, DATA_TABLE_RELATIVE)


def material_flow_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, MATERIAL_FLOW_RELATIVE)


def material_flow_state_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, MATERIAL_FLOW_STATE_RELATIVE)


def production_flow_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, PRODUCTION_FLOW_RELATIVE)


def production_flow_state_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, PRODUCTION_FLOW_STATE_RELATIVE)


def technology_flow_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, TECHNOLOGY_FLOW_RELATIVE)


def technology_flow_state_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, TECHNOLOGY_FLOW_STATE_RELATIVE)


def ancestry_runtime_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, ANCESTRY_RUNTIME_RELATIVE)


def recipe_forms_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, RECIPE_FORMS_RELATIVE)


def icon_assets_path(factorio_exe: Path) -> Path:
    return script_output_path(factorio_exe, ICON_ASSETS_RELATIVE)


def normalize_archive_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def collect_icon_sources(value: object, by_mod: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
    if by_mod is None:
        by_mod = {}
    if isinstance(value, dict):
        source = value.get("source")
        if isinstance(source, dict) and isinstance(source.get("mod"), str) and isinstance(source.get("inner_path"), str):
            by_mod.setdefault(source["mod"], set()).add(normalize_archive_path(source["inner_path"]))
        for child in value.values():
            collect_icon_sources(child, by_mod)
    elif isinstance(value, list):
        for child in value:
            collect_icon_sources(child, by_mod)
    return by_mod


def builtin_data_mod_path(root: Path, mod_name: str) -> Path | None:
    if mod_name in {"core", "base", "recycler", "quality", "space-age", "elevated-rails"}:
        path = root / "data" / mod_name
        if path.exists():
            return path
    return None


def local_mod_asset_path(mod_name: str, version: str | None) -> tuple[str, Path] | None:
    candidates: list[tuple[str, Path]] = [
        ("directory", MODS_DIR / mod_name),
    ]
    if version:
        candidates.append(("directory", MODS_DIR / f"{mod_name}_{version}"))
    candidates.extend(("directory", path) for path in sorted(MODS_DIR.glob(f"{mod_name}_*")) if path.is_dir())
    if version:
        candidates.append(("zip", MODS_DIR / f"{mod_name}_{version}.zip"))
    candidates.extend(("zip", path) for path in sorted(MODS_DIR.glob(f"{mod_name}_*.zip")) if path.is_file())
    for kind, path in candidates:
        if path.exists():
            return kind, path
    return None


def common_zip_root(names: list[str]) -> str:
    root = ""
    for name in names:
        normalized = normalize_archive_path(name)
        parts = normalized.split("/", 1)
        if len(parts) < 2:
            return ""
        if not root:
            root = parts[0]
        elif root != parts[0]:
            return ""
    return root


def extract_zip_icon_assets(zip_path: Path, mod_name: str, inner_paths: set[str], output_root: Path) -> Path | None:
    if not inner_paths:
        return None
    target_root = output_root / mod_name
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        root = common_zip_root(names)
        available = {normalize_archive_path(name): name for name in names}
        for inner_path in sorted(inner_paths):
            candidates = [inner_path]
            if root:
                candidates.insert(0, f"{root}/{inner_path}")
            member = next((available[candidate] for candidate in candidates if candidate in available), None)
            if member is None:
                continue
            target_path = target_root / Path(inner_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return target_root if target_root.exists() else None


def enrich_material_flow_metadata(factorio_exe: Path, flow_path: Path, state_path: Path | None = None) -> None:
    if not flow_path.exists():
        return
    try:
        data = json.loads(flow_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return

    root = factorio_root(factorio_exe)
    icon_output = icon_assets_path(factorio_exe)
    active_mods = data.get("active_mods")
    if not isinstance(active_mods, dict):
        active_mods = {}

    icon_sources = collect_icon_sources(data)
    asset_roots: dict[str, dict[str, str]] = {}
    for mod_name, version in active_mods.items():
        mod_name = str(mod_name)
        version = str(version) if version is not None else None
        builtin_path = builtin_data_mod_path(root, mod_name)
        if builtin_path is not None:
            asset_roots[mod_name] = {"type": "directory", "path": str(builtin_path)}
            continue
        local_path = local_mod_asset_path(mod_name, version)
        if local_path is not None:
            kind, path = local_path
            if kind == "zip":
                extracted = extract_zip_icon_assets(path, mod_name, icon_sources.get(mod_name, set()), icon_output)
                if extracted is not None:
                    asset_roots[mod_name] = {"type": "directory", "path": str(extracted), "source_zip": str(path)}
                else:
                    asset_roots[mod_name] = {"type": kind, "path": str(path)}
            else:
                asset_roots[mod_name] = {"type": kind, "path": str(path)}

    data["factorio_root"] = str(root)
    data["mods_dir"] = str(MODS_DIR)
    data["icon_assets_dir"] = str(icon_output)
    data["asset_roots"] = asset_roots
    flow_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    state_path = state_path or material_flow_state_path(factorio_exe)
    state_path.write_text(
        "window.__INGREDIENT_SCRAP_VIEWER_DATA__ = "
        + json.dumps(data, ensure_ascii=False)
        + ";\nwindow.__INGREDIENT_SCRAP_MATERIAL_FLOW__ = window.__INGREDIENT_SCRAP_VIEWER_DATA__;\n",
        encoding="utf-8",
    )


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))


def progress_bar(passed: int, total: int, width: int = 28, color: bool = True) -> str:
    if total <= 0:
        return colored("[no tests]", "yellow", color)
    filled = round((passed / total) * width)
    bar = "#" * filled + "-" * (width - filled)
    bar_color = "green" if passed == total else "red"
    return colored(f"[{bar}]", bar_color, color)


def group_cases(cases: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for case in cases:
        case_id = str(case.get("id", "misc"))
        group = case_id.split(".", 1)[0]
        groups.setdefault(group, []).append(case)
    return groups


def log_color(level: str) -> str:
    level = level.lower()
    if level == "error":
        return "red"
    if level in {"warning", "warn"}:
        return "yellow"
    return "gray"


def print_report_logs(logs: object, color: bool = True, show_passes: bool = False) -> None:
    if isinstance(logs, dict):
        logs = list(logs.values())
    if not isinstance(logs, list):
        return

    visible_logs = [
        entry for entry in logs
        if isinstance(entry, dict)
        if show_passes or str(entry.get("level", "info")).lower() in {"warning", "warn", "error"}
    ]
    if not visible_logs:
        return

    print()
    print(colored("[logs]", "blue", color))
    for entry in visible_logs:
        level = str(entry.get("level", "info")).upper()
        source = entry.get("source", "unknown")
        step = entry.get("step", "unknown")
        description = entry.get("description", "")
        line_color = log_color(level)
        print(f"  {colored(level, line_color, color)} {source}.{step}: {description}")
        details = entry.get("details")
        if details is not None:
            print(colored(f"       details: {compact_json(details)}", "gray", color))


def print_mixed_rounding(report: dict, color: bool = True) -> None:
    active = report.get("active_ancestry")
    rounding = active.get("mixed_rounding") if isinstance(active, dict) else None
    if not isinstance(rounding, dict):
        return

    floor = rounding.get("floor") if isinstance(rounding.get("floor"), dict) else {}
    ceil = rounding.get("ceil") if isinstance(rounding.get("ceil"), dict) else {}
    count = int(floor.get("count", ceil.get("count", 0)) or 0)
    if count <= 0:
        return

    def value(bucket: dict, key: str) -> float:
        return float(bucket.get(key, 0) or 0)

    rows = (
        ("min", value(floor, "min"), value(ceil, "min")),
        ("avg", value(floor, "avg"), value(ceil, "avg")),
        ("max", value(floor, "max"), value(ceil, "max")),
        ("total", value(floor, "total"), value(ceil, "total")),
        ("count", value(floor, "count"), value(ceil, "count")),
    )

    print()
    print(colored("[mixed scrap rounding]", "blue", color))
    print(f"{'':>8} {'floor':>8} {'ceil':>8}")
    for label, floor_value, ceil_value in rows:
        if label == "avg":
            print(f"{label:>8} {floor_value:>8.2f} {ceil_value:>8.2f}")
        else:
            print(f"{label:>8} {floor_value:>8.0f} {ceil_value:>8.0f}")


def print_mixed_recycle_distribution(report: dict, color: bool = True) -> None:
    active = report.get("active_ancestry")
    distribution = active.get("mixed_recycle_distribution") if isinstance(active, dict) else None
    if not isinstance(distribution, dict):
        return

    target_count = int(distribution.get("target_count", 0) or 0)
    if target_count <= 0:
        return

    total_probability = float(distribution.get("total_probability", 0) or 0)
    max_probability = float(distribution.get("max_probability", 0) or 0)
    min_probability = float(distribution.get("min_probability", 0) or 0)
    print()
    print(colored("[mixed recycle distribution]", "blue", color))
    print(
        f"targets={target_count} "
        f"total={total_probability:.2%} "
        f"top={max_probability:.2%} "
        f"min={min_probability:.2%}"
    )
    print(f"{'rank':>4} {'prob':>8} {'weight':>10}  target")
    for entry in distribution.get("top_targets", [])[:10]:
        if not isinstance(entry, dict):
            continue
        rank = int(entry.get("rank", 0) or 0)
        probability = float(entry.get("probability", 0) or 0)
        weight = float(entry.get("expected_weight", 0) or 0)
        print(f"{rank:>4} {probability:>7.2%} {weight:>10.2f}  {entry.get('name', '<unknown>')}")


def print_pretty_report(report: dict, color: bool = True, show_passes: bool = False) -> None:
    summary = report.get("summary", {})
    total = int(summary.get("total", 0) or 0)
    passed = int(summary.get("passed", 0) or 0)
    failed = int(summary.get("failed", 0) or 0)
    status = report.get("status")
    profile = report.get("profile", "unknown")

    title = f"Report: {profile}"
    print()
    print(colored("=" * 72, "cyan", color))
    print(colored(title, "bold", color))
    print(colored("=" * 72, "cyan", color))
    print(f"Status:  {status_label(status, color)}")
    print(f"Mod:     {report.get('mod', 'unknown')}")
    if report.get("compat"):
        print(f"Compat:  {report.get('compat_label', report.get('compat'))}")
    if report.get("setting_profile"):
        print(f"Settings:{report.get('setting_profile'):>9}")
    print(f"Schema:  {report.get('schema', 'unknown')}")
    print(f"Factorio:{report.get('factorio_version', 'unknown'):>9}")
    print(f"Summary: {progress_bar(passed, total, color=color)} {passed}/{total} passed, {failed} failed")

    cases = report.get("cases", [])
    logs = report.get("logs", [])
    is_ingredient_scrap_report = report.get("schema") == "ingredient-scrap-test-report/v1"
    if failed == 0 and not show_passes:
        print(colored("All assertions passed. Use --show-passes to print every case.", "green", color))
        if is_ingredient_scrap_report:
            print_mixed_rounding(report, color=color)
            print_mixed_recycle_distribution(report, color=color)
        print_report_logs(logs, color=color, show_passes=show_passes)
        return

    print()
    for group, group_cases_list in sorted(group_cases(cases).items()):
        visible_cases = [case for case in group_cases_list if show_passes or case.get("status") != "pass"]
        if not visible_cases:
            continue
        print(colored(f"[{group}]", "blue", color))
        for case in visible_cases:
            case_status = case.get("status")
            case_color = "green" if case_status == "pass" else "red"
            print(f"  {colored(status_label(case_status, color=False), case_color, color)} {case.get('id')}: {case.get('name')}")
            message = case.get("message")
            if message and message != "ok":
                print(colored(f"       {message}", "yellow", color))
            details = case.get("details")
            if details is not None and (show_passes or case_status != "pass"):
                print(colored(f"       details: {compact_json(details)}", "gray", color))
    if is_ingredient_scrap_report:
        print_mixed_rounding(report, color=color)
        print_mixed_recycle_distribution(report, color=color)
    print_report_logs(logs, color=color, show_passes=show_passes)


def factorio_diagnostic_args(verbose: bool = False, check_unused_prototype_data: bool = False) -> list[str]:
    args = []
    if verbose:
        args.append("--verbose")
    if check_unused_prototype_data:
        args.append("--check-unused-prototype-data")
    return args


def prototype_warning_lines(output: str) -> list[str]:
    warning_markers = (
        "not accessed",
        "unknown key",
    )
    lines = []
    for line in output.splitlines():
        lowered = line.lower()
        if "finished checking unused prototype data" in lowered:
            continue
        if any(marker in lowered for marker in warning_markers):
            lines.append(line)
    return lines


def run_factorio_profile(
    factorio_exe: Path,
    profile_name: str,
    settings: dict[str, object],
    artifacts: list[str] | None = None,
    extra_factorio_args: list[str] | None = None,
    strict_prototype_warnings: bool = False,
) -> tuple[bool, dict | None]:
    TMP_DIR.mkdir(exist_ok=True)
    save_path = TMP_DIR / f"{HARNESS_MOD_NAME}-{profile_name}.zip"
    output_path = report_path(factorio_exe)
    configured_artifacts = list(artifacts or HARNESS_ARTIFACTS)
    if "test-report.json" not in configured_artifacts and str(REPORT_RELATIVE) not in configured_artifacts:
        configured_artifacts.insert(0, "test-report.json")
    artifact_paths = {
        artifact: script_output_path(factorio_exe, artifact_relative_path(artifact))
        for artifact in configured_artifacts
    }

    for path in {save_path, *artifact_paths.values()}:
        if path.exists():
            path.unlink()

    write_profile(profile_name, settings)
    start_time = time.time()

    command = [
        str(factorio_exe),
        "--mod-directory", str(MODS_DIR),
        *(extra_factorio_args or []),
        "--create", str(save_path),
        "--disable-audio",
    ]

    print(f"\n=== {profile_name} ===")
    print(f"Factorio: {factorio_exe}")
    print(f"Report:   {output_path}")
    if len(artifact_paths) > 1:
        print("Artifacts:")
        for artifact, path in artifact_paths.items():
            if path != output_path:
                print(f"  {artifact}: {path}")

    try:
        proc = subprocess.run(command, timeout=TIMEOUT, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        print(f"FEHLER: Factorio hat nach {TIMEOUT}s nicht beendet.")
        return False, None

    elapsed = time.time() - start_time
    print(f"Factorio exit code {proc.returncode} nach {elapsed:.1f}s")

    diagnostic_warnings = prototype_warning_lines((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if diagnostic_warnings:
        print(f"Prototype diagnostics: {len(diagnostic_warnings)} warning line(s)")
        for line in diagnostic_warnings[:12]:
            print(f"  {line}")
        if len(diagnostic_warnings) > 12:
            print(f"  ... {len(diagnostic_warnings) - 12} more")
        if strict_prototype_warnings:
            print("FEHLER: Prototype diagnostics found and --strict-prototype-warnings is active.")
            return False, None

    if not output_path.exists():
        print("FEHLER: Kein JSON-Report erzeugt.")
        if proc.stdout:
            print("--- stdout tail ---")
            print(proc.stdout[-2000:])
        if proc.stderr:
            print("--- stderr tail ---")
            print(proc.stderr[-2000:])
        return False, None

    try:
        report = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FEHLER: JSON-Report ist ungueltig: {exc}")
        return False, None

    summary = report.get("summary", {})
    status = report.get("status")
    print(f"Status: {status} | {summary.get('passed', 0)}/{summary.get('total', 0)} bestanden")

    for artifact, path in artifact_paths.items():
        if path == output_path:
            continue
        if not path.exists():
            print(f"WARNUNG: {artifact} wurde nicht erzeugt.")
            continue
        if artifact.endswith("material-flow.json"):
            enrich_material_flow_metadata(factorio_exe, path, material_flow_state_path(factorio_exe))
            print(f"Material flow:   {path}")
        elif artifact.endswith("production-flow.json"):
            enrich_material_flow_metadata(factorio_exe, path, production_flow_state_path(factorio_exe))
            print(f"Production flow: {path}")
        elif artifact.endswith("technology-flow.json"):
            enrich_material_flow_metadata(factorio_exe, path, technology_flow_state_path(factorio_exe))
            print(f"Technology flow: {path}")
        else:
            print(f"{artifact}: {path}")
    return status == "pass", report


def compat_label(compat_name: str | None, profiles: dict[str, dict[str, object]] | None = None) -> str:
    if compat_name is None:
        return "none"
    return modlist.profile_label(compat_name, profiles)


def all_mod_profile_names(
    mod_profiles_json: Path | None,
    mod_profiles: dict[str, dict[str, object]],
    explicit_mod_profile: str | None,
) -> list[str | None]:
    """Return the mod-list profiles used for --all."""
    if explicit_mod_profile is not None:
        return [explicit_mod_profile]
    groups = modlist.load_profile_groups(mod_profiles_json)
    if "all" not in groups:
        return [DEFAULT_TEST_MOD_PROFILE or None]
    return modlist.validate_profile_group("all", groups, mod_profiles)


def all_setting_profile_names(explicit_setting_profile: str | None, use_all_setting_profiles: bool) -> list[str]:
    """Return startup setting profiles used for this run."""
    if explicit_setting_profile is not None:
        return [explicit_setting_profile]
    if not use_all_setting_profiles:
        return ["default"]
    if "all" in SETTING_PROFILE_GROUPS:
        return list(SETTING_PROFILE_GROUPS["all"])
    return list(SETTING_PROFILES)


def validate_setting_profiles(selected: list[str]) -> list[str]:
    """Return unknown startup setting profile names."""
    return [name for name in selected if name not in SETTING_PROFILES]


def run_label(mod_profile: str | None, setting_profile: str, test_profile: str) -> str:
    """Return the report summary key for one matrix cell."""
    mod_label = mod_profile or "unchanged"
    return f"{mod_label}/{setting_profile}/{test_profile}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Factorio mod integration tests")
    parser.add_argument("--mod-root", type=Path, help="target mod root containing info.json and tools/test/*.lua")
    parser.add_argument("--factorio", type=Path, help="Factorio executable path; defaults to tools/toolset/tool-ui.json or the local portable install")
    parser.add_argument("--profile", default="default", help="test profile from tools/test/harness.json")
    parser.add_argument("--all", action="store_true", help="run all configured profiles")
    parser.add_argument("--compat", help="legacy alias for --mod-profile")
    parser.add_argument("--mod-profile", help="Factorio mod-list profile to enable")
    parser.add_argument("--mod-profiles-json", type=Path, help="optional JSON file with additional mod-list profiles; defaults to modlist.py config")
    parser.add_argument("--list-mod-profiles", action="store_true", help="print known mod-list profiles and exit")
    parser.add_argument("--keep-mod-list", action="store_true", help="leave the selected mod profile enabled after the run")
    parser.add_argument("--setting-profile", help="startup setting profile from tools/test/harness.json")
    parser.add_argument("--all-setting-profiles", action="store_true", help="run selected Lua profiles against all configured startup setting profiles")
    parser.add_argument("--list-setting-profiles", action="store_true", help="print known startup setting profiles and groups and exit")
    parser.add_argument("--settings-mod", help="label passed through to the settings tool terminology")
    parser.add_argument("--debug-setting", help="startup setting to force to true while tests run")
    parser.add_argument("--report-relative", help="script-output relative report path, e.g. Some_Mod/test-report.json")
    parser.add_argument("--artifact", action="append", help="expected artifact name or script-output relative path; repeatable")
    parser.add_argument("--factorio-verbose", action="store_true", help="pass --verbose to Factorio")
    parser.add_argument("--check-unused-prototype-data", action="store_true", help="pass --check-unused-prototype-data to Factorio")
    parser.add_argument("--strict-prototype-warnings", action="store_true", help="fail when Factorio prototype diagnostics are printed")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument("--show-passes", action="store_true", help="print every passing assertion in the final report")
    parser.add_argument("--keep-saves", action="store_true", help="keep temporary Factorio saves under tools/test/tmp")
    args = parser.parse_args()

    if args.mod_root:
        configure_mod_root(args.mod_root)
    if args.report_relative:
        set_output_relative_paths(path_value(args.report_relative, REPORT_RELATIVE), HARNESS_MOD_NAME)

    tool_config = modlist.load_tool_config()
    factorio_exe = args.factorio or modlist.config_path_value(tool_config, "factorio", DEFAULT_FACTORIO)
    mod_profiles_json = resolve_mod_profiles_json(args.mod_profiles_json, tool_config)
    mod_profiles = modlist.load_profiles(mod_profiles_json)
    profile_groups = modlist.load_profile_groups(mod_profiles_json)

    if args.list_mod_profiles:
        print(f"Profiles: {profile_source_label(mod_profiles_json)}")
        for name in sorted(mod_profiles):
            print(f"{name}: {modlist.profile_label(name, mod_profiles)}")
        return 0

    if args.list_setting_profiles:
        print("Setting profiles:")
        for name in sorted(SETTING_PROFILES):
            print(f"{name}: {len(SETTING_PROFILES[name])} startup setting(s)")
        if SETTING_PROFILE_GROUPS:
            print("Setting profile groups:")
            for name in sorted(SETTING_PROFILE_GROUPS):
                print(f"{name}: {', '.join(SETTING_PROFILE_GROUPS[name])}")
        return 0

    if factorio_exe is None:
        print("FEHLER: Kein Factorio-Pfad gesetzt.")
        return 2
    if not factorio_exe.exists():
        print(f"FEHLER: Factorio nicht gefunden: {factorio_exe}")
        return 2

    print(f"Profiles: {profile_source_label(mod_profiles_json)}")

    selected = list(PROFILES) if args.all else [args.profile]
    unknown_profiles = [profile_name for profile_name in selected if profile_name not in PROFILES]
    if unknown_profiles:
        print(f"FEHLER: Unbekannte Testprofil(e): {', '.join(unknown_profiles)}")
        print("Verfuegbar: " + ", ".join(sorted(PROFILES)))
        return 2

    explicit_mod_profile = args.mod_profile or args.compat
    mod_profile = explicit_mod_profile or DEFAULT_TEST_MOD_PROFILE or None
    if args.all and "all" in profile_groups and explicit_mod_profile is None:
        try:
            selected_mod_profiles = all_mod_profile_names(mod_profiles_json, mod_profiles, None)
        except KeyError as exc:
            print(f"FEHLER: {exc.args[0]}")
            print("Verfuegbare Mod-Profile: " + ", ".join(sorted(mod_profiles)))
            if mod_profiles_json is not None:
                print(f"Profilquelle: {mod_profiles_json}")
            return 2
    else:
        selected_mod_profiles = [mod_profile]

    missing_mod_profiles = [name for name in selected_mod_profiles if name is not None and name not in mod_profiles]
    if missing_mod_profiles:
        print(f"FEHLER: Unbekannte Mod-Profil(e): {', '.join(missing_mod_profiles)}")
        print("Verfuegbar: " + ", ".join(sorted(mod_profiles)))
        if mod_profiles_json is not None:
            print(f"Profilquelle: {mod_profiles_json}")
        return 2

    selected_setting_profiles = all_setting_profile_names(args.setting_profile, args.all_setting_profiles)
    missing_setting_profiles = validate_setting_profiles(selected_setting_profiles)
    if missing_setting_profiles:
        print(f"FEHLER: Unbekannte Setting-Profil(e): {', '.join(missing_setting_profiles)}")
        print("Verfuegbar: " + ", ".join(sorted(SETTING_PROFILES)))
        return 2
    extra_factorio_args = factorio_diagnostic_args(
        verbose=args.factorio_verbose,
        check_unused_prototype_data=args.check_unused_prototype_data,
    )
    failed = []
    reports: list[dict] = []
    artifacts = args.artifact if args.artifact else HARNESS_ARTIFACTS
    settings_mod = args.settings_mod or DEFAULT_SETTINGS_MOD
    debug_setting = args.debug_setting if args.debug_setting is not None else DEFAULT_DEBUG_SETTING
    mod_list_file = modlist.default_mod_list_file(factorio_exe)
    original_mod_list = mod_list_file.read_bytes() if mod_list_file.exists() else None

    try:
        print(f"Debug setting: {settings_mod}.{debug_setting}=true" if debug_setting else "Debug setting: none")
        for current_mod_profile in selected_mod_profiles:
            if current_mod_profile is not None:
                mod_profile_result = modlist.apply_profile(factorio_exe, current_mod_profile, mod_profiles_json)
                print(f"Mod profile: {mod_profile_result['label']}")
            else:
                print("Mod profile: unchanged")
            for setting_profile_name in selected_setting_profiles:
                setting_values = SETTING_PROFILES[setting_profile_name]
                print(f"Setting profile: {setting_profile_name}")
                for profile_name in selected:
                    print(f"Lua profile: {profile_name}")
                    original_mod_settings = None
                    mod_settings_applied = False
                    try:
                        original_mod_settings = apply_startup_settings(setting_values, debug_setting)
                        mod_settings_applied = True
                        ok, report = run_factorio_profile(
                            factorio_exe,
                            profile_name,
                            PROFILES[profile_name],
                            artifacts=artifacts,
                            extra_factorio_args=extra_factorio_args,
                            strict_prototype_warnings=args.strict_prototype_warnings,
                        )
                    finally:
                        if mod_settings_applied:
                            restore_mod_settings(original_mod_settings)
                    if report is not None:
                        if current_mod_profile is not None:
                            report["compat"] = current_mod_profile
                            report["compat_label"] = compat_label(current_mod_profile, mod_profiles)
                        report["setting_profile"] = setting_profile_name
                        reports.append(report)
                    if not ok:
                        failed.append(run_label(current_mod_profile, setting_profile_name, profile_name))
    finally:
        if not args.keep_mod_list:
            if original_mod_list is not None:
                mod_list_file.write_bytes(original_mod_list)
            elif mod_list_file.exists():
                mod_list_file.unlink()
        remove_profile()
        remove_settings_cache()
        if not args.keep_saves:
            remove_temp_saves()

    print("\n=== Zusammenfassung ===")
    if failed:
        print("Fehlgeschlagen: " + ", ".join(failed))
    else:
        total_runs = len(selected) * len(selected_mod_profiles) * len(selected_setting_profiles)
        print(f"Alle {total_runs} Profil(e) bestanden.")

    if reports:
        print("\n=== JSON Reports ===")
        for report in reports:
            print_pretty_report(report, color=not args.no_color, show_passes=args.show_passes)

    if args.keep_saves:
        print(f"\nTemp saves kept: {TMP_DIR}")
    else:
        print("\nTemp saves removed.")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

