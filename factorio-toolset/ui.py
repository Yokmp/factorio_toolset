"""
Tkinter UI shell for Factorio Toolset.

Run with:
    python tools/toolset/ui.py
    python tools/toolset/modlist.py gui
"""

from __future__ import annotations

import argparse
import ast
import json
import queue
import re
import subprocess
import threading
import tkinter as tk
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import modlist
import settings


def enable_dpi_awareness() -> None:
    """Ask Windows for DPI-aware rendering so Tk text looks less blurry."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


enable_dpi_awareness()


BG = "#2b2a26"
PANEL = "#3c3a34"
PANEL_DARK = "#242320"
PANEL_LIGHT = "#9a9a98"
PANEL_HOVER = "#4a4740"
LIST_BG = "#202020"
LIST_ROW = "#242424"
LIST_LINE = "#333333"
TEXT = "#f0c274"
BODY_TEXT = "#e8e2d0"
BUTTON_TEXT = "#241a08"
MUTED = "#b8b0a3"
ORANGE = "#e08a1e"
ORANGE_HOVER = "#f0a23a"
ORANGE_DARK = "#9b5a15"
GREEN = "#5f9e42"
GREEN_HOVER = "#75b855"
RED = "#c1443b"
RED_HOVER = "#d76158"
ENTRY = "#34322c"
LINE = "#111111"
SETTING_NAME_CHARS = 28
SETTING_NAME_COLUMN = 230
SETTING_NAME_MIN_COLUMN = 140
SETTING_NAME_MAX_COLUMN = 520
APP_VERSION = "1.0.1"
GITHUB_URL = "https://github.com/Yokmp/factorio_toolset"

TOOL_DIR = Path(__file__).resolve().parent


def module_version(filename: str) -> str | None:
    """Read a tool's APP_VERSION without executing its module-level code."""
    path = TOOL_DIR / filename
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    return None


def find_mod_root(start: Path) -> Path | None:
    """Return the nearest parent directory containing a Factorio mod info.json."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "info.json").is_file():
            return candidate
    return None


def default_mod_root() -> Path | None:
    """Return the configured or discoverable target mod root."""
    config = modlist.load_tool_config()
    configured = modlist.config_path_value(config, "mod_root")
    if configured is not None:
        return configured
    return find_mod_root(Path.cwd()) or find_mod_root(TOOL_DIR)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    frame_type: type["ToolFrame"] | None = None
    required_file: Path | None = None
    title_override: str | None = None
    version: str | None = None
    min_version: str | None = None
    max_version: str | None = None

    @property
    def title(self) -> str:
        if self.title_override is not None:
            return self.title_override
        if self.frame_type is not None:
            return self.frame_type.title
        return self.name

    def is_available(self) -> bool:
        return self.compatibility_error() is None

    def compatibility_error(self) -> str | None:
        if self.required_file is not None and not self.required_file.exists():
            return f"{self.required_file.name} is missing"
        if self.frame_type is None:
            return f"{self.name} has no UI frame registered"
        if self.version is None:
            return "version unavailable" if self.min_version is not None or self.max_version is not None else None
        if self.min_version is not None and compare_versions(self.version, self.min_version) < 0:
            return f"{self.name} {self.version} is older than required {self.min_version}"
        if self.max_version is not None and compare_versions(self.version, self.max_version) >= 0:
            return f"{self.name} {self.version} is newer than supported {self.max_version}"
        return None


TOOL_REGISTRY: dict[str, ToolDefinition] = {}
PINNED_MOD_NAMES = [
    "base",
    "elevated-rails",
    "recycler",
    "quality",
    "space-age",
]
PINNED_MOD_ORDER = {name: index for index, name in enumerate(PINNED_MOD_NAMES)}


def version_parts(version: str) -> tuple[int, int, int]:
    """Parse a simple semantic version string into comparable integer parts."""
    parts = []
    for part in version.split(".")[:3]:
        digits = "".join(char for char in part if char.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def compare_versions(left: str, right: str) -> int:
    """Return -1, 0, or 1 for simple semantic version comparison."""
    left_parts = version_parts(left)
    right_parts = version_parts(right)
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def register_tool_from_map(name: str, spec: dict[str, Any]) -> None:
    """Register one top-bar tab from TOOL_MAP metadata."""
    filename = spec.get("filename")
    required_file = TOOL_DIR / filename if isinstance(filename, str) and filename else None
    TOOL_REGISTRY[name] = ToolDefinition(
        name=name,
        frame_type=spec.get("frame_type"),
        required_file=required_file,
        title_override=spec.get("title"),
        version=spec.get("version"),
        min_version=spec.get("min_version"),
        max_version=spec.get("max_version"),
    )


def register_tools_from_map(tool_map: dict[str, dict[str, Any]]) -> None:
    """Register all expected tools; missing files stay visible as disabled tabs."""
    TOOL_REGISTRY.clear()
    for name, spec in tool_map.items():
        register_tool_from_map(name, spec)


def available_tools() -> dict[str, ToolDefinition]:
    """Return only tools whose required file exists next to this UI."""
    return {name: tool for name, tool in TOOL_REGISTRY.items() if tool.is_available()}


def tool_compatibility_report() -> list[str]:
    """Return human-readable compatibility lines for every registered tool."""
    lines = [f"ui.py {APP_VERSION}"]
    for name, tool in TOOL_REGISTRY.items():
        error = tool.compatibility_error()
        version = tool.version or "unknown"
        if error is None:
            lines.append(f"{name}: {version} compatible")
        else:
            lines.append(f"{name}: {version} incompatible - {error}")
    return lines


def compact_text(text: str, max_chars: int) -> str:
    """Shorten long table labels while keeping the full value available elsewhere."""
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


def striped_header(master: tk.Misc, text: str, bg: str = BG) -> tk.Frame:
    """Create a Factorio-like section title with striped filler to the right."""
    frame = tk.Frame(master, bg=bg)
    frame.columnconfigure(1, weight=1)
    tk.Label(frame, text=text, bg=bg, fg=TEXT, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 10))
    stripes = tk.Canvas(frame, height=22, bg=bg, highlightthickness=0)
    stripes.grid(row=0, column=1, sticky="ew")

    def draw(event: tk.Event | None = None) -> None:
        width = stripes.winfo_width() if event is None else event.width
        stripes.delete("stripe")
        for x in range(0, width, 5):
            stripes.create_line(x, 0, x, 22, fill="#3d3d3d", tags="stripe")
            stripes.create_line(x + 1, 0, x + 1, 22, fill="#202020", tags="stripe")

    stripes.bind("<Configure>", draw)
    return frame


def factorio_button(
    master: tk.Misc,
    text: str,
    command: Callable[[], Any],
    *,
    kind: str = "normal",
) -> tk.Button:
    """Create a shared raised button with Factorio-inspired colors and hover state."""
    colors = {
        "normal": ("#9a9a98", "#b8b8b5", "#2a2a2a"),
        "orange": (ORANGE, ORANGE_HOVER, BUTTON_TEXT),
        "green": (GREEN, GREEN_HOVER, "#12240a"),
        "red": (RED, RED_HOVER, "#2a0e0b"),
    }
    base, hover, fg = colors[kind]
    button = tk.Button(
        master,
        text=text,
        command=command,
        bg=base,
        fg=fg,
        activebackground=hover,
        activeforeground=fg,
        relief=tk.RAISED,
        bd=2,
        padx=14,
        pady=7,
        font=("Segoe UI", 10, "bold"),
        cursor="hand2",
        highlightthickness=0,
    )
    button.bind("<Enter>", lambda _event: button.configure(bg=hover))
    button.bind("<Leave>", lambda _event: button.configure(bg=base))
    return button


class FactorioScrollbar(tk.Canvas):
    """Canvas scrollbar used so list/canvas scrollbars match the custom theme."""

    def __init__(self, master: tk.Misc, command: Callable[..., Any], width: int = 15):
        super().__init__(master, width=width, bg=PANEL_DARK, highlightthickness=1, highlightbackground=LINE, bd=0)
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self.drag_start_y: int | None = None
        self.drag_start_first = 0.0
        self.hovering_thumb = False
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self.on_click)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Motion>", self.on_motion)
        self.bind("<Leave>", self.on_leave)

    def set(self, first: float | str, last: float | str) -> None:
        self.first = float(first)
        self.last = float(last)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        self.create_rectangle(0, 0, width, height, fill=PANEL_DARK, outline=LINE)

        for y in range(3, height - 3, 6):
            self.create_line(3, y, width - 4, y, fill="#3d3c37")
            self.create_line(3, y + 1, width - 4, y + 1, fill="#171717")

        top, bottom = self.thumb_bounds()
        fill = ORANGE_HOVER if self.hovering_thumb else ORANGE
        self.create_rectangle(2, top, width - 3, bottom, fill=fill, outline=ORANGE_DARK, tags="thumb")
        for y in range(int(top) + 3, int(bottom) - 2, 4):
            self.create_line(4, y, width - 5, y, fill="#bf741c", tags="thumb")

    def thumb_bounds(self) -> tuple[float, float]:
        height = max(self.winfo_height(), 1)
        visible = max(self.last - self.first, 0.04)
        thumb_height = max(24, height * visible)
        max_top = max(height - thumb_height - 2, 1)
        top = 1 + max_top * self.first
        bottom = min(top + thumb_height, height - 1)
        return top, bottom

    def y_to_fraction(self, y: int) -> float:
        height = max(self.winfo_height(), 1)
        top, bottom = self.thumb_bounds()
        thumb_height = max(bottom - top, 1)
        max_top = max(height - thumb_height - 2, 1)
        return max(0.0, min(1.0, (y - thumb_height / 2) / max_top))

    def on_click(self, event: tk.Event) -> None:
        top, bottom = self.thumb_bounds()
        if top <= event.y <= bottom:
            self.drag_start_y = event.y
            self.drag_start_first = self.first
            return
        self.command("moveto", self.y_to_fraction(event.y))

    def on_drag(self, event: tk.Event) -> None:
        if self.drag_start_y is None:
            return
        height = max(self.winfo_height(), 1)
        top, bottom = self.thumb_bounds()
        thumb_height = max(bottom - top, 1)
        max_top = max(height - thumb_height - 2, 1)
        delta = (event.y - self.drag_start_y) / max_top
        self.command("moveto", max(0.0, min(1.0, self.drag_start_first + delta)))

    def on_release(self, _event: tk.Event) -> None:
        self.drag_start_y = None

    def on_motion(self, event: tk.Event) -> None:
        top, bottom = self.thumb_bounds()
        hovering = top <= event.y <= bottom
        if hovering != self.hovering_thumb:
            self.hovering_thumb = hovering
            self.redraw()

    def on_leave(self, _event: tk.Event) -> None:
        if self.hovering_thumb:
            self.hovering_thumb = False
            self.redraw()


def factorio_scrollbar(master: tk.Misc, command: Callable[..., Any]) -> FactorioScrollbar:
    """Convenience factory matching Tk's Scrollbar(command=...) shape."""
    return FactorioScrollbar(master, command)


def confirm_dialog(parent: tk.Misc, title: str, message: str, action: str, *, danger: bool = False) -> bool:
    """Small themed confirmation dialog for destructive or important actions."""
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=BG)
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()
    dialog.resizable(False, False)
    dialog.columnconfigure(0, weight=1)

    result = tk.BooleanVar(value=False)
    striped_header(dialog, title, BG).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 6))

    body = tk.Frame(dialog, bg=PANEL, highlightthickness=2, highlightbackground=LINE)
    body.grid(row=1, column=0, sticky="ew", padx=10)
    tk.Label(body, text=message, bg=PANEL, fg=BODY_TEXT, justify=tk.LEFT, wraplength=560, padx=12, pady=12).pack(anchor="w")

    actions = tk.Frame(dialog, bg=BG)
    actions.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
    actions.columnconfigure(1, weight=1)

    def close(value: bool) -> None:
        result.set(value)
        dialog.destroy()

    factorio_button(actions, "Back", lambda: close(False)).grid(row=0, column=0, sticky="w")
    factorio_button(actions, action, lambda: close(True), kind="red" if danger else "orange").grid(row=0, column=2, sticky="e")

    dialog.update_idletasks()
    parent_root = parent.winfo_toplevel()
    x = parent_root.winfo_rootx() + (parent_root.winfo_width() - dialog.winfo_width()) // 2
    y = parent_root.winfo_rooty() + (parent_root.winfo_height() - dialog.winfo_height()) // 2
    dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    parent.wait_window(dialog)
    return result.get()


def info_dialog(parent: tk.Misc, title: str, message: str) -> None:
    """Small themed information dialog for unavailable tools or compatibility notes."""
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=BG)
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()
    dialog.resizable(False, False)
    dialog.columnconfigure(0, weight=1)

    striped_header(dialog, title, BG).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 6))
    body = tk.Frame(dialog, bg=PANEL, highlightthickness=2, highlightbackground=LINE)
    body.grid(row=1, column=0, sticky="ew", padx=10)
    tk.Label(body, text=message, bg=PANEL, fg=BODY_TEXT, justify=tk.LEFT, wraplength=560, padx=12, pady=12).pack(anchor="w")

    actions = tk.Frame(dialog, bg=BG)
    actions.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
    actions.columnconfigure(0, weight=1)
    factorio_button(actions, "OK", dialog.destroy, kind="orange").grid(row=0, column=0, sticky="e")

    dialog.update_idletasks()
    parent_root = parent.winfo_toplevel()
    x = parent_root.winfo_rootx() + (parent_root.winfo_width() - dialog.winfo_width()) // 2
    y = parent_root.winfo_rooty() + (parent_root.winfo_height() - dialog.winfo_height()) // 2
    dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    parent.wait_window(dialog)


class ToolFrame(tk.Frame):
    """Base class for every tab content frame in the tool shell."""

    title = "Tool"

    def __init__(self, master: tk.Misc, app: "ToolApp"):
        super().__init__(master, bg=BG)
        self.app = app


class ToolApp(tk.Tk):
    """Main window: builds the tab shell, shared config, status bar, and workers."""

    def __init__(self, factorio_exe: Path | None = None, profiles_json: Path | None = None):
        super().__init__()
        self.tool_config = modlist.load_tool_config()
        factorio_exe = factorio_exe or modlist.config_path_value(self.tool_config, "factorio", modlist.DEFAULT_FACTORIO)
        profiles_json = profiles_json or modlist.config_path_value(self.tool_config, "profiles_json", modlist.DEFAULT_PROFILES_JSON)
        window_config = self.tool_config.get("window") if isinstance(self.tool_config.get("window"), dict) else {}

        self.title(f"Factorio Toolset v{APP_VERSION}")
        geometry = window_config.get("geometry") if isinstance(window_config, dict) else None
        self.geometry(geometry if isinstance(geometry, str) and geometry else "1120x700")
        self.minsize(900, 560)
        self.configure(bg=BG)

        self.factorio_exe = factorio_exe
        self.profiles_json = profiles_json
        self.mod_root = modlist.config_path_value(self.tool_config, "mod_root", default_mod_root())
        self.last_profile = self.tool_config.get("last_profile") if isinstance(self.tool_config.get("last_profile"), str) else ""
        self.all_tools = dict(TOOL_REGISTRY)
        self.tools = available_tools()
        self.current_tool: ToolFrame | None = None
        self.status_var = tk.StringVar(value="Ready")
        self.script_output_var = tk.StringVar(value="script-output: n/a")
        self.tab_buttons: dict[str, tk.Button] = {}
        self.logo_image: tk.PhotoImage | None = None

        self._configure_style()
        self._build_layout()
        if self.tools:
            self.show_tool(next(iter(self.tools)))
        else:
            self.show_no_tools()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", background=BG, foreground=BODY_TEXT, fieldbackground=ENTRY)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=BODY_TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Panel.TLabel", background=PANEL, foreground=BODY_TEXT)
        style.configure("TEntry", fieldbackground=ENTRY, foreground=BODY_TEXT, insertcolor=BODY_TEXT, bordercolor=LINE, lightcolor="#6c6960", darkcolor=LINE)
        style.configure(
            "TCombobox",
            background=ENTRY,
            foreground=BODY_TEXT,
            fieldbackground=ENTRY,
            selectbackground=ORANGE,
            selectforeground=BUTTON_TEXT,
            arrowcolor=BODY_TEXT,
            bordercolor=LINE,
            lightcolor="#6c6960",
            darkcolor=LINE,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", ENTRY), ("active", ENTRY), ("focus", ENTRY)],
            foreground=[("readonly", BODY_TEXT), ("active", BODY_TEXT), ("focus", BODY_TEXT)],
            selectbackground=[("readonly", ORANGE), ("focus", ORANGE)],
            selectforeground=[("readonly", BUTTON_TEXT), ("focus", BUTTON_TEXT)],
            arrowcolor=[("active", ORANGE_HOVER), ("pressed", ORANGE), ("readonly", BODY_TEXT)],
        )
        self.option_add("*TCombobox*Listbox.background", LIST_BG)
        self.option_add("*TCombobox*Listbox.foreground", BODY_TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ORANGE)
        self.option_add("*TCombobox*Listbox.selectForeground", BUTTON_TEXT)
        self.option_add("*TCombobox*Listbox.highlightBackground", LINE)
        self.option_add("*TCombobox*Listbox.highlightColor", ORANGE)

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(2, weight=1)

        logo = self.logo_label(header)
        logo.grid(row=0, column=0, sticky="w")

        tabs = tk.Frame(header, bg=BG)
        tabs.grid(row=0, column=1, sticky="w")
        for tool_name, tool_def in self.all_tools.items():
            version_text = f"v{tool_def.version}" if tool_def.version else "version unknown"
            tool_error = tool_def.compatibility_error()
            button = tk.Button(
                tabs,
                text=f"{tool_def.title}\n{version_text}",
                bg=PANEL_LIGHT if tool_error is None else PANEL_DARK,
                fg=BODY_TEXT if tool_error is None else MUTED,
                activebackground=ORANGE,
                activeforeground=BUTTON_TEXT,
                relief=tk.RAISED,
                bd=3,
                padx=18,
                pady=5,
                font=("Segoe UI", 9, "bold"),
                command=lambda name=tool_name: self.show_tool(name),
            )
            button.pack(side=tk.LEFT)
            self.tab_buttons[tool_name] = button

        striped_header(header, "", BG).grid(row=0, column=2, sticky="ew", padx=12)

        self.content = tk.Frame(self, bg=BG)
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        status_bar = tk.Frame(self, bg=PANEL_DARK, relief=tk.SUNKEN, bd=2, highlightthickness=1, highlightbackground=LINE)
        status_bar.grid(row=2, column=0, sticky="ew", padx=0, pady=(0, 1))
        tk.Label(status_bar, textvariable=self.script_output_var, bg=PANEL_DARK, fg=MUTED, anchor="w", padx=12, pady=4, width=26).pack(side=tk.LEFT)
        tk.Label(status_bar, textvariable=self.status_var, bg=PANEL_DARK, fg=MUTED, anchor="center", padx=12, pady=4).pack(side=tk.LEFT, fill="x", expand=True)
        version_label = tk.Label(status_bar, text=f"v{APP_VERSION}", bg=PANEL_DARK, fg=MUTED, anchor="e", padx=4, pady=4)
        github_label = tk.Label(status_bar, text="GitHub", bg=PANEL_DARK, fg=TEXT, cursor="hand2", anchor="e", padx=12, pady=4, font=("Segoe UI", 9, "underline"))
        github_label.pack(side=tk.RIGHT)
        version_label.pack(side=tk.RIGHT)
        github_label.bind("<Button-1>", lambda _event: webbrowser.open(GITHUB_URL))
        github_label.bind("<Enter>", lambda _event: github_label.configure(fg=ORANGE_HOVER))
        github_label.bind("<Leave>", lambda _event: github_label.configure(fg=TEXT))
        self.refresh_script_output_size()

    def show_tool(self, tool_name: str) -> None:
        """Destroy the current tab frame and mount the selected registered tool."""
        tool_def = self.all_tools[tool_name]
        tool_error = tool_def.compatibility_error()
        if tool_error is not None:
            details = [
                f"{tool_def.title} could not be loaded.",
                "",
                f"Reason: {tool_error}",
            ]
            if tool_def.version is not None or tool_def.min_version is not None or tool_def.max_version is not None:
                details.extend(
                    [
                        "",
                        f"UI version: {APP_VERSION}",
                        f"Tool version: {tool_def.version or 'unknown'}",
                        f"Supported versions: >= {tool_def.min_version or 'any'} and < {tool_def.max_version or 'any'}",
                    ]
                )
            info_dialog(
                self,
                "Tool not loaded",
                "\n".join(details),
            )
            self.status(f"{tool_def.title} not loaded: {tool_error}")
            return
        if self.current_tool is not None:
            self.current_tool.destroy()
        self.current_tool = tool_def.frame_type(self.content, self)
        self.current_tool.grid(row=0, column=0, sticky="nsew")
        for name, button in self.tab_buttons.items():
            button_tool = self.all_tools[name]
            selected = name == tool_name
            compatible = button_tool.compatibility_error() is None
            base_bg = PANEL_LIGHT if compatible else PANEL_DARK
            base_fg = BODY_TEXT if compatible else MUTED
            button.configure(bg=ORANGE if selected else base_bg, fg=BUTTON_TEXT if selected else base_fg)
        self.status(f"{tool_def.title} loaded")

    def show_no_tools(self) -> None:
        message = tk.Label(
            self.content,
            text="No tools available.",
            bg=BG,
            fg=BODY_TEXT,
            font=("Segoe UI", 12, "bold"),
            padx=24,
            pady=24,
        )
        message.grid(row=0, column=0, sticky="nsew")
        self.status("No tools available")

    def status(self, text: str) -> None:
        self.status_var.set(text)

    def script_output_path(self) -> Path:
        """Return the Factorio script-output folder for the configured executable."""
        return modlist.factorio_root(self.factorio_exe or modlist.DEFAULT_FACTORIO) / "script-output"

    def format_bytes(self, size: int) -> str:
        """Format a byte count for the status bar."""
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def folder_size(self, path: Path) -> int:
        """Calculate recursive folder size while ignoring unreadable files."""
        total = 0
        if not path.exists():
            return 0
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    def refresh_script_output_size(self) -> None:
        """Update the left status-bar segment with script-output folder size."""
        try:
            size = self.folder_size(self.script_output_path())
            self.script_output_var.set(f"script-output: {self.format_bytes(size)}")
        except OSError:
            self.script_output_var.set("script-output: n/a")

    def save_tool_config(
        self,
        *,
        factorio: Path | str | None = None,
        profiles_json: Path | str | None = None,
        settings_file: Path | str | None = None,
        mod_root: Path | str | None = None,
        last_profile: str | None = None,
    ) -> None:
        """Persist shared UI config without each tab touching JSON details."""
        window = {"geometry": self.geometry()}
        updates: dict[str, Any] = {"window": window}
        if factorio is not None:
            updates["factorio"] = str(factorio)
            self.factorio_exe = Path(factorio)
        if profiles_json is not None:
            updates["profiles_json"] = str(profiles_json)
        if settings_file is not None:
            updates["settings_file"] = str(settings_file)
        if mod_root is not None:
            updates["mod_root"] = str(mod_root)
            self.mod_root = Path(mod_root)
        if last_profile is not None:
            updates["last_profile"] = last_profile
            self.last_profile = last_profile
        self.tool_config = modlist.update_tool_config(**updates)
        self.refresh_script_output_size()

    def on_close(self) -> None:
        factorio: Path | str | None = self.factorio_exe
        profiles_json: Path | str | None = self.profiles_json
        if isinstance(self.current_tool, ModListTool):
            factorio = self.current_tool.factorio_path()
            profiles_json = self.current_tool.profiles_json_path()
            self.save_tool_config(factorio=factorio, profiles_json=profiles_json)
        elif isinstance(self.current_tool, SettingsTool):
            self.save_tool_config(profiles_json=self.current_tool.profiles_json_path(), settings_file=self.current_tool.settings_file_path())
        else:
            self.save_tool_config(factorio=factorio, profiles_json=profiles_json)
        self.destroy()

    def logo_label(self, master: tk.Misc) -> tk.Label:
        logo_path = (
            modlist.factorio_root(self.factorio_exe or modlist.DEFAULT_FACTORIO)
            / "data"
            / "base"
            / "graphics"
            / "entity"
            / "factorio-logo"
            / "factorio-logo-11tiles.png"
        )
        try:
            original = tk.PhotoImage(file=str(logo_path))
            max_width = 170
            max_height = 34
            width_scale = (original.width() + max_width - 1) // max_width
            height_scale = (original.height() + max_height - 1) // max_height
            scale = max(1, width_scale, height_scale)
            self.logo_image = original.subsample(scale, scale)
            return tk.Label(master, image=self.logo_image, bg=BG, padx=14, pady=6)
        except tk.TclError:
            self.logo_image = None
            return tk.Label(master, text="FACTORIO", bg=BG, fg=TEXT, font=("Segoe UI", 18, "bold"), padx=18, pady=10)

    def run_worker(
        self,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
        busy_text: str,
        fail_title: str,
    ) -> None:
        """Run slow filesystem work off the Tk thread and report back safely."""
        self.status(busy_text)
        results: queue.Queue[tuple[bool, Any]] = queue.Queue()

        def target() -> None:
            try:
                results.put((True, work()))
            except Exception as exc:  # noqa: BLE001 - show tool errors in the UI.
                results.put((False, exc))

        threading.Thread(target=target, daemon=True).start()

        def poll() -> None:
            try:
                ok, value = results.get_nowait()
            except queue.Empty:
                self.after(50, poll)
                return
            if ok:
                on_success(value)
                self.refresh_script_output_size()
            else:
                self.status("Error")
                messagebox.showerror(fail_title, str(value), parent=self)

        poll()


class ModListTool(ToolFrame):
    """Tab for editing Factorio mod-list.json and saving mod profiles."""

    title = "Mod List"

    def __init__(self, master: tk.Misc, app: ToolApp):
        super().__init__(master, app)
        self.factorio_var = tk.StringVar(value=str(app.factorio_exe or modlist.DEFAULT_FACTORIO))
        self.profiles_json_var = tk.StringVar(value=str(app.profiles_json or self.default_profiles_json()))
        self.profile_name_var = tk.StringVar(value=app.last_profile)
        self.profile_label_var = tk.StringVar()
        self.count_var = tk.StringVar(value="0 mods enabled")
        self.mod_list_warning_var = tk.StringVar()
        self.mod_vars: dict[str, tk.BooleanVar] = {}
        self.profile_rows: dict[str, str] = {}
        self.profiles: dict[str, dict[str, object]] = {}
        self.hover_profile_index: int | None = None
        self.last_mod_list_warning: str | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build()
        self.refresh()

    def default_profiles_json(self) -> Path:
        return modlist.DEFAULT_PROFILES_JSON

    def _build(self) -> None:
        self._build_paths()
        self._build_main()
        self._build_footer()

    def _panel(self, master: tk.Misc) -> tk.Frame:
        return tk.Frame(master, bg=PANEL, relief=tk.SUNKEN, bd=2, highlightthickness=1, highlightbackground=LINE)

    def _label(self, master: tk.Misc, text: str, *, muted: bool = False, size: int = 10, bold: bool = False) -> tk.Label:
        return tk.Label(master, text=text, bg=PANEL, fg=MUTED if muted else BODY_TEXT, font=("Segoe UI", size, "bold" if bold else "normal"))

    def _build_paths(self) -> None:
        paths = self._panel(self)
        paths.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        paths.columnconfigure(1, weight=1)

        self._label(paths, "Factorio", bold=True).grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 4))
        ttk.Entry(paths, textvariable=self.factorio_var).grid(row=0, column=1, sticky="ew", pady=(10, 4))
        factorio_button(paths, "Browse", self.choose_factorio).grid(row=0, column=2, padx=12, pady=(10, 4))

        self._label(paths, "Profiles JSON", bold=True).grid(row=1, column=0, sticky="w", padx=(12, 8), pady=(4, 10))
        ttk.Entry(paths, textvariable=self.profiles_json_var).grid(row=1, column=1, sticky="ew", pady=(4, 10))
        factorio_button(paths, "Browse", self.choose_profiles_json).grid(row=1, column=2, padx=12, pady=(4, 10))

    def _build_main(self) -> None:
        main = tk.Frame(self, bg=BG)
        main.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 10))
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        mods_panel = self._panel(main)
        mods_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        mods_panel.columnconfigure(0, weight=1)
        mods_panel.rowconfigure(3, weight=1)

        striped_header(mods_panel, "Installed Mods", PANEL).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 2))
        tk.Label(mods_panel, textvariable=self.count_var, bg=PANEL, fg=MUTED, anchor="w").grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        tk.Label(
            mods_panel,
            textvariable=self.mod_list_warning_var,
            bg=PANEL,
            fg=ORANGE_HOVER,
            anchor="w",
            justify=tk.LEFT,
            wraplength=620,
        ).grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

        self.mods_canvas = tk.Canvas(mods_panel, bg=LIST_BG, relief=tk.SUNKEN, bd=2, highlightthickness=1, highlightbackground=LINE)
        self.mods_scroll = factorio_scrollbar(mods_panel, self.mods_canvas.yview)
        self.mods_frame = tk.Frame(self.mods_canvas, bg=LIST_BG)
        self.mods_frame.bind("<Configure>", lambda _event: self.mods_canvas.configure(scrollregion=self.mods_canvas.bbox("all")))
        self.mods_window = self.mods_canvas.create_window((0, 0), window=self.mods_frame, anchor="nw")
        self.mods_canvas.configure(yscrollcommand=self.mods_scroll.set)
        self.mods_canvas.grid(row=3, column=0, sticky="nsew", padx=(8, 0), pady=(0, 12))
        self.mods_scroll.grid(row=3, column=1, sticky="ns", pady=(0, 12))
        self.mods_canvas.bind("<Configure>", lambda event: self.mods_canvas.itemconfigure(self.mods_window, width=event.width))
        self.bind_mods_scroll(self.mods_canvas)
        self.bind_mods_scroll(self.mods_frame)

        profiles_panel = self._panel(main)
        profiles_panel.grid(row=0, column=1, sticky="nsew")
        profiles_panel.columnconfigure(0, weight=1)
        profiles_panel.rowconfigure(1, weight=1)

        striped_header(profiles_panel, "Profiles", PANEL).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        self.profile_list = tk.Listbox(
            profiles_panel,
            exportselection=False,
            activestyle="none",
            bg=LIST_BG,
            fg=BODY_TEXT,
            selectbackground=ORANGE,
            selectforeground=BUTTON_TEXT,
            highlightthickness=1,
            highlightbackground=LINE,
            relief=tk.SUNKEN,
            bd=2,
            font=("Segoe UI", 10),
        )
        self.profile_scroll = factorio_scrollbar(profiles_panel, self.profile_list.yview)
        self.profile_list.configure(yscrollcommand=self.profile_scroll.set)
        self.profile_list.grid(row=1, column=0, sticky="nsew", padx=(12, 0))
        self.profile_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 12))
        self.profile_list.bind("<<ListboxSelect>>", self.on_profile_selected)
        self.profile_list.bind("<Motion>", self.on_profile_motion)
        self.profile_list.bind("<Leave>", self.on_profile_leave)
        self.bind_list_scroll(self.profile_list)

        profile_actions = tk.Frame(profiles_panel, bg=PANEL)
        profile_actions.grid(row=2, column=0, sticky="ew", padx=12, pady=10)
        profile_actions.columnconfigure(0, weight=1)
        profile_actions.columnconfigure(1, weight=1)
        profile_actions.columnconfigure(2, weight=1)
        factorio_button(profile_actions, "Load Profile", self.load_selected_profile).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        factorio_button(profile_actions, "Apply Profile", self.apply_selected_profile, kind="orange").grid(row=0, column=1, sticky="ew", padx=5)
        factorio_button(profile_actions, "Delete Profile", self.delete_selected_profile, kind="red").grid(row=0, column=2, sticky="ew", padx=(5, 0))

        save_box = tk.Frame(profiles_panel, bg=PANEL)
        save_box.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        save_box.columnconfigure(0, weight=1)
        tk.Label(save_box, text="Save current selection as profile", bg=PANEL, fg=MUTED, anchor="w").grid(row=0, column=0, sticky="ew", columnspan=2, pady=(0, 4))
        ttk.Entry(save_box, textvariable=self.profile_label_var).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        factorio_button(save_box, "Save", self.save_current_profile, kind="orange").grid(row=1, column=1)

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=BG)
        footer.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        footer.columnconfigure(0, weight=1)
        factorio_button(footer, "Refresh", self.refresh).grid(row=0, column=0, sticky="w")
        factorio_button(footer, "Apply Selection", self.apply_current_selection, kind="orange").grid(row=0, column=1, padx=8)
        factorio_button(footer, "Apply and Launch Factorio", self.apply_and_launch, kind="green").grid(row=0, column=2)

    def choose_factorio(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Select factorio.exe",
            filetypes=[("Factorio executable", "factorio.exe"), ("Executable", "*.exe"), ("All files", "*.*")],
        )
        if selected:
            self.factorio_var.set(selected)
            self.app.factorio_exe = Path(selected)
            self.app.save_tool_config(factorio=selected, profiles_json=self.profiles_json_path())
            self.refresh()

    def choose_profiles_json(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Select profiles JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if selected:
            self.profiles_json_var.set(selected)
            self.app.profiles_json = Path(selected)
            self.app.save_tool_config(factorio=self.factorio_path(), profiles_json=selected)
            self.refresh()

    def profiles_json_path(self) -> Path:
        return Path(self.profiles_json_var.get().strip() or self.default_profiles_json())

    def factorio_path(self) -> Path:
        return Path(self.factorio_var.get().strip())

    def selected_mods(self) -> set[str]:
        return {name for name, var in self.mod_vars.items() if var.get()}

    def refresh(self, select_profile: str | None = None) -> None:
        """Reload installed mods and profiles, then redraw both list panes."""
        factorio_path = self.factorio_path()
        profiles_json_path = self.profiles_json_path()

        def work() -> tuple[dict[str, bool], dict[str, dict[str, object]], str | None]:
            mod_list_file = modlist.default_mod_list_file(factorio_path)
            return (
                modlist.current_mod_states(factorio_path, mod_list_file=mod_list_file),
                modlist.load_profiles(profiles_json_path),
                modlist.mod_list_warning(mod_list_file),
            )

        def done(result: tuple[dict[str, bool], dict[str, dict[str, object]], str | None]) -> None:
            states, profiles, warning = result
            self.profiles = profiles
            if select_profile is not None:
                self.profile_name_var.set(select_profile)
            self.show_mod_list_warning(warning)
            self.render_mods(states)
            self.render_profiles(profiles)
            self.app.status("Mod list refreshed")

        self.app.run_worker(work, done, "Refreshing mod list...", "Refresh failed")

    def show_mod_list_warning(self, warning: str | None) -> None:
        """Show a visible warning when Factorio has not written a useful mod-list.json yet."""
        if warning is None:
            self.mod_list_warning_var.set("")
            self.last_mod_list_warning = None
            return

        short_warning = warning.split("\n\n", 1)[0]
        self.mod_list_warning_var.set(short_warning + "\nStart Factorio once up to the main menu, then refresh.")
        if warning != self.last_mod_list_warning:
            self.last_mod_list_warning = warning
            messagebox.showwarning("Mod list not initialized", warning, parent=self)

    def render_mods(self, states: dict[str, bool]) -> None:
        """Render installed mods as themed rows with checkboxes."""
        for child in self.mods_frame.winfo_children():
            child.destroy()
        self.mod_vars = {}
        sorted_mods = sorted(
            states.items(),
            key=lambda item: (PINNED_MOD_ORDER.get(item[0], len(PINNED_MOD_ORDER)), item[0].lower()),
        )
        for row, (name, enabled) in enumerate(sorted_mods):
            var = tk.BooleanVar(value=enabled)
            var.trace_add("write", lambda *_args: self.update_count())
            self.mod_vars[name] = var
            row_frame = tk.Frame(self.mods_frame, bg=LIST_ROW, highlightthickness=1, highlightbackground=LIST_LINE)
            row_frame.grid(row=row, column=0, sticky="ew", padx=0, pady=0)
            row_frame.columnconfigure(0, weight=1)
            check = tk.Checkbutton(
                row_frame,
                text=name,
                variable=var,
                bg=LIST_ROW,
                fg=BODY_TEXT,
                activebackground=PANEL_HOVER,
                activeforeground=BODY_TEXT,
                selectcolor=LIST_BG,
                anchor="w",
                padx=10,
                pady=4,
                font=("Segoe UI", 10),
                relief=tk.FLAT,
            )
            check.grid(row=0, column=0, sticky="ew")
            self.bind_mod_row_hover(row_frame, check)
            self.bind_mods_scroll(row_frame)
            self.bind_mods_scroll(check)
        self.mods_frame.columnconfigure(0, weight=1)
        self.update_count()

    def render_profiles(self, profiles: dict[str, dict[str, object]]) -> None:
        """Render selectable profile rows in the right listbox."""
        selected = self.profile_name_var.get()
        self.profile_rows = {
            f"{name}: {modlist.profile_label(name, profiles)}": name
            for name in sorted(profiles)
        }
        self.profile_list.delete(0, tk.END)
        self.hover_profile_index = None
        selected_index: int | None = None
        for index, row in enumerate(self.profile_rows):
            self.profile_list.insert(tk.END, row)
            self.profile_list.itemconfigure(index, background=LIST_BG, foreground=BODY_TEXT)
            if self.profile_rows[row] == selected:
                selected_index = index
        if selected_index is not None:
            self.profile_list.selection_set(selected_index)
            self.profile_list.see(selected_index)
            self.on_profile_selected()
        else:
            self.profile_name_var.set("")
            self.profile_label_var.set("")

    def update_count(self) -> None:
        self.count_var.set(f"{len(self.selected_mods())} mods enabled")

    def on_profile_selected(self, _event: tk.Event | None = None) -> None:
        self.reset_profile_item_styles()
        selection = self.profile_list.curselection()
        if not selection:
            self.profile_name_var.set("")
            return
        row = self.profile_list.get(selection[0])
        profile_name = self.profile_rows[row]
        self.profile_name_var.set(profile_name)
        self.profile_label_var.set(modlist.profile_label(profile_name, self.profiles))
        self.app.save_tool_config(factorio=self.factorio_path(), profiles_json=self.profiles_json_path(), last_profile=profile_name)
        self.preview_profile(profile_name)

    def on_profile_motion(self, event: tk.Event) -> None:
        index = self.profile_list.nearest(event.y)
        if index == self.hover_profile_index:
            return
        self.reset_profile_item_styles()
        if 0 <= index < self.profile_list.size() and index not in self.profile_list.curselection():
            self.profile_list.itemconfigure(index, background=PANEL_HOVER, foreground=BODY_TEXT)
            self.hover_profile_index = index

    def on_profile_leave(self, _event: tk.Event) -> None:
        self.reset_profile_item_styles()

    def reset_profile_item_styles(self) -> None:
        selected = set(self.profile_list.curselection())
        for index in range(self.profile_list.size()):
            if index not in selected:
                self.profile_list.itemconfigure(index, background=LIST_BG, foreground=BODY_TEXT)
        self.hover_profile_index = None

    def bind_list_scroll(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self.on_list_mousewheel)
        widget.bind("<Button-4>", self.on_list_mousewheel)
        widget.bind("<Button-5>", self.on_list_mousewheel)

    def bind_mods_scroll(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self.on_mods_mousewheel)
        widget.bind("<Button-4>", self.on_mods_mousewheel)
        widget.bind("<Button-5>", self.on_mods_mousewheel)

    def bind_mod_row_hover(self, row_frame: tk.Frame, check: tk.Checkbutton) -> None:
        def enter(_event: tk.Event) -> None:
            row_frame.configure(bg=PANEL_HOVER)
            check.configure(bg=PANEL_HOVER)

        def leave(_event: tk.Event) -> None:
            row_frame.configure(bg=LIST_ROW)
            check.configure(bg=LIST_ROW)

        row_frame.bind("<Enter>", enter)
        row_frame.bind("<Leave>", leave)
        check.bind("<Enter>", enter)
        check.bind("<Leave>", leave)

    def wheel_delta(self, event: tk.Event) -> int:
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        return -1 if event.delta > 0 else 1

    def on_list_mousewheel(self, event: tk.Event) -> str:
        widget = event.widget
        if isinstance(widget, tk.Listbox):
            widget.yview_scroll(self.wheel_delta(event) * 3, "units")
        return "break"

    def on_mods_mousewheel(self, event: tk.Event) -> str:
        self.mods_canvas.yview_scroll(self.wheel_delta(event) * 3, "units")
        return "break"

    def preview_profile(self, profile_name: str, warn_missing: bool = False) -> bool:
        """Reflect a profile's mod selection in the left checkbox list."""
        enabled = modlist.enabled_mods_for_profile(profile_name, self.profiles)
        for name, var in self.mod_vars.items():
            var.set(name in enabled)
        missing = sorted(enabled - set(self.mod_vars), key=str.lower)
        if missing and warn_missing:
            messagebox.showwarning(
                "Profile has missing mods",
                "These profile mods are not currently installed or visible:\n\n" + "\n".join(missing),
                parent=self,
            )
        self.app.status(f"Previewing profile {profile_name}")
        return True

    def load_selected_profile(self, warn_missing: bool = True) -> bool:
        profile_name = self.profile_name_var.get()
        if not profile_name:
            return False
        if self.preview_profile(profile_name, warn_missing=warn_missing):
            self.app.status(f"Loaded profile {profile_name}")
            return True
        return False

    def apply_selected_profile(self) -> None:
        if self.load_selected_profile():
            self.apply_current_selection()

    def apply_current_selection(self, after_apply: Callable[[], None] | None = None) -> None:
        """Write the current checkbox state to Factorio's mod-list.json."""
        enabled = self.selected_mods()
        factorio_path = self.factorio_path()

        def work() -> dict[str, Any]:
            return modlist.apply_enabled_mods(factorio_path, enabled)

        def done(result: dict[str, Any]) -> None:
            self.app.status(f"Applied {len(result['enabled'])} enabled mods")
            self.app.save_tool_config(factorio=self.factorio_path(), profiles_json=self.profiles_json_path(), last_profile=self.profile_name_var.get() or None)
            if after_apply is not None:
                after_apply()
            else:
                messagebox.showinfo("Mod list applied", f"Applied {len(result['enabled'])} enabled mods.", parent=self)

        self.app.run_worker(work, done, "Applying selected mods...", "Apply failed")

    def save_current_profile(self) -> None:
        """Save the current checkbox state under the entered profile label."""
        label = self.profile_label_var.get().strip()
        if not label:
            messagebox.showinfo("Profile name missing", "Enter a profile name first.", parent=self)
            return
        path = self.profiles_json_path()
        enabled = self.selected_mods()

        def work() -> tuple[str, dict[str, dict[str, object]]]:
            profile_name = modlist.save_profile(path, label, enabled)
            return profile_name, modlist.load_profiles(path)

        def done(result: tuple[str, dict[str, dict[str, object]]]) -> None:
            profile_name, profiles = result
            self.profiles = profiles
            self.profile_name_var.set(profile_name)
            self.render_profiles(profiles)
            self.load_selected_profile(warn_missing=False)
            self.app.save_tool_config(factorio=self.factorio_path(), profiles_json=path, last_profile=profile_name)
            messagebox.showinfo("Profile saved", f"Saved profile {profile_name}.", parent=self)

        self.app.run_worker(work, done, "Saving profile...", "Save failed")

    def delete_selected_profile(self) -> None:
        """Delete the selected user profile after confirmation."""
        profile_name = self.profile_name_var.get()
        if not profile_name:
            return
        if profile_name in modlist.BUILTIN_PROFILES:
            messagebox.showinfo("Built-in profile", "Built-in profiles cannot be deleted.", parent=self)
            return
        label = modlist.profile_label(profile_name, self.profiles)
        confirmed = confirm_dialog(
            self,
            "Confirmation",
            f"You are about to permanently delete profile '{label}'.\n\n{self.profiles_json_path()}",
            "Delete",
            danger=True,
        )
        if not confirmed:
            return
        path = self.profiles_json_path()

        def work() -> dict[str, dict[str, object]]:
            modlist.delete_profile(path, profile_name)
            return modlist.load_profiles(path)

        def done(profiles: dict[str, dict[str, object]]) -> None:
            self.profiles = profiles
            self.profile_name_var.set("")
            self.profile_label_var.set("")
            if self.app.last_profile == profile_name:
                self.app.tool_config = modlist.update_tool_config(last_profile=None)
                self.app.last_profile = ""
            self.render_profiles(profiles)
            self.app.status(f"Deleted profile {profile_name}")

        self.app.run_worker(work, done, "Deleting profile...", "Delete failed")

    def apply_and_launch(self) -> None:
        """Apply the current mod selection, then launch factorio.exe."""
        def launch() -> None:
            factorio = self.factorio_path()
            subprocess.Popen([str(factorio)], cwd=str(factorio.parent))  # noqa: S603 - local executable selected by user.
            self.app.status("Factorio launched")

        self.apply_current_selection(after_apply=launch)


class SettingsTool(ToolFrame):
    """Tab for editing startup settings and storing them inside profiles."""

    title = "Settings"

    def __init__(self, master: tk.Misc, app: ToolApp):
        super().__init__(master, app)
        default_settings = settings.default_settings_file(app.factorio_exe or modlist.DEFAULT_FACTORIO)
        settings_file = modlist.config_path_value(app.tool_config, "settings_file", default_settings)
        self.settings_file_var = tk.StringVar(value=str(settings_file or default_settings))
        self.profiles_json_var = tk.StringVar(value=str(app.profiles_json or modlist.DEFAULT_PROFILES_JSON))
        self.profile_name_var = tk.StringVar(value=app.last_profile)
        self.profile_label_var = tk.StringVar()
        self.definitions: list[dict[str, Any]] = []
        self.setting_vars: dict[str, tk.Variable] = {}
        self.current_setting_values: dict[str, Any] = {}
        self.setting_name_column = SETTING_NAME_COLUMN
        self.setting_drag_start_x: int | None = None
        self.setting_drag_start_width = SETTING_NAME_COLUMN
        self.setting_column_widgets: list[tk.Misc] = []
        self.setting_label_widgets: list[tuple[tk.Label, str]] = []
        self.profile_rows: dict[str, str] = {}
        self.profiles: dict[str, dict[str, object]] = {}
        self.hover_profile_index: int | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build()
        self.refresh()

    def _build(self) -> None:
        self._build_paths()
        self._build_main()
        self._build_footer()

    def _panel(self, master: tk.Misc) -> tk.Frame:
        return tk.Frame(master, bg=PANEL, relief=tk.SUNKEN, bd=2, highlightthickness=1, highlightbackground=LINE)

    def _build_paths(self) -> None:
        paths = self._panel(self)
        paths.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        paths.columnconfigure(1, weight=1)

        tk.Label(paths, text="Settings DAT", bg=PANEL, fg=BODY_TEXT, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 4))
        ttk.Entry(paths, textvariable=self.settings_file_var).grid(row=0, column=1, sticky="ew", pady=(10, 4))
        factorio_button(paths, "Browse", self.choose_settings_file).grid(row=0, column=2, padx=12, pady=(10, 4))

        tk.Label(paths, text="Profiles JSON", bg=PANEL, fg=BODY_TEXT, font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", padx=(12, 8), pady=(4, 10))
        ttk.Entry(paths, textvariable=self.profiles_json_var).grid(row=1, column=1, sticky="ew", pady=(4, 10))
        factorio_button(paths, "Browse", self.choose_profiles_json).grid(row=1, column=2, padx=12, pady=(4, 10))

    def _build_main(self) -> None:
        main = tk.Frame(self, bg=BG)
        main.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 10))
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        settings_panel = self._panel(main)
        settings_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        settings_panel.columnconfigure(0, weight=1)
        settings_panel.rowconfigure(1, weight=1)
        striped_header(settings_panel, "Startup Settings", PANEL).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))

        self.settings_canvas = tk.Canvas(settings_panel, bg=LIST_BG, relief=tk.SUNKEN, bd=2, highlightthickness=1, highlightbackground=LINE)
        self.settings_scroll = factorio_scrollbar(settings_panel, self.settings_canvas.yview)
        self.settings_frame = tk.Frame(self.settings_canvas, bg=LIST_BG)
        self.settings_frame.bind("<Configure>", lambda _event: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all")))
        self.settings_window = self.settings_canvas.create_window((0, 0), window=self.settings_frame, anchor="nw")
        self.settings_canvas.configure(yscrollcommand=self.settings_scroll.set)
        self.settings_canvas.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 12))
        self.settings_scroll.grid(row=1, column=1, sticky="ns", pady=(0, 12))
        self.settings_canvas.bind("<Configure>", lambda event: self.settings_canvas.itemconfigure(self.settings_window, width=event.width))
        self.bind_settings_scroll(self.settings_canvas)
        self.bind_settings_scroll(self.settings_frame)

        profiles_panel = self._panel(main)
        profiles_panel.grid(row=0, column=1, sticky="nsew")
        profiles_panel.columnconfigure(0, weight=1)
        profiles_panel.rowconfigure(1, weight=1)
        striped_header(profiles_panel, "Profiles", PANEL).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))

        self.profile_list = tk.Listbox(
            profiles_panel,
            exportselection=False,
            activestyle="none",
            bg=LIST_BG,
            fg=BODY_TEXT,
            selectbackground=ORANGE,
            selectforeground=BUTTON_TEXT,
            highlightthickness=1,
            highlightbackground=LINE,
            relief=tk.SUNKEN,
            bd=2,
            font=("Segoe UI", 10),
        )
        self.profile_scroll = factorio_scrollbar(profiles_panel, self.profile_list.yview)
        self.profile_list.configure(yscrollcommand=self.profile_scroll.set)
        self.profile_list.grid(row=1, column=0, sticky="nsew", padx=(12, 0))
        self.profile_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 12))
        self.profile_list.bind("<<ListboxSelect>>", self.on_profile_selected)
        self.profile_list.bind("<Motion>", self.on_profile_motion)
        self.profile_list.bind("<Leave>", self.on_profile_leave)
        self.bind_list_scroll(self.profile_list)

        profile_actions = tk.Frame(profiles_panel, bg=PANEL)
        profile_actions.grid(row=2, column=0, sticky="ew", padx=12, pady=10)
        profile_actions.columnconfigure(0, weight=1)
        profile_actions.columnconfigure(1, weight=1)
        factorio_button(profile_actions, "Load Settings", self.load_selected_profile).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        factorio_button(profile_actions, "Apply Settings", self.apply_selected_profile, kind="orange").grid(row=0, column=1, sticky="ew", padx=(5, 0))

        save_box = tk.Frame(profiles_panel, bg=PANEL)
        save_box.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        save_box.columnconfigure(0, weight=1)
        tk.Label(save_box, text="Save current settings to profile", bg=PANEL, fg=MUTED, anchor="w").grid(row=0, column=0, sticky="ew", columnspan=2, pady=(0, 4))
        ttk.Entry(save_box, textvariable=self.profile_label_var).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        factorio_button(save_box, "Save", self.save_current_settings, kind="orange").grid(row=1, column=1)

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=BG)
        footer.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        footer.columnconfigure(0, weight=1)
        factorio_button(footer, "Refresh", self.refresh).grid(row=0, column=0, sticky="w")
        factorio_button(footer, "Apply Settings", self.apply_current_settings, kind="orange").grid(row=0, column=1, padx=8)

    def choose_settings_file(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="Select mod-settings.dat", filetypes=[("Factorio settings", "mod-settings.dat"), ("All files", "*.*")])
        if selected:
            self.settings_file_var.set(selected)
            self.app.save_tool_config(settings_file=selected, profiles_json=self.profiles_json_path())
            self.refresh()

    def choose_profiles_json(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="Select profiles JSON", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if selected:
            self.profiles_json_var.set(selected)
            self.app.profiles_json = Path(selected)
            self.app.save_tool_config(profiles_json=selected)
            self.refresh()

    def settings_file_path(self) -> Path:
        return Path(self.settings_file_var.get().strip() or settings.default_settings_file(self.app.factorio_exe or modlist.DEFAULT_FACTORIO))

    def profiles_json_path(self) -> Path:
        return Path(self.profiles_json_var.get().strip() or modlist.DEFAULT_PROFILES_JSON)

    def refresh(self, select_profile: str | None = None) -> None:
        """Reload settings definitions and profiles; settings render after profile click."""
        profiles_json_path = self.profiles_json_path()

        def work() -> dict[str, dict[str, object]]:
            profiles = modlist.load_profiles(profiles_json_path)
            return profiles

        def done(result: dict[str, dict[str, object]]) -> None:
            self.definitions = []
            self.profiles = result
            if select_profile is not None:
                self.profile_name_var.set(select_profile)
            self.render_settings_placeholder()
            self.render_profiles(self.profiles)
            self.app.status("Settings refreshed")

        self.app.run_worker(work, done, "Refreshing settings...", "Refresh failed")

    def render_settings_placeholder(self, text: str = "Click a profile to show ->") -> None:
        """Show the empty-state message before a profile is selected."""
        for child in self.settings_frame.winfo_children():
            child.destroy()
        self.setting_vars = {}
        self.current_setting_values = {}
        self.setting_column_widgets = []
        self.setting_label_widgets = []
        placeholder = tk.Frame(self.settings_frame, bg=LIST_BG)
        placeholder.grid(row=0, column=0, sticky="nsew")
        placeholder.columnconfigure(0, weight=1)
        placeholder.rowconfigure(0, weight=1)
        tk.Label(
            placeholder,
            text=text,
            bg=LIST_BG,
            fg=MUTED,
            font=("Segoe UI", 18, "bold"),
            padx=24,
            pady=160,
        ).grid(row=0, column=0, sticky="nsew")
        self.settings_frame.columnconfigure(0, weight=1)

    def setting_name_chars(self) -> int:
        """Approximate text-column width from the draggable pixel width."""
        return max(12, min(64, int(self.setting_name_column / 8)))

    def apply_setting_column_width(self) -> None:
        """Apply the current setting-name column width to existing table rows."""
        name_chars = self.setting_name_chars()
        for widget in self.setting_column_widgets:
            try:
                widget.columnconfigure(0, minsize=self.setting_name_column)
            except tk.TclError:
                continue
        for label, text in self.setting_label_widgets:
            try:
                label.configure(text=compact_text(text, name_chars), width=name_chars)
            except tk.TclError:
                continue

    def begin_setting_column_drag(self, event: tk.Event) -> None:
        """Start dragging the Settings/Value column divider."""
        self.setting_drag_start_x = event.x_root
        self.setting_drag_start_width = self.setting_name_column

    def drag_setting_column(self, event: tk.Event) -> str:
        """Resize the setting-name column without recreating the table widgets."""
        if self.setting_drag_start_x is None:
            return "break"
        delta = event.x_root - self.setting_drag_start_x
        width = max(SETTING_NAME_MIN_COLUMN, min(SETTING_NAME_MAX_COLUMN, self.setting_drag_start_width + delta))
        if width != self.setting_name_column:
            self.setting_name_column = width
            self.apply_setting_column_width()
        return "break"

    def end_setting_column_drag(self, _event: tk.Event) -> None:
        """Finish dragging the Settings/Value column divider."""
        self.setting_drag_start_x = None

    def make_setting_separator(self, master: tk.Misc, *, active: bool = False) -> tk.Frame:
        """Create the visible column divider used by the settings table."""
        separator = tk.Frame(master, bg=ORANGE_DARK if active else LINE, width=6, cursor="sb_h_double_arrow")
        separator.bind("<Button-1>", self.begin_setting_column_drag)
        separator.bind("<B1-Motion>", self.drag_setting_column)
        separator.bind("<ButtonRelease-1>", self.end_setting_column_drag)
        return separator

    def render_settings(self, values: dict[str, Any]) -> None:
        """Render settings as a two-column editor with type-specific value widgets."""
        for child in self.settings_frame.winfo_children():
            child.destroy()
        self.setting_vars = {}
        self.current_setting_values = dict(values)
        self.setting_column_widgets = []
        self.setting_label_widgets = []
        name_chars = self.setting_name_chars()

        header = tk.Frame(self.settings_frame, bg=PANEL_DARK, highlightthickness=1, highlightbackground=LINE)
        header.grid(row=0, column=0, sticky="ew")
        self.setting_column_widgets.append(header)
        header.columnconfigure(0, weight=0, minsize=self.setting_name_column)
        header.columnconfigure(1, weight=0, minsize=6)
        header.columnconfigure(2, weight=1, minsize=280)
        tk.Label(header, text="Setting", bg=PANEL_DARK, fg=TEXT, anchor="w", padx=10, pady=5, width=name_chars, font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="ew")
        header_separator = self.make_setting_separator(header, active=True)
        header_separator.grid(row=0, column=1, sticky="ns")
        tk.Label(header, text="Value", bg=PANEL_DARK, fg=TEXT, anchor="w", padx=10, pady=5, font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky="ew")
        self.bind_settings_scroll(header)
        self.bind_settings_scroll(header_separator)

        shown_mods = {definition.get("mod") for definition in self.definitions if definition.get("mod")}
        show_mod_prefix = len(shown_mods) > 1
        previous_mod_name: str | None = None
        grid_row = 1
        for row, definition in enumerate(self.definitions, start=1):
            name = definition["name"]
            display_name = str(definition.get("display_name") or name)
            mod_name = str(definition.get("mod") or "")
            if show_mod_prefix and mod_name and mod_name != previous_mod_name:
                group_frame = tk.Frame(self.settings_frame, bg=LIST_BG)
                group_frame.grid(row=grid_row, column=0, sticky="ew")
                group_frame.columnconfigure(0, weight=1)
                group_label = tk.Label(
                    group_frame,
                    text=f"{mod_name}:",
                    bg=LIST_BG,
                    fg=BODY_TEXT,
                    anchor="w",
                    padx=10,
                    pady=2,
                    font=("Segoe UI", 9),
                )
                group_label.grid(row=0, column=0, sticky="ew", pady=(5, 0))
                self.bind_settings_scroll(group_frame)
                self.bind_settings_scroll(group_label)
                grid_row += 1
                previous_mod_name = mod_name

            label_text = display_name
            row_frame = tk.Frame(self.settings_frame, bg=LIST_ROW, highlightthickness=1, highlightbackground=LIST_LINE)
            row_frame.grid(row=grid_row, column=0, sticky="ew")
            grid_row += 1
            self.setting_column_widgets.append(row_frame)
            row_frame.columnconfigure(0, weight=0, minsize=self.setting_name_column)
            row_frame.columnconfigure(1, weight=0, minsize=6)
            row_frame.columnconfigure(2, weight=1, minsize=280)
            label = tk.Label(
                row_frame,
                text=compact_text(label_text, name_chars),
                bg=LIST_ROW,
                fg=BODY_TEXT,
                anchor="w",
                padx=(28 if show_mod_prefix and mod_name else 10),
                pady=5,
                width=name_chars,
            )
            label.grid(row=0, column=0, sticky="ew")
            self.setting_label_widgets.append((label, label_text))
            label.bind("<Enter>", lambda _event, setting=f"{label_text} ({name})": self.app.status(setting))
            label.bind("<Leave>", lambda _event: self.app.status("Settings"))
            value = values.get(name, definition.get("default"))
            setting_type = definition.get("type")
            allowed_values = definition.get("allowed_values")
            separator = self.make_setting_separator(row_frame)
            separator.grid(row=0, column=1, sticky="ns")
            value_cell = tk.Frame(row_frame, bg=LIST_ROW)
            value_cell.grid(row=0, column=2, sticky="ew", padx=(4, 10), pady=3)
            value_cell.columnconfigure(0, weight=1)
            if setting_type == "bool-setting":
                var = tk.BooleanVar(value=bool(value))
                widget = tk.Checkbutton(
                    value_cell,
                    variable=var,
                    bg=LIST_ROW,
                    activebackground=PANEL_HOVER,
                    selectcolor=LIST_BG,
                    highlightthickness=0,
                    bd=0,
                )
                widget.grid(row=0, column=0, sticky="w")
            elif isinstance(allowed_values, list) and allowed_values:
                string_values = [str(item) for item in allowed_values]
                var = tk.StringVar(value="" if value is None else str(value))
                widget = ttk.Combobox(value_cell, textvariable=var, values=string_values, state="readonly")
                widget.grid(row=0, column=0, sticky="ew")
            else:
                var = tk.StringVar(value="" if value is None else str(value))
                widget = ttk.Entry(value_cell, textvariable=var)
                widget.grid(row=0, column=0, sticky="ew")
            self.setting_vars[name] = var
            self.bind_settings_scroll(row_frame)
            self.bind_settings_scroll(label)
            self.bind_settings_scroll(separator)
            self.bind_settings_scroll(value_cell)
            self.bind_settings_scroll(widget)
        self.settings_frame.columnconfigure(0, weight=1)

    def collect_settings(self) -> dict[str, Any]:
        """Collect widget values and convert them back to Factorio setting types."""
        definitions_by_name = {definition["name"]: definition for definition in self.definitions}
        values: dict[str, Any] = {}
        for name, var in self.setting_vars.items():
            definition = definitions_by_name[name]
            if definition.get("type") == "bool-setting":
                values[name] = bool(var.get())
            else:
                raw = str(var.get())
                if definition.get("type") == "int-setting":
                    values[name] = int(raw)
                elif definition.get("type") == "double-setting":
                    values[name] = float(raw)
                else:
                    values[name] = settings.parse_value(raw)
        return values

    def render_profiles(self, profiles: dict[str, dict[str, object]]) -> None:
        """Render selectable profile rows for loading/saving settings."""
        selected = self.profile_name_var.get()
        self.profile_rows = {f"{name}: {modlist.profile_label(name, profiles)}": name for name in sorted(profiles)}
        self.profile_list.delete(0, tk.END)
        self.hover_profile_index = None
        selected_index: int | None = None
        for index, row in enumerate(self.profile_rows):
            self.profile_list.insert(tk.END, row)
            self.profile_list.itemconfigure(index, background=LIST_BG, foreground=BODY_TEXT)
            if self.profile_rows[row] == selected:
                selected_index = index
        if selected_index is not None:
            self.profile_list.selection_set(selected_index)
            self.profile_list.see(selected_index)

    def on_profile_selected(self, _event: tk.Event | None = None) -> None:
        self.reset_profile_item_styles()
        selection = self.profile_list.curselection()
        if not selection:
            self.profile_name_var.set("")
            return
        row = self.profile_list.get(selection[0])
        profile_name = self.profile_rows[row]
        self.profile_name_var.set(profile_name)
        self.profile_label_var.set(modlist.profile_label(profile_name, self.profiles))
        self.preview_profile(profile_name)

    def preview_profile(self, profile_name: str) -> bool:
        """Show saved profile settings, or current mod-settings.dat values as a baseline."""
        enabled_mods = modlist.enabled_mods_for_profile(profile_name, self.profiles)
        self.definitions = settings.read_startup_settings_for_mods(
            enabled_mods,
            factorio_exe=self.app.factorio_exe or modlist.DEFAULT_FACTORIO,
        )
        if not self.definitions:
            self.render_settings_placeholder("No startup settings found for this profile")
            self.app.status(f"Profile {profile_name} has no visible startup settings")
            return False
        profile_values = modlist.profile_settings(profile_name, self.profiles)
        if profile_values:
            self.render_settings(profile_values)
            self.app.status(f"Previewing settings from {profile_name}")
            return True
        current_values = settings.get_startup_settings(self.settings_file_path(), self.definitions)
        self.render_settings(current_values)
        self.app.status(f"Profile {profile_name} has no saved settings; showing current settings")
        return True

    def load_selected_profile(self) -> bool:
        profile_name = self.profile_name_var.get()
        return bool(profile_name and self.preview_profile(profile_name))

    def apply_selected_profile(self) -> None:
        if self.load_selected_profile():
            self.apply_current_settings()

    def apply_current_settings(self) -> None:
        """Write the currently visible settings table to mod-settings.dat."""
        if not self.setting_vars:
            messagebox.showinfo("No settings loaded", "Click a profile with saved settings first.", parent=self)
            return
        values = self.collect_settings()
        settings_file_path = self.settings_file_path()

        def work() -> None:
            settings.set_startup_settings(settings_file_path, values)

        def done(_result: None) -> None:
            self.app.save_tool_config(profiles_json=self.profiles_json_path(), last_profile=self.profile_name_var.get() or None)
            self.app.status(f"Applied {len(values)} startup settings")
            messagebox.showinfo("Settings applied", f"Applied {len(values)} startup settings.", parent=self)

        self.app.run_worker(work, done, "Applying settings...", "Apply failed")

    def save_current_settings(self) -> None:
        """Save the currently visible settings table into the selected profile."""
        if not self.setting_vars:
            messagebox.showinfo("No settings loaded", "Click a profile with saved settings first.", parent=self)
            return
        label = self.profile_label_var.get().strip()
        if not label:
            messagebox.showinfo("Profile name missing", "Enter or select a profile name first.", parent=self)
            return
        profile_name = self.profile_name_var.get() or modlist.profile_key_from_label(label)
        path = self.profiles_json_path()
        values = self.collect_settings()

        def work() -> dict[str, dict[str, object]]:
            modlist.update_profile_settings(path, profile_name, values)
            return modlist.load_profiles(path)

        def done(profiles: dict[str, dict[str, object]]) -> None:
            self.profiles = profiles
            self.profile_name_var.set(profile_name)
            self.render_profiles(profiles)
            self.app.save_tool_config(profiles_json=path, last_profile=profile_name)
            messagebox.showinfo("Settings saved", f"Saved settings to profile {profile_name}.", parent=self)

        self.app.run_worker(work, done, "Saving settings...", "Save failed")

    def on_profile_motion(self, event: tk.Event) -> None:
        index = self.profile_list.nearest(event.y)
        if index == self.hover_profile_index:
            return
        self.reset_profile_item_styles()
        if 0 <= index < self.profile_list.size() and index not in self.profile_list.curselection():
            self.profile_list.itemconfigure(index, background=PANEL_HOVER, foreground=BODY_TEXT)
            self.hover_profile_index = index

    def on_profile_leave(self, _event: tk.Event) -> None:
        self.reset_profile_item_styles()

    def reset_profile_item_styles(self) -> None:
        selected = set(self.profile_list.curselection())
        for index in range(self.profile_list.size()):
            if index not in selected:
                self.profile_list.itemconfigure(index, background=LIST_BG, foreground=BODY_TEXT)
        self.hover_profile_index = None

    def bind_list_scroll(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self.on_list_mousewheel)
        widget.bind("<Button-4>", self.on_list_mousewheel)
        widget.bind("<Button-5>", self.on_list_mousewheel)

    def bind_settings_scroll(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self.on_settings_mousewheel)
        widget.bind("<Button-4>", self.on_settings_mousewheel)
        widget.bind("<Button-5>", self.on_settings_mousewheel)

    def wheel_delta(self, event: tk.Event) -> int:
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        return -1 if event.delta > 0 else 1

    def on_list_mousewheel(self, event: tk.Event) -> str:
        widget = event.widget
        if isinstance(widget, tk.Listbox):
            widget.yview_scroll(self.wheel_delta(event) * 3, "units")
        return "break"

    def on_settings_mousewheel(self, event: tk.Event) -> str:
        self.settings_canvas.yview_scroll(self.wheel_delta(event) * 3, "units")
        return "break"


class MaterialFlowTool(ToolFrame):
    """Tab for generating configured Factorio JSON dumps and opening the browser viewer."""

    title = "Material Flow"

    def __init__(self, master: tk.Misc, app: ToolApp):
        super().__init__(master, app)
        self.factorio_var = tk.StringVar(value=str(app.factorio_exe or modlist.DEFAULT_FACTORIO))
        self.mod_root_var = tk.StringVar(value=str(app.mod_root or ""))
        self.profiles_json_var = tk.StringVar(value=str(self.default_profiles_json_path()))
        default_settings = settings.default_settings_file(app.factorio_exe or modlist.DEFAULT_FACTORIO)
        settings_file = modlist.config_path_value(app.tool_config, "settings_file", default_settings)
        self.settings_file_var = tk.StringVar(value=str(settings_file or default_settings))
        self.viewer_artifact_var = tk.StringVar(value=self.default_viewer_artifact())
        self.profile_name_var = tk.StringVar(value=app.last_profile)
        self.profile_label_var = tk.StringVar(value="")
        self.output_var = tk.StringVar(value="No dump generated in this UI session.")
        self.profiles: dict[str, dict[str, object]] = {}
        self.profile_rows: dict[str, str] = {}
        self.hover_profile_index: int | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build()
        self.refresh()

    def _panel(self, master: tk.Misc) -> tk.Frame:
        return tk.Frame(master, bg=PANEL, relief=tk.SUNKEN, bd=2, highlightthickness=1, highlightbackground=LINE)

    def _label(self, master: tk.Misc, text: str, *, muted: bool = False, size: int = 10, bold: bool = False) -> tk.Label:
        return tk.Label(master, text=text, bg=PANEL, fg=MUTED if muted else BODY_TEXT, font=("Segoe UI", size, "bold" if bold else "normal"))

    def _build(self) -> None:
        self._build_paths()
        self._build_main()
        self._build_footer()

    def _build_paths(self) -> None:
        paths = self._panel(self)
        paths.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        paths.columnconfigure(1, weight=1)

        self._label(paths, "Factorio", bold=True).grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 4))
        ttk.Entry(paths, textvariable=self.factorio_var).grid(row=0, column=1, sticky="ew", pady=(10, 4))
        factorio_button(paths, "Browse", self.choose_factorio).grid(row=0, column=2, padx=12, pady=(10, 4))

        self._label(paths, "Mod Root", bold=True).grid(row=1, column=0, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(paths, textvariable=self.mod_root_var).grid(row=1, column=1, sticky="ew", pady=4)
        factorio_button(paths, "Browse", self.choose_mod_root).grid(row=1, column=2, padx=12, pady=4)

        self._label(paths, "Profiles JSON", bold=True).grid(row=2, column=0, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(paths, textvariable=self.profiles_json_var).grid(row=2, column=1, sticky="ew", pady=4)
        factorio_button(paths, "Browse", self.choose_profiles_json).grid(row=2, column=2, padx=12, pady=4)

        self._label(paths, "Viewer Artifact", bold=True).grid(row=3, column=0, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(paths, textvariable=self.viewer_artifact_var).grid(row=3, column=1, sticky="ew", pady=4)

        self._label(paths, "Settings", bold=True).grid(row=4, column=0, sticky="w", padx=(12, 8), pady=(4, 10))
        ttk.Entry(paths, textvariable=self.settings_file_var).grid(row=4, column=1, sticky="ew", pady=(4, 10))
        factorio_button(paths, "Browse", self.choose_settings_file).grid(row=4, column=2, padx=12, pady=(4, 10))

    def _build_main(self) -> None:
        main = tk.Frame(self, bg=BG)
        main.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        profiles_panel = self._panel(main)
        profiles_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        profiles_panel.columnconfigure(0, weight=1)
        profiles_panel.rowconfigure(1, weight=1)
        striped_header(profiles_panel, "Profile", PANEL).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        self.profile_list = tk.Listbox(
            profiles_panel,
            exportselection=False,
            activestyle="none",
            bg=LIST_BG,
            fg=BODY_TEXT,
            selectbackground=ORANGE,
            selectforeground=BUTTON_TEXT,
            highlightthickness=1,
            highlightbackground=LINE,
            relief=tk.SUNKEN,
            bd=2,
            font=("Segoe UI", 10),
        )
        self.profile_scroll = factorio_scrollbar(profiles_panel, self.profile_list.yview)
        self.profile_list.configure(yscrollcommand=self.profile_scroll.set)
        self.profile_list.grid(row=1, column=0, sticky="nsew", padx=(12, 0))
        self.profile_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 12))
        self.profile_list.bind("<<ListboxSelect>>", self.on_profile_selected)
        self.profile_list.bind("<Motion>", self.on_profile_motion)
        self.profile_list.bind("<Leave>", self.on_profile_leave)
        self.bind_list_scroll(self.profile_list)

        details_panel = self._panel(main)
        details_panel.grid(row=0, column=1, sticky="nsew")
        details_panel.columnconfigure(0, weight=1)
        details_panel.rowconfigure(1, weight=1)
        striped_header(details_panel, "Active Mods and Current Settings", PANEL).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        self.details_text = tk.Text(
            details_panel,
            bg=LIST_BG,
            fg=BODY_TEXT,
            insertbackground=BODY_TEXT,
            relief=tk.SUNKEN,
            bd=2,
            highlightthickness=1,
            highlightbackground=LINE,
            wrap=tk.NONE,
            font=("Consolas", 10),
        )
        self.details_scroll = factorio_scrollbar(details_panel, self.details_text.yview)
        self.details_text.configure(yscrollcommand=self.details_scroll.set)
        self.details_text.grid(row=1, column=0, sticky="nsew", padx=(12, 0), pady=(0, 12))
        self.details_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 12), pady=(0, 12))
        self.bind_text_scroll(self.details_text)

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=BG)
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        footer.columnconfigure(3, weight=1)
        factorio_button(footer, "Refresh", self.refresh).grid(row=0, column=0, sticky="w")
        factorio_button(footer, "Create Dump and Open Viewer", self.create_dump_and_open_viewer, kind="green").grid(row=0, column=1, padx=8)
        factorio_button(footer, "Open Viewer", self.open_viewer, kind="orange").grid(row=0, column=2)
        tk.Label(footer, textvariable=self.output_var, bg=BG, fg=MUTED, anchor="e").grid(row=0, column=3, sticky="ew", padx=(12, 0))

    def factorio_path(self) -> Path:
        return Path(self.factorio_var.get().strip())

    def profiles_json_path(self) -> Path:
        return Path(self.profiles_json_var.get().strip() or modlist.DEFAULT_PROFILES_JSON)

    def mod_root_path(self) -> Path | None:
        value = self.mod_root_var.get().strip()
        return Path(value) if value else None

    def harness_config(self) -> dict[str, object]:
        mod_root = self.mod_root_path()
        if mod_root is None:
            return {}
        config_path = mod_root / "tools" / "test" / "harness.json"
        if not config_path.exists():
            return {}
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def harness_mod_name(self) -> str:
        mod_root = self.mod_root_path()
        config = self.harness_config()
        if isinstance(config.get("mod_name"), str) and config["mod_name"].strip():
            return str(config["mod_name"]).strip()
        if mod_root is not None:
            try:
                info = json.loads((mod_root / "info.json").read_text(encoding="utf-8-sig"))
                if isinstance(info, dict) and isinstance(info.get("name"), str):
                    return info["name"]
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
        return "Ingredient_Scrap"

    def harness_report_relative(self) -> Path:
        config = self.harness_config()
        mod_name = self.harness_mod_name()
        if isinstance(config.get("report_path"), str) and config["report_path"].strip():
            return Path(str(config["report_path"]).replace("\\", "/"))
        return Path(mod_name) / "test-report.json"

    def harness_artifacts(self) -> list[str]:
        config = self.harness_config()
        artifacts = config.get("artifacts")
        if isinstance(artifacts, list):
            values = [entry.strip() for entry in artifacts if isinstance(entry, str) and entry.strip()]
            if values:
                return values
        return ["test-report.json", "material-flow.json"]

    def default_viewer_artifact(self) -> str:
        for artifact in self.harness_artifacts():
            if Path(artifact).name == "material-flow.json":
                return artifact
        for artifact in self.harness_artifacts():
            if artifact.endswith(".json"):
                return artifact
        return "test-report.json"

    def default_profiles_json_path(self) -> Path:
        mod_root = self.mod_root_path()
        config = self.harness_config()
        configured = config.get("mod_profiles_json")
        if isinstance(configured, str) and configured.strip():
            path = Path(configured.replace("\\", "/"))
            return path if path.is_absolute() or mod_root is None else mod_root / path
        if mod_root is not None:
            local_profiles = mod_root / "tools" / "test" / "modlist-profiles.json"
            if local_profiles.exists():
                return local_profiles
        return self.app.profiles_json or modlist.DEFAULT_PROFILES_JSON

    def settings_file_path(self) -> Path:
        return Path(self.settings_file_var.get().strip() or settings.default_settings_file(self.factorio_path()))

    def choose_factorio(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="Select factorio.exe", filetypes=[("Factorio executable", "factorio.exe"), ("Executable", "*.exe"), ("All files", "*.*")])
        if selected:
            self.factorio_var.set(selected)
            self.app.factorio_exe = Path(selected)
            self.app.save_tool_config(factorio=selected, profiles_json=self.profiles_json_path(), settings_file=self.settings_file_path())
            self.refresh()

    def choose_mod_root(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose Factorio mod root",
            initialdir=str(Path(self.mod_root_var.get()).parent if self.mod_root_var.get() else TOOL_DIR.parent),
            parent=self,
        )
        if not selected:
            return
        self.mod_root_var.set(selected)
        self.profiles_json_var.set(str(self.default_profiles_json_path()))
        self.viewer_artifact_var.set(self.default_viewer_artifact())
        self.app.save_tool_config(mod_root=selected, profiles_json=self.profiles_json_path(), settings_file=self.settings_file_path())
        self.refresh()

    def choose_profiles_json(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="Select profiles JSON", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if selected:
            self.profiles_json_var.set(selected)
            self.app.profiles_json = Path(selected)
            self.app.save_tool_config(factorio=self.factorio_path(), profiles_json=selected, settings_file=self.settings_file_path())
            self.refresh()

    def choose_settings_file(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="Select mod-settings.dat", filetypes=[("Factorio settings", "mod-settings.dat"), ("All files", "*.*")])
        if selected:
            self.settings_file_var.set(selected)
            self.app.save_tool_config(settings_file=selected, profiles_json=self.profiles_json_path())
            self.refresh()

    def refresh(self, select_profile: str | None = None) -> None:
        def work() -> dict[str, dict[str, object]]:
            return modlist.load_profiles(self.profiles_json_path())

        def done(profiles: dict[str, dict[str, object]]) -> None:
            self.profiles = profiles
            if select_profile is not None:
                self.profile_name_var.set(select_profile)
            self.render_profiles(profiles)
            self.app.status("Material Flow profiles refreshed")

        self.app.run_worker(work, done, "Refreshing Material Flow profiles...", "Refresh failed")

    def render_profiles(self, profiles: dict[str, dict[str, object]]) -> None:
        selected = self.profile_name_var.get()
        self.profile_rows = {f"{name}: {modlist.profile_label(name, profiles)}": name for name in sorted(profiles)}
        self.profile_list.delete(0, tk.END)
        self.hover_profile_index = None
        selected_index: int | None = None
        for index, row in enumerate(self.profile_rows):
            self.profile_list.insert(tk.END, row)
            self.profile_list.itemconfigure(index, background=LIST_BG, foreground=BODY_TEXT)
            if self.profile_rows[row] == selected:
                selected_index = index
        if selected_index is None and self.profile_rows:
            selected_index = 0
        if selected_index is not None:
            self.profile_list.selection_set(selected_index)
            self.profile_list.see(selected_index)
            self.on_profile_selected()
        else:
            self.profile_name_var.set("")
            self.render_details()

    def on_profile_selected(self, _event: tk.Event | None = None) -> None:
        self.reset_profile_item_styles()
        selection = self.profile_list.curselection()
        if not selection:
            self.profile_name_var.set("")
            self.render_details()
            return
        row = self.profile_list.get(selection[0])
        profile_name = self.profile_rows[row]
        self.profile_name_var.set(profile_name)
        self.profile_label_var.set(modlist.profile_label(profile_name, self.profiles))
        self.app.save_tool_config(factorio=self.factorio_path(), profiles_json=self.profiles_json_path(), settings_file=self.settings_file_path(), last_profile=profile_name)
        self.render_details()

    def render_details(self) -> None:
        profile_name = self.profile_name_var.get()
        lines: list[str] = []
        if not profile_name:
            lines.append("No profile selected.")
        else:
            enabled = sorted(modlist.enabled_mods_for_profile(profile_name, self.profiles), key=str.lower)
            profile_values = modlist.profile_settings(profile_name, self.profiles)
            current_values = self.current_settings_for_profile(profile_name)
            lines.extend([
                f"Profile: {profile_name}",
                f"Label:   {modlist.profile_label(profile_name, self.profiles)}",
                "",
                f"Active mods ({len(enabled)}):",
            ])
            lines.extend(f"  - {name}" for name in enabled)
            lines.extend(["", f"Current startup settings ({len(current_values)}):"])
            if current_values:
                for key in sorted(current_values):
                    lines.append(f"  {key} = {current_values[key]!r}")
            else:
                lines.append("  <none found>")
            lines.extend(["", f"Profile-saved settings ({len(profile_values)}):"])
            if profile_values:
                for key in sorted(profile_values):
                    lines.append(f"  {key} = {profile_values[key]!r}")
            else:
                lines.append("  <none saved in profile>")
            lines.extend([
                "",
                "Output:",
                f"  artifact: {self.viewer_artifact()}",
                f"  selected: {self.selected_material_flow_path()}",
                f"  latest:   {self.material_flow_path()}",
                f"  {self.viewer_path()}",
            ])
        self.details_text.configure(state=tk.NORMAL)
        self.details_text.delete("1.0", tk.END)
        self.details_text.insert("1.0", "\n".join(lines))
        self.details_text.configure(state=tk.DISABLED)

    def current_settings_for_profile(self, profile_name: str) -> dict[str, Any]:
        try:
            enabled_mods = modlist.enabled_mods_for_profile(profile_name, self.profiles)
            definitions = settings.read_startup_settings_for_mods(enabled_mods, factorio_exe=self.factorio_path())
            return settings.get_startup_settings(self.settings_file_path(), definitions)
        except Exception:
            return {}

    def create_dump_and_open_viewer(self) -> None:
        profile_name = self.profile_name_var.get()
        if not profile_name:
            messagebox.showinfo("No profile selected", "Select a mod profile first.", parent=self)
            return
        script = TOOL_DIR / "material_flow.py"
        cmd = [
            sys.executable,
            str(script),
            "--factorio",
            str(self.factorio_path()),
            "--dump-profile",
            "default",
            "--mod-profiles-json",
            str(self.profiles_json_path()),
            "--viewer-artifact",
            self.viewer_artifact(),
            "--keep-mod-list",
        ]
        mod_root = self.mod_root_path()
        if mod_root is not None:
            cmd.extend(["--mod-root", str(mod_root)])
        if profile_name:
            cmd.extend(["--mod-profile", profile_name])

        def work() -> subprocess.CompletedProcess[str]:
            return subprocess.run(cmd, cwd=str(TOOL_DIR.parent.parent), text=True, capture_output=True, check=False)  # noqa: S603 - local tool command.

        def done(result: subprocess.CompletedProcess[str]) -> None:
            if result.returncode != 0:
                messagebox.showerror("Dump failed", (result.stdout + "\n" + result.stderr).strip()[-4000:], parent=self)
                self.app.status("Material Flow dump failed")
                return
            self.output_var.set(f"Dump ready: {self.selected_material_flow_path()}")
            self.app.status("JSON dump generated")
            self.open_viewer()

        self.app.run_worker(work, done, "Generating JSON dump...", "Dump failed")

    def open_viewer(self) -> None:
        viewer = self.viewer_path()
        if not viewer.exists():
            messagebox.showerror("Viewer missing", f"Viewer not found:\n{viewer}", parent=self)
            return
        url = (
            viewer.resolve().as_uri()
            + "?factorioRoot="
            + self.url_quote(str(modlist.factorio_root(self.factorio_path())))
            + "&file="
            + self.url_quote(str(self.preferred_material_flow_path()))
        )
        if self.material_flow_state_path() is not None:
            url += "&state=" + self.url_quote(str(self.preferred_material_flow_state_path()))
        webbrowser.open(url)
        self.app.status("Material Flow viewer opened in browser")

    def profile_suffix(self) -> str | None:
        profile_name = self.profile_name_var.get().strip()
        if not profile_name:
            return None
        suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile_name)
        return suffix or None

    def profiled_path(self, path: Path) -> Path:
        suffix = self.profile_suffix()
        if not suffix:
            return path
        return path.with_name(f"{path.stem}-{suffix}{path.suffix}")

    def selected_material_flow_path(self) -> Path:
        return self.profiled_path(self.material_flow_path())

    def selected_material_flow_state_path(self) -> Path:
        state_path = self.material_flow_state_path()
        return self.profiled_path(state_path) if state_path is not None else self.material_flow_path()

    def preferred_material_flow_path(self) -> Path:
        selected_path = self.selected_material_flow_path()
        if selected_path.exists():
            return selected_path
        return self.material_flow_path()

    def preferred_material_flow_state_path(self) -> Path:
        state_path = self.material_flow_state_path()
        if state_path is None:
            return self.material_flow_path()
        selected_path = self.profiled_path(state_path)
        if selected_path.exists():
            return selected_path
        return state_path

    def viewer_artifact(self) -> str:
        return self.viewer_artifact_var.get().strip() or self.default_viewer_artifact()

    def artifact_relative_path(self, artifact: str) -> Path:
        normalized = artifact.replace("\\", "/").strip()
        if "/" in normalized:
            return Path(normalized)
        report_relative = self.harness_report_relative()
        if normalized == "test-report.json":
            return report_relative
        output_dir = report_relative.parent if str(report_relative.parent) != "." else Path(self.harness_mod_name())
        return output_dir / normalized

    def material_flow_path(self) -> Path:
        return modlist.factorio_root(self.factorio_path()) / "script-output" / self.artifact_relative_path(self.viewer_artifact())

    def material_flow_state_path(self) -> Path | None:
        artifact = self.viewer_artifact()
        report_relative = self.harness_report_relative()
        output_dir = report_relative.parent if str(report_relative.parent) != "." else Path(self.harness_mod_name())
        if artifact.endswith("material-flow.json"):
            return modlist.factorio_root(self.factorio_path()) / "script-output" / output_dir / "material-flow-data.js"
        if artifact.endswith("production-flow.json"):
            return modlist.factorio_root(self.factorio_path()) / "script-output" / output_dir / "production-flow-data.js"
        if artifact.endswith("technology-flow.json"):
            return modlist.factorio_root(self.factorio_path()) / "script-output" / output_dir / "technology-flow-data.js"
        return None

    def viewer_path(self) -> Path:
        return TOOL_DIR / "json-tree-viewer.html"

    def url_quote(self, value: str) -> str:
        from urllib.parse import quote

        return quote(value.replace("\\", "/"), safe="")

    def on_profile_motion(self, event: tk.Event) -> None:
        index = self.profile_list.nearest(event.y)
        if index == self.hover_profile_index:
            return
        self.reset_profile_item_styles()
        if 0 <= index < self.profile_list.size() and index not in self.profile_list.curselection():
            self.profile_list.itemconfigure(index, background=PANEL_HOVER, foreground=BODY_TEXT)
            self.hover_profile_index = index

    def on_profile_leave(self, _event: tk.Event) -> None:
        self.reset_profile_item_styles()

    def reset_profile_item_styles(self) -> None:
        selected = set(self.profile_list.curselection())
        for index in range(self.profile_list.size()):
            if index not in selected:
                self.profile_list.itemconfigure(index, background=LIST_BG, foreground=BODY_TEXT)
        self.hover_profile_index = None

    def bind_list_scroll(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self.on_list_mousewheel)
        widget.bind("<Button-4>", self.on_list_mousewheel)
        widget.bind("<Button-5>", self.on_list_mousewheel)

    def bind_text_scroll(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self.on_text_mousewheel)
        widget.bind("<Button-4>", self.on_text_mousewheel)
        widget.bind("<Button-5>", self.on_text_mousewheel)

    def wheel_delta(self, event: tk.Event) -> int:
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        return -1 if event.delta > 0 else 1

    def on_list_mousewheel(self, event: tk.Event) -> str:
        widget = event.widget
        if isinstance(widget, tk.Listbox):
            widget.yview_scroll(self.wheel_delta(event) * 3, "units")
        return "break"

    def on_text_mousewheel(self, event: tk.Event) -> str:
        self.details_text.yview_scroll(self.wheel_delta(event) * 3, "units")
        return "break"


class MipmapTool(ToolFrame):
    """Tab for generating Factorio mipmap strips from square PNG icons."""

    title = "Mipmaps"

    def __init__(self, master: tk.Misc, app: ToolApp):
        super().__init__(master, app)
        mod_root = app.mod_root or default_mod_root() or TOOL_DIR
        self.input_var = tk.StringVar(value=str(mod_root / "_single"))
        self.output_var = tk.StringVar(value=str(mod_root / "_multi"))
        self.levels_var = tk.IntVar(value=2)
        self.crop_var = tk.BooleanVar(value=False)
        self.verbose_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready.")
        self.mipmap_script = TOOL_DIR / "mipmap.py"
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        header.columnconfigure(1, weight=1)
        tk.Label(header, text="MIPMAP GENERATOR", bg=BG, fg=TEXT, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        striped_header(header, "", BG).grid(row=0, column=1, sticky="ew", padx=12)

        controls = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        controls.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        controls.columnconfigure(1, weight=1)

        tk.Label(controls, text="INPUT", bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(12, 6))
        ttk.Entry(controls, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", pady=(12, 6))
        factorio_button(controls, "Browse", self.choose_input).grid(row=0, column=2, padx=12, pady=(12, 6))

        tk.Label(controls, text="OUTPUT", bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Entry(controls, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", pady=6)
        factorio_button(controls, "Browse", self.choose_output).grid(row=1, column=2, padx=12, pady=6)

        options = tk.Frame(controls, bg=PANEL)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 12))
        tk.Label(options, text="MIPMAPS", bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Spinbox(options, from_=1, to=16, textvariable=self.levels_var, width=5).pack(side=tk.LEFT, padx=(0, 18))
        ttk.Checkbutton(options, text="Crop canvas", variable=self.crop_var).pack(side=tk.LEFT, padx=(0, 18))
        ttk.Checkbutton(options, text="Verbose output", variable=self.verbose_var).pack(side=tk.LEFT)
        factorio_button(options, "Generate", self.run_mipmap, kind="orange").pack(side=tk.RIGHT)

        log_frame = tk.Frame(self, bg=BG)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.output_text = tk.Text(log_frame, bg=LIST_BG, fg=BODY_TEXT, insertbackground=BODY_TEXT, relief=tk.SUNKEN, bd=2, wrap=tk.WORD)
        self.output_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.output_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.output_text.configure(yscrollcommand=scrollbar.set)
        self.append_output("Mipmap output will appear here.")

        tk.Label(self, textvariable=self.status_var, bg=BG, fg=MUTED, anchor="w").grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))

    def choose_input(self) -> None:
        selected = filedialog.askdirectory(title="Choose input folder", initialdir=self.existing_directory(self.input_var.get()), parent=self)
        if selected:
            self.input_var.set(selected)

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(title="Choose output folder", initialdir=self.existing_directory(self.output_var.get()), parent=self)
        if selected:
            self.output_var.set(selected)

    def existing_directory(self, value: str) -> str:
        """Return an existing directory suitable as a folder-dialog starting point."""
        candidate = Path(value).expanduser()
        if candidate.is_dir():
            return str(candidate)
        if candidate.parent.is_dir():
            return str(candidate.parent)
        return str(TOOL_DIR)

    def append_output(self, text: str) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert("1.0", text.strip() + "\n")
        self.output_text.configure(state=tk.DISABLED)
        self.output_text.see(tk.END)

    def run_mipmap(self) -> None:
        input_dir = Path(self.input_var.get().strip()).expanduser()
        output_dir = Path(self.output_var.get().strip()).expanduser()
        if not self.mipmap_script.is_file():
            messagebox.showerror("Mipmap tool missing", f"mipmap.py not found:\n{self.mipmap_script}", parent=self)
            return
        if not input_dir.is_dir():
            messagebox.showerror("Invalid input folder", f"Input folder not found:\n{input_dir}", parent=self)
            return
        if not output_dir.is_dir():
            messagebox.showerror("Invalid output folder", f"Output folder not found:\n{output_dir}", parent=self)
            return
        try:
            levels = int(self.levels_var.get())
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror("Invalid mipmap count", "Mipmaps must be a positive whole number.", parent=self)
            return
        if levels < 1:
            messagebox.showerror("Invalid mipmap count", "Mipmaps must be at least 1.", parent=self)
            return

        args = [str(levels), "--in", str(input_dir), "--out", str(output_dir)]
        if self.crop_var.get():
            args.append("--crop")
        if self.verbose_var.get():
            args.append("--verbose")
        cmd = [sys.executable, str(self.mipmap_script), *args]
        mod_root = find_mod_root(input_dir) or find_mod_root(output_dir) or TOOL_DIR
        display_command = " ".join(["python", self.mipmap_script.name, *args])

        def work() -> subprocess.CompletedProcess[str]:
            return subprocess.run(cmd, cwd=str(mod_root), text=True, capture_output=True, check=False)  # noqa: S603 - local tool command.

        def done(result: subprocess.CompletedProcess[str]) -> None:
            output = (result.stdout + "\n" + result.stderr).strip()
            self.append_output("\n".join([f"> {display_command}", "", output or "<no output>", "", f"Exit code: {result.returncode}"]))
            if result.returncode == 0:
                self.status_var.set("Mipmaps generated successfully.")
                self.app.status("Mipmaps generated")
            else:
                self.status_var.set(f"Mipmap generation failed with exit code {result.returncode}.")
                self.app.status("Mipmap generation failed")

        self.status_var.set("Generating mipmaps...")
        self.app.run_worker(work, done, "Generating mipmaps...", "Mipmap generation failed")


class DeployTool(ToolFrame):
    """Tab for building and publishing filtered mod release archives."""

    title = "Deploy"

    def __init__(self, master: tk.Misc, app: ToolApp):
        super().__init__(master, app)
        self.branch_var = tk.StringVar(value="main")
        self.mod_root_var = tk.StringVar(value=str(app.mod_root or ""))
        self.mod_version_var = tk.StringVar()
        self.mod_root_labels: dict[str, Path] = {}
        self.build_no_public_var = tk.BooleanVar(value=False)
        self.build_no_strip_var = tk.BooleanVar(value=False)
        self.build_first_var = tk.BooleanVar(value=False)
        self.publish_dry_run_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready.")
        self.deploy_script = TOOL_DIR / "deploy.py"
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        header.columnconfigure(1, weight=1)
        tk.Label(header, text="DEPLOY", bg=BG, fg=TEXT, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        striped_header(header, "", BG).grid(row=0, column=1, sticky="ew", padx=12)

        info = "Builds filtered release archives for the selected mod root. It does not copy files into Factorio/mods."
        tk.Label(self, text=info, bg=BG, fg=BODY_TEXT, anchor="w", justify=tk.LEFT).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))

        body = tk.Frame(self, bg=BG)
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=460)
        body.rowconfigure(0, weight=1)
        body.rowconfigure(1, weight=0)

        info_frame = self._panel(body)
        info_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        info_frame.columnconfigure(0, weight=1)
        info_frame.rowconfigure(1, weight=1)
        striped_header(info_frame, "Selected Mod", PANEL).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        self.info_text = tk.Text(info_frame, bg=LIST_BG, fg=BODY_TEXT, insertbackground=BODY_TEXT, relief=tk.SUNKEN, bd=2, wrap=tk.WORD, height=12)
        self.info_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.info_text.configure(state=tk.DISABLED)

        log_frame = self._panel(body)
        log_frame.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        striped_header(log_frame, "Output", PANEL).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        self.output_text = tk.Text(log_frame, bg=LIST_BG, fg=BODY_TEXT, insertbackground=BODY_TEXT, relief=tk.SUNKEN, bd=2, wrap=tk.WORD, height=8)
        self.output_text.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.append_output("Deploy output will appear here.")

        controls = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        controls.grid(row=0, column=1, rowspan=2, sticky="nsew")
        controls.columnconfigure(0, weight=1)
        tk.Label(controls, text="MOD ROOT", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        mod_root_row = tk.Frame(controls, bg=PANEL)
        mod_root_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        mod_root_row.columnconfigure(0, weight=1)
        self.mod_root_combo = ttk.Combobox(mod_root_row, textvariable=self.mod_root_var, state="readonly", height=18)
        self.mod_root_combo.grid(row=0, column=0, sticky="ew")
        self.mod_root_combo.bind("<<ComboboxSelected>>", self.on_mod_root_selected)

        version_row = tk.Frame(controls, bg=PANEL)
        version_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        version_row.columnconfigure(1, weight=1)
        tk.Label(version_row, text="Version", bg=PANEL, fg=BODY_TEXT, width=10, anchor="w").grid(row=0, column=0, sticky="w")
        ttk.Entry(version_row, textvariable=self.mod_version_var, width=16).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        factorio_button(version_row, "Save", self.save_selected_version, kind="orange").grid(row=0, column=2, sticky="e")

        tk.Label(controls, text="COMMANDS", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").grid(row=3, column=0, sticky="ew", padx=12, pady=(12, 6))

        self.refresh_mod_roots()
        self.command_row(
            controls,
            4,
            "Build",
            ["build"],
            options=[
                ("no public", self.build_no_public_var),
                ("keep debug", self.build_no_strip_var),
            ],
        )
        maintenance_row = tk.Frame(controls, bg=PANEL)
        maintenance_row.grid(row=5, column=0, sticky="ew", padx=12, pady=6)
        maintenance_row.columnconfigure(0, weight=1)
        maintenance_row.columnconfigure(1, weight=1)
        self.command_button(maintenance_row, "Gitignore", ["ensure-gitignore"]).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.command_button(maintenance_row, "Clean", ["clean"]).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        publish_frame = self.command_row(
            controls,
            6,
            "Publish",
            [],
            publish=True,
            options=[
                ("build first", self.build_first_var),
                ("dry run", self.publish_dry_run_var),
            ],
        )
        branch_row = tk.Frame(publish_frame, bg=PANEL)
        branch_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        branch_row.columnconfigure(1, weight=1)
        tk.Label(branch_row, text="Branch", bg=PANEL, fg=BODY_TEXT, width=10, anchor="w").pack(side=tk.LEFT)
        ttk.Entry(branch_row, textvariable=self.branch_var, width=20).pack(side=tk.LEFT, fill="x", expand=True)

        tk.Label(self, textvariable=self.status_var, bg=BG, fg=MUTED, anchor="w").grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        self.update_info_preview()

    def _panel(self, master: tk.Misc) -> tk.Frame:
        return tk.Frame(master, bg=PANEL, relief=tk.SUNKEN, bd=2, highlightthickness=1, highlightbackground=LINE)

    def mod_root_label(self, path: Path) -> str:
        """Return a readable label for a deployable mod root."""
        try:
            info = json.loads((path / "info.json").read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return str(path)
        name = info.get("name") or path.name
        title = info.get("title") or name
        return f"{title} ({name})"

    def discover_mod_roots(self) -> list[Path]:
        """Find deployable mod roots from the configured Factorio mods directory."""
        roots: list[Path] = []
        current = Path(self.mod_root_var.get()).expanduser() if self.mod_root_var.get().strip() else self.app.mod_root
        if current is not None and (current / "info.json").is_file():
            roots.append(current)

        factorio_exe = self.app.factorio_exe or modlist.DEFAULT_FACTORIO
        mods_dir = modlist.default_mods_dir(factorio_exe)
        if mods_dir.exists():
            for path in mods_dir.iterdir():
                if path.is_dir() and (path / "info.json").is_file():
                    roots.append(path)

        unique: dict[str, Path] = {}
        for root in roots:
            unique[str(root.resolve()).lower()] = root.resolve()
        return sorted(unique.values(), key=lambda path: self.mod_root_label(path).lower())

    def refresh_mod_roots(self) -> None:
        """Refresh the deploy target dropdown and preserve the current selection when possible."""
        roots = self.discover_mod_roots()
        self.mod_root_labels = {self.mod_root_label(root): root for root in roots}
        labels = list(self.mod_root_labels)
        self.mod_root_combo.configure(values=labels)

        current = Path(self.mod_root_var.get()).expanduser().resolve() if self.mod_root_var.get().strip() else None
        for label, root in self.mod_root_labels.items():
            if current is not None and root == current:
                self.mod_root_var.set(label)
                return
        if labels:
            self.mod_root_var.set(labels[0])
        self.update_info_preview()

    def selected_mod_root(self) -> Path | None:
        """Return the currently selected deploy target mod root."""
        value = self.mod_root_var.get().strip()
        if value in self.mod_root_labels:
            return self.mod_root_labels[value]
        if value:
            path = Path(value).expanduser()
            if (path / "info.json").is_file():
                return path.resolve()
        return None

    def on_mod_root_selected(self, _event: tk.Event | None = None) -> None:
        """Persist the selected deploy target."""
        mod_root = self.selected_mod_root()
        if mod_root is not None:
            self.app.save_tool_config(mod_root=mod_root)
        self.update_info_preview()

    def selected_info(self) -> dict[str, Any] | None:
        """Read info.json for the selected deploy target."""
        mod_root = self.selected_mod_root()
        if mod_root is None:
            return None
        try:
            data = json.loads((mod_root / "info.json").read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def update_info_preview(self) -> None:
        """Render selected mod info.json and mirror its version into the version entry."""
        mod_root = self.selected_mod_root()
        info = self.selected_info()
        if info is None:
            self.mod_version_var.set("")
            preview = "No valid info.json found for the selected mod root."
        else:
            version = str(info.get("version") or "")
            self.mod_version_var.set(version)
            preview = json.dumps(info, indent=2, ensure_ascii=False)
            if mod_root is not None:
                preview = f"{mod_root}\n\n{preview}"

        self.info_text.configure(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        self.info_text.insert("1.0", preview)
        self.info_text.configure(state=tk.DISABLED)

    def save_selected_version(self) -> None:
        """Update the selected mod's info.json version field."""
        mod_root = self.selected_mod_root()
        info = self.selected_info()
        version = self.mod_version_var.get().strip()
        if mod_root is None or info is None:
            messagebox.showerror("No mod selected", "Select a mod with a valid info.json first.", parent=self)
            return
        if not version:
            messagebox.showerror("Version missing", "Enter a version first.", parent=self)
            return
        info["version"] = version
        (mod_root / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.update_info_preview()
        self.status_var.set(f"Updated version to {version}.")
        self.app.status(f"Updated {mod_root.name} version to {version}")

    def command_row(
        self,
        master: tk.Misc,
        row: int,
        text: str,
        args: list[str],
        publish: bool = False,
        options: list[tuple[str, tk.BooleanVar]] | None = None,
    ) -> tk.Frame:
        frame = tk.Frame(master, bg=PANEL)
        frame.grid(row=row, column=0, sticky="ew", padx=12, pady=6)
        frame.columnconfigure(1, weight=1)
        self.command_button(frame, text, args, publish=publish).grid(row=0, column=0, sticky="ew")
        option_frame = tk.Frame(frame, bg=PANEL)
        option_frame.grid(row=0, column=1, sticky="w", padx=(8, 0))
        for label, variable in options or []:
            ttk.Checkbutton(option_frame, text=label, variable=variable).pack(side=tk.LEFT, padx=(0, 8))
        return frame

    def command_button(self, master: tk.Misc, text: str, args: list[str], publish: bool = False) -> tk.Button:
        return tk.Button(
            master,
            text=text,
            bg=ORANGE,
            fg=BUTTON_TEXT,
            activebackground=ORANGE_HOVER,
            activeforeground=BUTTON_TEXT,
            relief=tk.RAISED,
            bd=3,
            padx=12,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            command=lambda: self.run_publish() if publish else self.run_deploy(args),
        )

    def append_output(self, text: str) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert("1.0", text.strip() + "\n")
        self.output_text.configure(state=tk.DISABLED)
        self.output_text.see(tk.END)

    def deploy_command(self, args: list[str]) -> list[str]:
        command = [sys.executable, str(self.deploy_script), *args]
        mod_root = self.selected_mod_root()
        if mod_root is not None:
            command.extend(["--mod-root", str(mod_root)])
        return command

    def run_deploy(self, args: list[str]) -> None:
        args = list(args)
        if args and args[0] in {"build", "check"} and self.build_no_strip_var.get():
            args.append("--no-strip-debug")
        if args and args[0] == "build" and self.build_no_public_var.get():
            args.append("--no-public")
        cmd = self.deploy_command(args)
        mod_root = self.selected_mod_root()
        if mod_root is not None:
            self.app.save_tool_config(mod_root=mod_root)

        def work() -> subprocess.CompletedProcess[str]:
            return subprocess.run(cmd, cwd=str(TOOL_DIR), text=True, capture_output=True, check=False)  # noqa: S603 - local tool command.

        def done(result: subprocess.CompletedProcess[str]) -> None:
            output = (result.stdout + "\n" + result.stderr).strip()
            self.append_output(output or "<no output>")
            if result.returncode == 0:
                self.status_var.set("Command completed.")
                self.app.status("Deploy command completed")
            else:
                self.status_var.set(f"Command failed with exit code {result.returncode}.")
                self.app.status("Deploy command failed")

        self.status_var.set("Running: " + " ".join(args))
        self.app.run_worker(work, done, "Running deploy command...", "Deploy command failed")

    def run_publish(self) -> None:
        branch = self.branch_var.get().strip() or "main"
        args = ["publish-public", "--branch", branch]
        if self.build_first_var.get():
            args.append("--build-first")
        if self.publish_dry_run_var.get():
            args.append("--dry-run")
        self.run_deploy(args)


class DebugTool(ToolFrame):
    """Tab for launching common Factorio test-harness commands."""

    title = "Debug"

    def __init__(self, master: tk.Misc, app: ToolApp):
        super().__init__(master, app)
        self.no_color_var = tk.BooleanVar(value=True)
        self.show_passes_var = tk.BooleanVar(value=False)
        self.keep_mod_list_var = tk.BooleanVar(value=False)
        self.keep_saves_var = tk.BooleanVar(value=False)
        self.factorio_verbose_var = tk.BooleanVar(value=False)
        self.check_unused_var = tk.BooleanVar(value=False)
        self.strict_warnings_var = tk.BooleanVar(value=False)
        self.custom_args_var = tk.StringVar(value="--profile default")
        self.mod_root_var = tk.StringVar(value=str(app.mod_root or ""))
        self.status_var = tk.StringVar(value="Ready.")
        self.run_tests_script = self.find_run_tests_script()
        self.test_commands: list[tuple[str, list[str]]] = [
            ("Default", ["--profile", "default"]),
            ("All profiles", ["--all"]),
            ("Material heavy", ["--profile", "ancestry_material_heavy"]),
            ("Base IS", ["--mod-profile", "is_base", "--profile", "default"]),
            ("K2 default", ["--mod-profile", "krastorio_is", "--profile", "default"]),
            ("Bob+Angels", ["--mod-profile", "bob_angels_full_is", "--profile", "default"]),
            ("Bob+Angels width 1", ["--mod-profile", "bob_angels_full_is", "--profile", "ancestry_width_1"]),
        ]
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        header.columnconfigure(1, weight=1)
        tk.Label(header, text="DEBUG", bg=BG, fg=TEXT, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        striped_header(header, "", BG).grid(row=0, column=1, sticky="ew", padx=12)

        info = "Runs the real Factorio test harness from this workspace. Use these buttons when Codex asks for a local test run."
        tk.Label(self, text=info, bg=BG, fg=BODY_TEXT, anchor="w", justify=tk.LEFT).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))

        body = tk.Frame(self, bg=BG)
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=360)
        body.rowconfigure(0, weight=1)

        log_frame = tk.Frame(body, bg=BG)
        log_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.output_text = tk.Text(log_frame, bg=LIST_BG, fg=BODY_TEXT, insertbackground=BODY_TEXT, relief=tk.SUNKEN, bd=2, wrap=tk.WORD)
        self.output_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.output_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.output_text.configure(yscrollcommand=scrollbar.set)
        self.append_output("Debug output will appear here.")

        controls = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        controls.grid(row=0, column=1, sticky="nsew")
        controls.columnconfigure(0, weight=1)
        tk.Label(controls, text="TEST COMMANDS", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        for row, (label, args) in enumerate(self.test_commands, start=1):
            self.command_row(controls, row, label, args)

        option_row = len(self.test_commands) + 1
        tk.Label(controls, text="MOD ROOT", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").grid(row=option_row, column=0, sticky="ew", padx=12, pady=(14, 4))
        mod_root_row = tk.Frame(controls, bg=PANEL)
        mod_root_row.grid(row=option_row + 1, column=0, sticky="ew", padx=12)
        mod_root_row.columnconfigure(0, weight=1)
        ttk.Entry(mod_root_row, textvariable=self.mod_root_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        factorio_button(mod_root_row, "Browse", self.choose_mod_root).grid(row=0, column=1, sticky="e")

        option_row = option_row + 2
        tk.Label(controls, text="OPTIONS", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").grid(row=option_row, column=0, sticky="ew", padx=12, pady=(14, 4))
        options = tk.Frame(controls, bg=PANEL)
        options.grid(row=option_row + 1, column=0, sticky="ew", padx=12)
        for index, (label, variable) in enumerate([
            ("no color", self.no_color_var),
            ("show passes", self.show_passes_var),
            ("keep mod list", self.keep_mod_list_var),
            ("keep saves", self.keep_saves_var),
            ("verbose Factorio", self.factorio_verbose_var),
            ("unused prototype check", self.check_unused_var),
            ("strict warnings", self.strict_warnings_var),
        ]):
            ttk.Checkbutton(options, text=label, variable=variable).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 12), pady=2)

        custom_row = option_row + 2
        tk.Label(controls, text="CUSTOM ARGS", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").grid(row=custom_row, column=0, sticky="ew", padx=12, pady=(14, 4))
        custom = tk.Frame(controls, bg=PANEL)
        custom.grid(row=custom_row + 1, column=0, sticky="ew", padx=12, pady=(0, 8))
        custom.columnconfigure(0, weight=1)
        ttk.Entry(custom, textvariable=self.custom_args_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        factorio_button(custom, "Run", self.run_custom, kind="orange").grid(row=0, column=1, sticky="e")

        tk.Label(self, textvariable=self.status_var, bg=BG, fg=MUTED, anchor="w").grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))

    def find_run_tests_script(self) -> Path:
        for candidate in (
            TOOL_DIR.parent / "testharness" / "run_tests.py",
            TOOL_DIR.parent / "test" / "run_tests.py",
        ):
            if candidate.exists():
                return candidate
        return TOOL_DIR.parent / "testharness" / "run_tests.py"

    def choose_mod_root(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose Factorio mod root",
            initialdir=str(Path(self.mod_root_var.get()).parent if self.mod_root_var.get() else TOOL_DIR.parent),
            parent=self,
        )
        if not selected:
            return
        self.mod_root_var.set(selected)
        self.app.save_tool_config(mod_root=selected)

    def command_row(self, master: tk.Misc, row: int, text: str, args: list[str]) -> None:
        frame = tk.Frame(master, bg=PANEL)
        frame.grid(row=row, column=0, sticky="ew", padx=12, pady=4)
        frame.columnconfigure(0, weight=1)
        factorio_button(frame, text, lambda command_args=args: self.run_tests(command_args), kind="orange").grid(row=0, column=0, sticky="ew")

    def append_output(self, text: str) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert("1.0", text.strip() + "\n")
        self.output_text.configure(state=tk.DISABLED)
        self.output_text.see(tk.END)

    def option_args(self) -> list[str]:
        args: list[str] = []
        if self.no_color_var.get():
            args.append("--no-color")
        if self.show_passes_var.get():
            args.append("--show-passes")
        if self.keep_mod_list_var.get():
            args.append("--keep-mod-list")
        if self.keep_saves_var.get():
            args.append("--keep-saves")
        if self.factorio_verbose_var.get():
            args.append("--factorio-verbose")
        if self.check_unused_var.get():
            args.append("--check-unused-prototype-data")
        if self.strict_warnings_var.get():
            args.append("--strict-prototype-warnings")
        return args

    def run_custom(self) -> None:
        try:
            import shlex

            args = shlex.split(self.custom_args_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid custom args", str(exc), parent=self)
            return
        self.run_tests(args)

    def run_tests(self, args: list[str]) -> None:
        if not self.run_tests_script.exists():
            messagebox.showerror("Test harness missing", f"run_tests.py not found:\n{self.run_tests_script}", parent=self)
            return
        final_args = list(args) + self.option_args()
        mod_root = self.mod_root_var.get().strip()
        if mod_root:
            final_args = ["--mod-root", mod_root, *final_args]
            self.app.save_tool_config(mod_root=mod_root)
        cmd = [sys.executable, str(self.run_tests_script), *final_args]
        display_command = " ".join(["python", str(self.run_tests_script), *final_args])

        def work() -> subprocess.CompletedProcess[str]:
            cwd = mod_root or str(TOOL_DIR.parent)
            return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)  # noqa: S603 - local tool command.

        def done(result: subprocess.CompletedProcess[str]) -> None:
            parts = [f"> {display_command}", ""]
            output = (result.stdout + "\n" + result.stderr).strip()
            parts.append(output or "<no output>")
            parts.extend(["", f"Exit code: {result.returncode}"])
            self.append_output("\n".join(parts))
            if result.returncode == 0:
                self.status_var.set("Test command completed.")
                self.app.status("Debug test command completed")
            else:
                self.status_var.set(f"Test command failed with exit code {result.returncode}.")
                self.app.status("Debug test command failed")

        self.status_var.set("Running: " + display_command)
        self.app.run_worker(work, done, "Running test harness...", "Test command failed")


TOOL_MAP: dict[str, dict[str, Any]] = {
    "modlist": {
        "title": "Mod List",
        "version": getattr(modlist, "APP_VERSION", None),
        "filename": "modlist.py",
        "frame_type": ModListTool,
        "min_version": "1.0.0",
        "max_version": "2.0.0",
    },
    "settings": {
        "title": "Settings",
        "version": getattr(settings, "APP_VERSION", None),
        "filename": "settings.py",
        "frame_type": SettingsTool,
        "min_version": "1.0.0",
        "max_version": "2.0.0",
    },
    "material_flow": {
        "title": "Material Flow",
        "version": "1.0.0",
        "filename": "json-tree-viewer.html",
        "frame_type": MaterialFlowTool,
        "min_version": "1.0.0",
        "max_version": "2.0.0",
    },
    "deploy": {
        "title": "Deploy",
        "version": module_version("deploy.py"),
        "filename": "deploy.py",
        "frame_type": DeployTool,
        "min_version": "2.0.0",
        "max_version": "3.0.0",
    },
    "mipmap": {
        "title": "Mipmaps",
        "version": module_version("mipmap.py"),
        "filename": "mipmap.py",
        "frame_type": MipmapTool,
        "min_version": "1.2.0",
        "max_version": "2.0.0",
    },
    "debug": {
        "title": "Debug",
        "version": "1.0.0",
        "filename": "../test/run_tests.py",
        "frame_type": DebugTool,
        "min_version": "1.0.0",
        "max_version": "2.0.0",
    },
}

register_tools_from_map(TOOL_MAP)


def run(factorio_exe: Path | None = None, profiles_json: Path | None = None) -> None:
    """Launch the Tk application."""
    app = ToolApp(factorio_exe=factorio_exe, profiles_json=profiles_json)
    app.mainloop()


def main() -> int:
    """CLI wrapper for launching the UI or printing version information."""
    parser = argparse.ArgumentParser(
        description="Open the Factorio Toolset UI.",
        epilog=f"Factorio Toolset: {GITHUB_URL}",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--check-versions", action="store_true", help="print UI/tool compatibility and exit")
    args = parser.parse_args()
    if args.check_versions:
        print("\n".join(tool_compatibility_report()))
        return 0
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
