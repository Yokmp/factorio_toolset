# Example: Ingredient Scrap

Ingredient Scrap is the large reference integration. Its `tools/test/harness.json` declares many profiles and extra debug artifacts:

```json
{
  "mod_name": "Ingredient_Scrap",
  "settings_mod": "Ingredient_Scrap",
  "debug_setting": "yis-IS_DEBUG",
  "report_path": "Ingredient_Scrap/test-report.json",
  "mod_profile": "ingredient_scrap",
  "artifacts": [
    "test-report.json",
    "data-table.lua",
    "material-flow.json",
    "production-flow.json",
    "technology-flow.json",
    "ancestry-runtime.json",
    "recipe-forms.json"
  ]
}
```

Run from the external Toolsets package:

```powershell
python F:\Games\Factorio_ModTest\mods\Toolsets\testharness\run_tests.py --mod-root F:\Games\Factorio_ModTest\mods\Ingredient_Scrap --profile default --no-color
```

Ingredient-Scrap-specific pretty-printer sections such as mixed-scrap rounding only appear when the report schema is `ingredient-scrap-test-report/v1`.
