# Factorio JSON Dump Viewer Integration

`material_flow.py` launches Factorio once, creates a temporary save, and expects the active mod set to write a configured JSON artifact into Factorio's `script-output` directory.

It is a dump runner, not the full assertion harness. Use `tools/test/run_tests.py` when you want the complete test report.

## Run

From the repository root:

```powershell
python tools\toolset\material_flow.py --mod-root . --mod-profile vanilla_dlc --dump-profile default --open-viewer
```

Useful options:

- `--factorio PATH`: Factorio executable. Defaults to `tool-ui.json` or the local portable test install.
- `--mod-root PATH`: target mod root containing `info.json` and optional `tools/test/harness.json`.
- `--mod-profile NAME`: mod-list profile to apply before starting Factorio.
- `--mod-profiles-json PATH`: optional mod-list profile JSON; otherwise the harness config, target mod local profiles, tool UI config, and Toolset default are checked in that order.
- `--dump-profile NAME`: configured test/dump settings profile, for example `default`.
- `--viewer-artifact NAME`: JSON artifact to open in the viewer. Defaults to configured `material-flow.json`, then the first configured JSON artifact, then `test-report.json`.
- `--debug-setting NAME`: startup setting forced to `true`; defaults to the selected mod's harness config.
- `--no-debug-setting`: do not edit `mod-settings.dat`.
- `--keep-mod-list`: leave the selected mod profile enabled after the run. Without
  this option, the previous `mod-list.json` content is restored exactly after
  the dump.
- `--keep-saves`: keep temporary saves under `tools/test/tmp`.
- `--open-viewer`: open `json-tree-viewer.html` after a successful dump.

After a run, summarize Ingredient Scrap data-stage timing markers from
`factorio-current.log`:

```powershell
python tools\toolset\is_timing.py
```

For Ingredient Scrap, the generated files are:

```text
script-output/Ingredient_Scrap/material-flow.json
script-output/Ingredient_Scrap/material-flow-data.js
script-output/Ingredient_Scrap/production-flow.json
script-output/Ingredient_Scrap/production-flow-data.js
script-output/Ingredient_Scrap/icon-assets/
```

When `--mod-profile NAME` is set, the tool also writes profile-specific copies
next to the latest dumps:

```text
script-output/Ingredient_Scrap/material-flow-NAME.json
script-output/Ingredient_Scrap/material-flow-data-NAME.js
script-output/Ingredient_Scrap/production-flow-NAME.json
script-output/Ingredient_Scrap/production-flow-data-NAME.js
```

The unprofiled names always represent the most recent run. The profiled names
are easier to compare across compat profiles.

`material-flow-data.js` contains the same data wrapped as:

```js
window.__INGREDIENT_SCRAP_MATERIAL_FLOW__ = { ... };
```

This exists because some browsers block direct `file://` JSON reads. The viewer first tries `?file=...material-flow.json` and can fall back to `?state=...material-flow-data.js`.

`production-flow.json` is a neutral graph of recipes plus item/fluid prototype
nodes. It intentionally excludes generated Ingredient Scrap recycling recipes
and removes generated scrap byproducts from displayed recipe results, so it is
useful for reviewing the original mod ecosystem.

It also contains a passive classification catalog:

```json
{
  "classification": {
    "summary": { "production": 120, "smelting_process": 40 },
    "by_class": { "production": ["item/iron-plate"] },
    "nodes": {
      "item/iron-plate": {
        "class": "production",
        "scores": { "production": 12 },
        "reasons": ["consumer:normal-production-use"]
      }
    }
  }
}
```

The same `classification` object is also attached to each prototype node. These
labels are review evidence only; they do not change generated recipes.

## Project Contract

For a project to work with the viewer, it only needs to write a JSON file with this root shape:

```json
{
  "schema": "ingredient-scrap-material-flow/v1",
  "flows": [
    {
      "material": "prefix-example",
      "mode": "solid",
      "recipe": {
        "name": "test-prefix-example-mixed",
        "main_product": "prefix-example-product-mixed",
        "custom": {
          "markers": [
            { "key": "category:crafting", "label": "crafting", "value": "crafting" }
          ]
        }
      },
      "ingredients": [],
      "results": []
    }
  ]
}
```

The viewer also understands the older Ingredient Scrap internal shape:

```json
{
  "material": "iron",
  "input": { "type": "item", "name": "iron-plate", "amount": 1 },
  "source_recipe": {
    "name": "battery",
    "main_product": "battery",
    "custom": {
      "markers": [
        { "key": "category:chemistry", "label": "chemistry", "value": "chemistry" }
      ]
    },
    "ingredients": [],
    "results": []
  },
  "scrap": { "type": "item", "name": "iron-scrap" }
}
```

Recipe entries can provide `custom.markers`. The viewer renders these as small clickable chips below the
recipe tile. Clicking a marker opens an overview of all flow entries with the same marker key, which is
useful for reviewing flags such as `auto_recycle:false`, `allow_decomposition:false`, `hidden`, or a recipe
category.

## Writing From Factorio

Factorio cannot write arbitrary files during the data stage. The usual pattern is:

1. Build or collect the dump data during data/data-updates/data-final-fixes.
2. Store it in `mod-data` or another runtime-visible place.
3. Start a temporary save.
4. In `control.lua`, write the JSON to `script-output`.

Example runtime write:

```lua
script.on_init(function()
  local report = remote.call("your-mod", "material_flow_report")
  game.write_file("Your_Mod/material-flow.json", helpers.table_to_json(report), false)
end)
```

Ingredient Scrap writes to:

```text
script-output/Ingredient_Scrap/material-flow.json
```

Other mods can use their own output folder by declaring `report_path` and `artifacts` in `tools/test/harness.json`.

## Icons

Every ingredient/result can provide an icon. The viewer checks:

```js
entry.icon
entry.prototype.icon
entry.result.prototype.icon
```

A directly visible icon has:

```json
{
  "icon": {
    "url": "data:image/svg+xml,..."
  }
}
```

For Factorio-style icon paths, provide metadata:

```json
{
  "prototype": {
    "icon": {
      "path": "__base__/graphics/icons/iron-plate.png",
      "source": {
        "mod": "base",
        "inner_path": "graphics/icons/iron-plate.png"
      }
    }
  }
}
```

The viewer can resolve this metadata through:

- `asset_roots` in the JSON
- dropped mod ZIP files
- dropped loose image files
- the configured Factorio root path

`material_flow.py` enriches supported flow dumps with `asset_roots` and extracts ZIP icons into the configured script-output `icon-assets/` folder.

## Viewer URLs

Open a JSON file:

```text
json-tree-viewer.html?file=treeview-example.json
```

Open a generated dump with fallback:

```text
json-tree-viewer.html?file=F:/Games/Factorio_ModTest/script-output/Ingredient_Scrap/material-flow.json&state=F:/Games/Factorio_ModTest/script-output/Ingredient_Scrap/material-flow-data.js
```
