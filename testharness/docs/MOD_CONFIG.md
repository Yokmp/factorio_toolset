# Harness Config

Place this file in the target mod:

```text
tools/test/harness.json
```

Minimal example:

```json
{
  "mod_name": "some-mod",
  "debug_setting": "some-mod-test-mode",
  "report_path": "some-mod/test-report.json",
  "mod_profiles_json": "tools/test/modlist-profiles.json",
  "mod_profile": "some_mod",
  "artifacts": [
    "test-report.json"
  ],
  "setting_profiles": {
    "default": {},
    "steam_off": {
      "some-startup-setting": false
    }
  },
  "setting_profile_groups": {
    "all": ["default", "steam_off"]
  },
  "profiles": {
    "default": {},
    "high_pollution": {
      "amountofcollectedpollution": 100
    }
  }
}
```

## Fields

`mod_name`: Factorio mod name. If omitted, the harness uses `info.json.name`.

`settings_mod`: Optional display label for debug-setting output. Defaults to `mod_name`.

`debug_setting`: Startup setting forced to `true` before the test run. If omitted, no setting is changed for non-Ingredient-Scrap mods.

`report_path`: Path under `script-output`, for example `some-mod/test-report.json`. If omitted, defaults to `<info.json.name>/test-report.json`.

`mod_profiles_json`: Optional path to a mod-list profile JSON. Relative paths are resolved from the target mod root. You can omit this field when the file is exactly `tools/test/modlist-profiles.json`, because that path is discovered automatically.

`mod_profile`: Default mod-list profile passed to the Toolsets mod-list helper. If omitted for non-Ingredient-Scrap mods, the current `mod-list.json` is left unchanged.

`artifacts`: Files the harness should check after Factorio exits. Use only files your mod actually writes. The test report is always required; extra Ingredient-Scrap dumps are not expected for other mods unless listed here.

`profiles`: Lua test profile names and values written into generated `tools/test/profile.lua`.

`setting_profiles`: Optional startup setting profiles written to `mod-settings.dat` before a Factorio run. The harness restores the previous file after every run. If omitted, `default` is treated as an empty profile.

`setting_profile_groups`: Optional groups of startup setting profiles. `setting_profile_groups.all` is used by `--all-setting-profiles`.

## Mod-List Profile Groups

`profile_groups` belongs in `tools/test/modlist-profiles.json`, not in `harness.json`, because it groups mod-list profiles:

```json
{
  "profiles": {
    "some_mod": { "label": "Some Mod", "mods": ["some-mod"] },
    "some_mod_dlc": { "label": "Some Mod + DLC", "mods": ["some-mod", "quality", "space-age"] }
  },
  "profile_groups": {
    "all": ["some_mod", "some_mod_dlc"]
  }
}
```

When `profile_groups.all` exists, `--all` runs every harness test profile against every compatible mod profile in that group. Keep unstable diagnostic profiles out of `all`.

## CLI Overrides

`--report-relative` overrides `report_path` for one run.

`--artifact` can be repeated to define the expected artifact list from the command line.

`--debug-setting`, `--settings-mod`, `--mod-profile`, and `--mod-profiles-json` override the config values for one run.

`--setting-profile` selects one startup setting profile for one run.

`--all-setting-profiles` combines the selected Lua test profiles with every startup setting profile in `setting_profile_groups.all`, or with all defined setting profiles if that group is missing.

`--list-setting-profiles` prints startup setting profiles and groups.

