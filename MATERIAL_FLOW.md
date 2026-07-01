# Material Flow Dump Integration

`material_flow.py` launches Factorio once, creates a temporary save, and expects the active mod set to write a Material Flow dump into Factorio's `script-output` directory.

It is a dump runner, not the full Ingredient Scrap assertion harness. Use `tools/test/run_tests.py` when you want the complete test report.

## Run

From the repository root:

```powershell
python tools\toolset\material_flow.py --mod-profile vanilla_dlc --dump-profile default --open-viewer
```

Useful options:

- `--factorio PATH`: Factorio executable. Defaults to `tool-ui.json` or the local portable test install.
- `--mod-profile NAME`: mod-list profile to apply before starting Factorio.
- `--dump-profile NAME`: Ingredient Scrap settings profile, for example `default` or `recipe_chain_targets`.
- `--debug-setting NAME`: startup setting forced to `true`; defaults to `yis-IS_DEBUG`.
- `--no-debug-setting`: do not edit `mod-settings.dat`.
- `--keep-mod-list`: leave the selected mod profile enabled after the run.
- `--keep-saves`: keep temporary saves under `tools/test/tmp`.
- `--open-viewer`: open `json-tree-viewer.html` after a successful dump.

The generated files are:

```text
script-output/Ingredient_Scrap/material-flow.json
script-output/Ingredient_Scrap/material-flow-data.js
script-output/Ingredient_Scrap/icon-assets/
```

`material-flow-data.js` contains the same data wrapped as:

```js
window.__INGREDIENT_SCRAP_MATERIAL_FLOW__ = { ... };
```

This exists because some browsers block direct `file://` JSON reads. The viewer first tries `?file=...material-flow.json` and can fall back to `?state=...material-flow-data.js`.

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
        "main_product": "prefix-example-product-mixed"
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
    "ingredients": [],
    "results": []
  },
  "scrap": { "type": "item", "name": "iron-scrap" }
}
```

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

Ingredient Scrap currently writes to:

```text
script-output/Ingredient_Scrap/material-flow.json
```

and the tools expect that path by default.

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

`material_flow.py` enriches Ingredient Scrap dumps with `asset_roots` and extracts ZIP icons into `script-output/Ingredient_Scrap/icon-assets/`.

## Viewer URLs

Open a JSON file:

```text
json-tree-viewer.html?file=treeview-example.json
```

Open a generated dump with fallback:

```text
json-tree-viewer.html?file=F:/Games/Factorio_ModTest/script-output/Ingredient_Scrap/material-flow.json&state=F:/Games/Factorio_ModTest/script-output/Ingredient_Scrap/material-flow-data.js
```
