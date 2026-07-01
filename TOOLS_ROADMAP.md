# Roadmap

Ideas for future versions of the Factorio Toolset.

## Dynamic Tool Tabs

- Add a small `+` button on the right side of the top tab bar.
- Let users register additional tools from arbitrary paths, not only files next to `ui.py`.
- Store user-added tools in `tool-ui.json` or a dedicated tool registry JSON.
- Keep missing or incompatible tools visible as dark, clickable tabs with an explanation dialog.
- Allow user-added tools to be removed again.
- Avoid buttons inside buttons.
- Add one small red `-` button next to the future `+` button.
- The `-` button should open a small themed dialog with a list view of user-added tools.
- Selecting an item and confirming removes that tool from the user tool registry.
- Only user-added tools should appear in the remove dialog, not built-in tools from `TOOL_MAP`.

## Tool Metadata

Each tool entry could describe:

- Display name
- Script path
- Version
- Supported UI compatibility range
- CLI commands for common actions
- Whether the tool can run standalone, needs a profile, or needs Factorio paths

Example shape:

```json
{
  "name": "deploy",
  "title": "Deploy",
  "path": "../deploy.py",
  "version": "1.0.0",
  "commands": {
    "check": ["python", "../deploy.py", "--check"],
    "run": ["python", "../deploy.py", "--deploy"]
  }
}
```

## Generated UI For CLI Tools

- Build a simple generic tab from declared CLI commands.
- Render command buttons from metadata.
- Capture command output in an inset log panel.
- Show success/failure states in the status bar and in a themed popup.
- Allow tools to declare required inputs such as paths, profile names, checkboxes, or dropdown options.

## UI Cleanup

- Reduce duplicate apply actions.
- In the Mod List tab, avoid showing both `Apply Profile` and `Apply Selection` as competing primary actions.
- In the Settings tab, avoid showing `Apply Settings` twice.
- Prefer one clear primary apply action per tab, with secondary actions grouped near the profile list only when they have a distinct meaning.
- Revisit button labels so `Load`, `Preview`, `Apply`, and `Save` describe exactly what changes immediately and what only changes the visible selection.

## Dependency Check

- Read `info.json` from installed mod folders and zipped mods.
- Parse required and optional dependencies.
- When a selected profile enables a mod, resolve its required dependencies.
- If a required dependency is installed but disabled, offer to enable it automatically or mark it as dependency-enabled.
- If a required dependency is missing, show it in the mod list as a red row with only its name.
- Missing dependency rows should not have a checkbox, because they cannot be enabled locally.
- Keep optional dependencies informational unless the user explicitly enables related behavior later.
- Surface dependency problems before applying a profile or launching Factorio.

## Deploy Tool

- Adapt the existing root-level `deploy.py`.
- Add `--version`.
- Add dry-run/check mode.
- Add a UI frame once the CLI contract is stable.
- Keep it in the top bar as a visible but unavailable tab until then.

## Packaging

- Keep the release package small: Python files, README, screenshots, and optional roadmap.
- Do not ship generated user files such as `tool-ui.json` or `modlist-profiles.json`.
- Consider a small zip build command once the file list stabilizes.
