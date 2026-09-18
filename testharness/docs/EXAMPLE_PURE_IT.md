# Example: Small Mod Without Extra Artifacts

A small mod can use the same Python harness without copying it and without producing Ingredient-Scrap debug dumps.

Example `tools/test/harness.json`:

```json
{
  "mod_name": "bery0zas-pure-it-updated",
  "debug_setting": "bery0zas-pure-it-test-mode",
  "report_path": "bery0zas-pure-it-updated/test-report.json",
  "mod_profile": "pure_it",
  "artifacts": [
    "test-report.json"
  ],
  "setting_profiles": {
    "default": {},
    "high_pollution_startup": {
      "example-startup-setting": true
    }
  },
  "setting_profile_groups": {
    "all": ["default", "high_pollution_startup"]
  },
  "profiles": {
    "default": {},
    "high_pollution": {
      "amountofcollectedpollution": 100
    }
  }
}
```

Example `tools/test/modlist-profiles.json`:

```json
{
  "profiles": {
    "pure_it": {
      "label": "Pure It",
      "mods": ["bery0zas-pure-it-updated"]
    },
    "pure_it_dlc": {
      "label": "Pure It + DLC",
      "mods": ["bery0zas-pure-it-updated", "elevated-rails", "quality", "space-age"]
    },
    "pure_it_local_all": {
      "label": "Pure It + All Local Mods (diagnostic)",
      "mods": ["bery0zas-pure-it-updated", "example-other-mod"]
    }
  },
  "profile_groups": {
    "all": ["pure_it", "pure_it_dlc"]
  }
}
```

The short command below works because the profile file is in the portable default location `tools/test/modlist-profiles.json`. If you store the profile file elsewhere, either add `mod_profiles_json` to `harness.json` or pass `--mod-profiles-json` on the CLI.

Do not put unstable diagnostic profiles such as `pure_it_local_all` into `profile_groups.all`; they may fail because of unrelated third-party mod conflicts.

The runtime writer should write only this file:

```text
script-output/bery0zas-pure-it-updated/test-report.json
```

No mirror to `script-output/Ingredient_Scrap/test-report.json` is needed.

Run with config-discovered profiles:

```powershell
python F:\Games\Factorio_ModTest\mods\Toolsets\testharness\run_tests.py --mod-root F:\Games\Factorio_ModTest\mods\bery0zas-pure-it-updated --profile default --no-color
```

Run the compatible matrix:

```powershell
python F:\Games\Factorio_ModTest\mods\Toolsets\testharness\run_tests.py --mod-root F:\Games\Factorio_ModTest\mods\bery0zas-pure-it-updated --all --no-color
```

Run all compatible mod profiles, startup setting profiles, and Lua test profiles:

```powershell
python F:\Games\Factorio_ModTest\mods\Toolsets\testharness\run_tests.py --mod-root F:\Games\Factorio_ModTest\mods\bery0zas-pure-it-updated --all --all-setting-profiles --no-color
```
Or run with an explicit profile file:

```powershell
python F:\Games\Factorio_ModTest\mods\Toolsets\testharness\run_tests.py --mod-root F:\Games\Factorio_ModTest\mods\bery0zas-pure-it-updated --mod-profiles-json F:\Games\Factorio_ModTest\mods\bery0zas-pure-it-updated\tools\test\modlist-profiles.json --profile default --no-color
```

