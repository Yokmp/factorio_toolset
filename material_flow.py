"""
Generate material-flow.json by launching Factorio once.

This is the small dump runner for the Material Flow viewer. It intentionally does
not evaluate the Ingredient Scrap assertions; use tools/test/run_tests.py for the
full test harness.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
TOOLS_ROOT = TOOL_DIR.parent
TEST_DIR = TOOLS_ROOT / "test"
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


def mod_profiles_json_from_config(value: Path | None) -> Path:
    if value is not None:
        return value
    tool_config = modlist.load_tool_config()
    return modlist.config_path_value(tool_config, "profiles_json", modlist.DEFAULT_PROFILES_JSON)


def create_dump(
    factorio_exe: Path,
    dump_profile: str,
    extra_factorio_args: list[str],
) -> int:
    profile_settings = run_tests.PROFILES.get(dump_profile)
    if profile_settings is None:
        raise ValueError(f"Unknown dump profile: {dump_profile}")

    run_tests.TMP_DIR.mkdir(exist_ok=True)
    save_path = run_tests.TMP_DIR / f"material-flow-{dump_profile}.zip"
    flow_path = run_tests.material_flow_path(factorio_exe)
    data_table_path = run_tests.data_table_path(factorio_exe)
    report_path = run_tests.report_path(factorio_exe)

    for path in (save_path, flow_path, data_table_path, report_path):
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
    print(f"Flow:     {flow_path}")

    start = time.time()
    proc = subprocess.run(cmd, cwd=factorio_exe.parent, text=True, capture_output=True)
    elapsed = time.time() - start
    print(f"Factorio exit code {proc.returncode} after {elapsed:.1f}s")

    if proc.returncode != 0:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        return proc.returncode

    if not flow_path.exists():
        print("ERROR: material-flow.json was not generated.")
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        return 1

    run_tests.enrich_material_flow_metadata(factorio_exe, flow_path)
    print(f"Material flow: {flow_path}")
    print(f"State script:  {run_tests.material_flow_state_path(factorio_exe)}")
    print(f"Icon assets:   {run_tests.icon_assets_path(factorio_exe)}")
    return 0


def viewer_url(factorio_exe: Path) -> str:
    viewer = TOOL_DIR / "json-tree-viewer.html"
    flow_path = run_tests.material_flow_path(factorio_exe)
    state_path = run_tests.material_flow_state_path(factorio_exe)
    root = run_tests.factorio_root(factorio_exe)
    return (
        viewer.resolve().as_uri()
        + "?factorioRoot="
        + quote_url(str(root))
        + "&file="
        + quote_url(str(flow_path))
        + "&state="
        + quote_url(str(state_path))
    )


def quote_url(value: str) -> str:
    from urllib.parse import quote

    return quote(value.replace("\\", "/"), safe="")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate material-flow.json by launching Factorio once")
    parser.add_argument("--factorio", type=Path, help="Factorio executable path")
    parser.add_argument("--mod-profile", default=run_tests.DEFAULT_TEST_MOD_PROFILE, help="mod-list profile to apply before the run")
    parser.add_argument("--mod-profiles-json", type=Path, help="optional mod-list profiles JSON")
    parser.add_argument("--dump-profile", choices=sorted(run_tests.PROFILES), default="default", help="Ingredient Scrap dump settings profile")
    parser.add_argument("--debug-setting", default=run_tests.DEFAULT_DEBUG_SETTING, help="startup setting forced to true for dump generation")
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
    configure_run_tests_paths(factorio_exe)

    mod_profiles_json = mod_profiles_json_from_config(args.mod_profiles_json)
    original_mod_settings = None
    extra_args: list[str] = []
    if args.factorio_verbose:
        extra_args.append("--verbose")
    if args.check_unused_prototype_data:
        extra_args.append("--check-unused-prototype-data")

    try:
        result = modlist.apply_profile(factorio_exe, args.mod_profile, mod_profiles_json)
        print(f"Mod profile: {result['label']}")
        if not args.no_debug_setting:
            print(f"Debug setting: {run_tests.DEFAULT_SETTINGS_MOD}.{args.debug_setting}=true")
            original_mod_settings = run_tests.with_debug_setting_enabled(args.debug_setting)

        status = create_dump(factorio_exe, args.dump_profile, extra_args)
        if status == 0 and args.open_viewer:
            webbrowser.open(viewer_url(factorio_exe))
        return status
    finally:
        if not args.no_debug_setting:
            run_tests.restore_mod_settings(original_mod_settings)
        if not args.keep_mod_list:
            modlist.apply_profile(factorio_exe, run_tests.DEFAULT_TEST_MOD_PROFILE, mod_profiles_json)
        run_tests.remove_profile()
        run_tests.remove_settings_cache()
        if not args.keep_saves:
            run_tests.remove_temp_saves()


if __name__ == "__main__":
    raise SystemExit(main())
