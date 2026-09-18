# Factorio Toolsets

Internal Python tools for local Factorio mod development.

This folder is intentionally not a Factorio mod and must not contain an `info.json` at the root. Factorio will ignore it when it lives next to real mods under `Factorio/mods/Toolsets`.

## Layout

- `factorio-toolset/`: UI, mod-list profiles, settings editor, deploy helper, material-flow viewer and offline analyzers.
- `testharness/`: generic launcher for a mod-owned Lua test harness.

## Typical Commands

Run the UI:

```powershell
python F:\Games\Factorio_ModTest\mods\Toolsets\factorio-toolset\ui.py
```

Run a mod's harness directly:

```powershell
python F:\Games\Factorio_ModTest\mods\Toolsets\testharness\run_tests.py --mod-root F:\Games\Factorio_ModTest\mods\Ingredient_Scrap --profile default --no-color
```

The target mod still owns its Lua test files under `tools/test`, because Factorio loads those files from the active mod.
