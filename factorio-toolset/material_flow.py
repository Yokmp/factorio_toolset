"""
Generate a configured JSON dump by launching Factorio once.

This is the small dump runner for the JSON viewer. It intentionally does not
evaluate assertions; use tools/test/run_tests.py for the full test harness.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
TOOLS_ROOT = TOOL_DIR.parent
TEST_DIR = TOOLS_ROOT / "test"
if not TEST_DIR.exists():
    TEST_DIR = TOOLS_ROOT / "testharness"
sys.path.insert(0, str(TOOL_DIR))
sys.path.insert(0, str(TEST_DIR))

import modlist
import run_tests


def configure_run_tests_paths(factorio_exe: Path) -> None:
    """Point the Ingredient Scrap runner helpers at the selected Factorio install."""
    mods_dir = modlist.default_mods_dir(factorio_exe)
    run_tests.MODS_DIR = mods_dir
    run_tests.MOD_SETTINGS_FILE = mods_dir / "mod-settings.dat"
    run_tests.MOD_SETTINGS_BACKUP_FILE = mods_dir / "mod-settings.dat.codex-test-backup"


def factorio_from_config(value: Path | None) -> Path | None:
    if value is not None:
        return value
    tool_config = modlist.load_tool_config()
    return modlist.config_path_value(tool_config, "factorio", run_tests.DEFAULT_FACTORIO)


def mod_profiles_json_from_config(value: Path | None) -> Path | None:
    if value is not None:
        return value
    tool_config = modlist.load_tool_config()
    return run_tests.resolve_mod_profiles_json(None, tool_config)


def artifact_path(factorio_exe: Path, artifact: str) -> Path:
    """Return the script-output path for a configured artifact."""
    return run_tests.script_output_path(factorio_exe, run_tests.artifact_relative_path(artifact))


def artifact_state_path(factorio_exe: Path, artifact: str) -> Path | None:
    """Return the viewer state script path for known flow artifacts."""
    if artifact.endswith("material-flow.json"):
        return run_tests.material_flow_state_path(factorio_exe)
    if artifact.endswith("production-flow.json"):
        return run_tests.production_flow_state_path(factorio_exe)
    if artifact.endswith("technology-flow.json"):
        return run_tests.technology_flow_state_path(factorio_exe)
    return None


def default_viewer_artifact() -> str:
    """Choose the best configured artifact to open in the JSON viewer."""
    for artifact in run_tests.HARNESS_ARTIFACTS:
        if Path(artifact).name == "material-flow.json":
            return artifact
    for artifact in run_tests.HARNESS_ARTIFACTS:
        if artifact.endswith(".json"):
            return artifact
    return "test-report.json"


def profile_suffix(profile_name: str | None) -> str | None:
    """Return a filesystem-safe profile suffix, or None when no profile is active."""
    if not profile_name:
        return None
    suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile_name.strip())
    return suffix or None


def profiled_path(path: Path, profile_name: str | None) -> Path:
    """Return a sibling path with the profile name inserted before the suffix."""
    suffix = profile_suffix(profile_name)
    if not suffix:
        return path
    return path.with_name(f"{path.stem}-{suffix}{path.suffix}")


def copy_profiled_dump(path: Path, profile_name: str | None) -> Path | None:
    """Copy a generated dump to a profile-specific sibling file."""
    target = profiled_path(path, profile_name)
    if target == path or not path.exists():
        return None
    shutil.copy2(path, target)
    return target


def create_dump(
    factorio_exe: Path,
    dump_profile: str,
    mod_profile: str | None,
    viewer_artifact: str,
    extra_factorio_args: list[str],
) -> int:
    profile_settings = run_tests.PROFILES.get(dump_profile)
    if profile_settings is None:
        raise ValueError(f"Unknown dump profile: {dump_profile}")

    run_tests.TMP_DIR.mkdir(exist_ok=True)
    save_path = run_tests.TMP_DIR / f"{run_tests.HARNESS_MOD_NAME}-{dump_profile}-dump.zip"
    configured_artifacts = list(dict.fromkeys([*run_tests.HARNESS_ARTIFACTS, viewer_artifact]))
    output_paths = {artifact: artifact_path(factorio_exe, artifact) for artifact in configured_artifacts}
    viewer_path = artifact_path(factorio_exe, viewer_artifact)

    for path in {save_path, *output_paths.values()}:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    run_tests.write_profile(dump_profile, profile_settings)
    cmd = [
        str(factorio_exe),
        "--mod-directory", str(run_tests.MODS_DIR),
        *extra_factorio_args,
        "--create", str(save_path),
        "--disable-audio",
    ]

    print(f"Factorio: {factorio_exe}")
    print(f"Profile:  {dump_profile}")
    print(f"Viewer:   {viewer_path}")

    start = time.time()
    proc = subprocess.run(cmd, cwd=factorio_exe.parent, text=True, capture_output=True)
    elapsed = time.time() - start
    print(f"Factorio exit code {proc.returncode} after {elapsed:.1f}s")

    if proc.returncode != 0:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        return proc.returncode

    if not viewer_path.exists():
        print(f"ERROR: viewer artifact was not generated: {viewer_artifact}")
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        return 1

    for artifact, path in output_paths.items():
        if not path.exists():
            continue
        state_path = artifact_state_path(factorio_exe, artifact)
        if state_path is not None:
            run_tests.enrich_material_flow_metadata(factorio_exe, path, state_path)
        print(f"{artifact}: {path}")

    profiled_viewer_path = copy_profiled_dump(viewer_path, mod_profile)
    viewer_state_path = artifact_state_path(factorio_exe, viewer_artifact)
    profiled_state_path = copy_profiled_dump(viewer_state_path, mod_profile) if viewer_state_path else None
    if profiled_viewer_path:
        print(f"Profile viewer: {profiled_viewer_path}")
    if profiled_state_path:
        print(f"Profile state:  {profiled_state_path}")
    print(f"Icon assets:   {run_tests.icon_assets_path(factorio_exe)}")
    return 0


def viewer_url(factorio_exe: Path, viewer_artifact: str, mod_profile: str | None = None) -> str:
    viewer = TOOL_DIR / "json-tree-viewer.html"
    output_path = profiled_path(artifact_path(factorio_exe, viewer_artifact), mod_profile)
    state_artifact_path = artifact_state_path(factorio_exe, viewer_artifact)
    state_path = profiled_path(state_artifact_path, mod_profile) if state_artifact_path else None
    root = run_tests.factorio_root(factorio_exe)
    url = (
        viewer.resolve().as_uri()
        + "?factorioRoot="
        + quote_url(str(root))
        + "&file="
        + quote_url(str(output_path))
    )
    if state_path is not None:
        url += "&state=" + quote_url(str(state_path))
    return url


def quote_url(value: str) -> str:
    from urllib.parse import quote

    return quote(value.replace("\\", "/"), safe="")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a configured JSON dump by launching Factorio once")
    parser.add_argument("--mod-root", type=Path, help="target mod root containing info.json and tools/test/*.lua")
    parser.add_argument("--factorio", type=Path, help="Factorio executable path")
    parser.add_argument("--mod-profile", help="mod-list profile to apply before the run")
    parser.add_argument("--mod-profiles-json", type=Path, help="optional mod-list profiles JSON")
    parser.add_argument("--dump-profile", default="default", help="configured dump settings profile")
    parser.add_argument("--viewer-artifact", help="configured artifact to open in the JSON viewer")
    parser.add_argument("--debug-setting", help="startup setting forced to true for dump generation")
    parser.add_argument("--no-debug-setting", action="store_true", help="do not edit mod-settings.dat before launch")
    parser.add_argument("--keep-mod-list", action="store_true", help="leave the selected mod profile enabled after the run")
    parser.add_argument("--keep-saves", action="store_true", help="keep temporary Factorio saves under tools/test/tmp")
    parser.add_argument("--open-viewer", action="store_true", help="open json-tree-viewer.html after a successful dump")
    parser.add_argument("--factorio-verbose", action="store_true", help="pass --verbose to Factorio")
    parser.add_argument("--check-unused-prototype-data", action="store_true", help="pass --check-unused-prototype-data to Factorio")
    args = parser.parse_args()

    factorio_exe = factorio_from_config(args.factorio)
    if factorio_exe is None:
        print("ERROR: No Factorio executable configured.")
        return 2
    if not factorio_exe.exists():
        print(f"ERROR: Factorio executable not found: {factorio_exe}")
        return 2
    if args.mod_root:
        run_tests.configure_mod_root(args.mod_root)
    configure_run_tests_paths(factorio_exe)
    if args.dump_profile not in run_tests.PROFILES:
        print(f"ERROR: Unknown dump profile: {args.dump_profile}")
        print("Available: " + ", ".join(sorted(run_tests.PROFILES)))
        return 2
    mod_profile = args.mod_profile or run_tests.DEFAULT_TEST_MOD_PROFILE or None
    debug_setting = args.debug_setting if args.debug_setting is not None else run_tests.DEFAULT_DEBUG_SETTING
    viewer_artifact = args.viewer_artifact or default_viewer_artifact()

    mod_profiles_json = mod_profiles_json_from_config(args.mod_profiles_json)
    mod_list_file = modlist.default_mod_list_file(factorio_exe)
    original_mod_list = mod_list_file.read_text(encoding="utf-8") if mod_list_file.exists() else None
    original_mod_settings = None
    mod_settings_changed = False
    extra_args: list[str] = []
    if args.factorio_verbose:
        extra_args.append("--verbose")
    if args.check_unused_prototype_data:
        extra_args.append("--check-unused-prototype-data")

    try:
        if mod_profile is not None:
            profiles = modlist.load_profiles(mod_profiles_json)
            if mod_profile not in profiles:
                print(f"ERROR: Unknown mod profile: {mod_profile}")
                print("Available: " + ", ".join(sorted(profiles)))
                if mod_profiles_json is not None:
                    print(f"Profiles JSON: {mod_profiles_json}")
                return 2
            result = modlist.apply_profile(factorio_exe, mod_profile, mod_profiles_json)
            print(f"Mod profile: {result['label']}")
        else:
            print("Mod profile: unchanged")
        if not args.no_debug_setting and debug_setting:
            print(f"Debug setting: {run_tests.DEFAULT_SETTINGS_MOD}.{debug_setting}=true")
            original_mod_settings = run_tests.with_debug_setting_enabled(debug_setting)
            mod_settings_changed = True
        elif not args.no_debug_setting:
            print("Debug setting: none")

        status = create_dump(factorio_exe, args.dump_profile, mod_profile, viewer_artifact, extra_args)
        if status == 0 and args.open_viewer:
            webbrowser.open(viewer_url(factorio_exe, viewer_artifact, mod_profile))
        return status
    finally:
        if mod_settings_changed:
            run_tests.restore_mod_settings(original_mod_settings)
        if not args.keep_mod_list:
            if original_mod_list is not None:
                mod_list_file.write_text(original_mod_list, encoding="utf-8")
            elif mod_list_file.exists():
                mod_list_file.unlink()
        run_tests.remove_profile()
        run_tests.remove_settings_cache()
        if not args.keep_saves:
            run_tests.remove_temp_saves()


if __name__ == "__main__":
    raise SystemExit(main())
