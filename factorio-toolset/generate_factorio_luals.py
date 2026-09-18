"""
Generate LuaLS/EmmyLua annotations from the local Factorio API documentation.

The generated files are intended for editor support only. They are not loaded by
Factorio and are safe to keep under .vscode/factorio-types.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_DOCS = Path(r"F:/Games/Factorio_ModTest/doc-html")
DEFAULT_OUT = Path(".vscode/factorio-types")

BUILTIN_TYPES = {
    "bool": "boolean",
    "boolean": "boolean",
    "double": "number",
    "float": "number",
    "int": "integer",
    "int8": "integer",
    "int16": "integer",
    "int32": "integer",
    "int64": "integer",
    "uint": "integer",
    "uint8": "integer",
    "uint16": "integer",
    "uint32": "integer",
    "uint64": "integer",
    "string": "string",
    "nil": "nil",
    "table": "table",
}

TYPE_ALIASES = {
    "LocalisedString": "string|number|boolean|nil|table",
}

LUA_KEYWORDS = {
    "and",
    "break",
    "do",
    "else",
    "elseif",
    "end",
    "false",
    "for",
    "function",
    "goto",
    "if",
    "in",
    "local",
    "nil",
    "not",
    "or",
    "repeat",
    "return",
    "then",
    "true",
    "until",
    "while",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sanitize_identifier(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not clean or clean[0].isdigit():
        clean = "_" + clean
    return clean


def field_name(name: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name) and name not in LUA_KEYWORDS:
        return name
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'["{escaped}"]'


def literal_type(value: Any) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "nil"
    if isinstance(value, (int, float)):
        return str(value)
    return "any"


def type_name(type_data: Any) -> str:
    if type_data is None:
        return "any"
    if isinstance(type_data, str):
        if type_data in TYPE_ALIASES:
            return TYPE_ALIASES[type_data]
        return BUILTIN_TYPES.get(type_data, type_data)
    if not isinstance(type_data, dict):
        return "any"

    complex_type = type_data.get("complex_type")
    if complex_type == "literal":
        return literal_type(type_data.get("value"))
    if complex_type == "array":
        return f"{type_name(type_data.get('value'))}[]"
    if complex_type in {"dictionary", "LuaCustomTable"}:
        return f"table<{type_name(type_data.get('key'))}, {type_name(type_data.get('value'))}>"
    if complex_type == "LuaLazyLoadedValue":
        return type_name(type_data.get("value"))
    if complex_type == "tuple":
        values = [type_name(value) for value in type_data.get("values", [])]
        return f"[{', '.join(values)}]" if values else "table"
    if complex_type == "union":
        options = [type_name(option) for option in type_data.get("options", [])]
        return "|".join(options) if options else "any"
    if complex_type == "type":
        return type_name(type_data.get("value"))
    if complex_type == "table":
        return "table"
    if complex_type == "struct":
        return "table"
    if complex_type == "function":
        params = type_data.get("parameters", [])
        param_types = ", ".join(type_name(param) for param in params)
        return f"fun({param_types})"
    if complex_type == "LuaStruct":
        return "table"
    if complex_type == "builtin":
        return "any"
    return "any"


def doc_lines(text: str | None, indent: str = "") -> list[str]:
    if not text:
        return []

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("```", "`")
    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            lines.append(f"{indent}---")
        else:
            lines.append(f"{indent}---{line}")
    while lines and lines[-1] == f"{indent}---":
        lines.pop()
    return lines


def field_line(name: str, field_type: str, optional: bool, description: str | None = None) -> list[str]:
    suffix = "?" if optional else ""
    lines = doc_lines(description)
    lines.append(f"---@field {field_name(name)}{suffix} {field_type}")
    return lines


def class_header(name: str, description: str | None, parent: str | None = None) -> list[str]:
    lines = doc_lines(description)
    parent_part = f" : {parent}" if parent else ""
    lines.append(f"---@class {name}{parent_part}")
    return lines


def alias_header(name: str, description: str | None, alias_type: str) -> list[str]:
    lines = doc_lines(description)
    lines.append(f"---@alias {name} {alias_type}")
    return lines


def method_type(method: dict[str, Any]) -> str:
    params = []
    for parameter in sorted(method.get("parameters", []), key=lambda item: item.get("order", 0)):
        param_name = sanitize_identifier(parameter["name"])
        param_type = type_name(parameter.get("type"))
        params.append(f"{param_name}: {param_type}")

    returns = [type_name(value.get("type")) for value in sorted(method.get("return_values", []), key=lambda item: item.get("order", 0))]
    return_part = ""
    if returns:
        return_part = ": " + (returns[0] if len(returns) == 1 else ", ".join(returns))
    return f"fun({', '.join(params)}){return_part}"


def emit_prototype_types(api: dict[str, Any]) -> str:
    out: list[str] = [
        "---@meta",
        "---Generated from Factorio prototype-api.json.",
        f"---Factorio {api.get('application_version', 'unknown')} / API {api.get('api_version', 'unknown')}",
        "",
    ]

    all_types = sorted(api.get("types", []) + api.get("prototypes", []), key=lambda item: item["name"])
    for item in all_types:
        name = item["name"]
        if name in TYPE_ALIASES:
            out.extend(alias_header(name, item.get("description"), TYPE_ALIASES[name]))
            out.append("")
            continue
        parent = item.get("parent")
        out.extend(class_header(name, item.get("description"), parent))
        if item.get("typename"):
            out.append(f"---@field type \"{item['typename']}\"")
        for prop in sorted(item.get("properties", []), key=lambda field: (field.get("order", 0), field["name"])):
            prop_name = prop["name"]
            out.extend(field_line(prop_name, type_name(prop.get("type")), bool(prop.get("optional")), prop.get("description")))
        out.append("")

    raw_fields = []
    for prototype in sorted(api.get("prototypes", []), key=lambda item: item.get("typename") or item["name"]):
        typename = prototype.get("typename")
        if typename:
            raw_fields.extend(field_line(typename, f"table<string, {prototype['name']}>", False, prototype.get("description")))

    out.extend(class_header("FactorioDataRaw", "The data.raw prototype tables indexed by prototype type and prototype name."))
    out.extend(raw_fields)
    out.append("")
    out.extend(class_header("FactorioData", "The Factorio data-stage interface used to register prototypes."))
    out.append("---@field raw FactorioDataRaw")
    out.append("---@field extend fun(self: FactorioData, prototypes: table[])")
    out.append("")
    out.append("---@type FactorioData")
    out.append("data = data")
    out.append("")
    out.append("---@type table<string, string>")
    out.append("mods = mods")
    out.append("")
    out.append("---@type table")
    out.append("settings = settings")
    out.append("")
    return "\n".join(out)


def emit_runtime_types(api: dict[str, Any]) -> str:
    out: list[str] = [
        "---@meta",
        "---Generated from Factorio runtime-api.json.",
        f"---Factorio {api.get('application_version', 'unknown')} / API {api.get('api_version', 'unknown')}",
        "",
    ]

    for concept in sorted(api.get("concepts", []), key=lambda item: item["name"]):
        if concept["name"] in TYPE_ALIASES:
            out.extend(alias_header(concept["name"], concept.get("description"), TYPE_ALIASES[concept["name"]]))
            out.append("")
            continue
        out.extend(class_header(concept["name"], concept.get("description")))
        out.append(f"---@field value {type_name(concept.get('type'))}")
        out.append("")

    for cls in sorted(api.get("classes", []), key=lambda item: item["name"]):
        out.extend(class_header(cls["name"], cls.get("description")))
        for attr in sorted(cls.get("attributes", []), key=lambda item: (item.get("order", 0), item["name"])):
            out.extend(field_line(attr["name"], type_name(attr.get("read_type") or attr.get("write_type")), bool(attr.get("optional")), attr.get("description")))
        for method in sorted(cls.get("methods", []), key=lambda item: (item.get("order", 0), item["name"])):
            out.extend(field_line(method["name"], method_type(method), False, method.get("description")))
        out.append("")

    for global_object in sorted(api.get("global_objects", []), key=lambda item: item["name"]):
        out.extend(doc_lines(global_object.get("description")))
        out.append(f"---@type {type_name(global_object.get('type'))}")
        out.append(f"{global_object['name']} = {global_object['name']}")
        out.append("")

    for function_data in sorted(api.get("global_functions", []), key=lambda item: item["name"]):
        out.extend(doc_lines(function_data.get("description")))
        for parameter in sorted(function_data.get("parameters", []), key=lambda item: item.get("order", 0)):
            out.append(f"---@param {sanitize_identifier(parameter['name'])} {type_name(parameter.get('type'))}")
        returns = sorted(function_data.get("return_values", []), key=lambda item: item.get("order", 0))
        for value in returns:
            out.append(f"---@return {type_name(value.get('type'))}")
        params = ", ".join(sanitize_identifier(parameter["name"]) for parameter in sorted(function_data.get("parameters", []), key=lambda item: item.get("order", 0)))
        out.append(f"function {function_data['name']}({params}) end")
        out.append("")

    return "\n".join(out)


def write_luarc(path: Path, type_dir: Path) -> None:
    library = str(type_dir).replace("\\", "/")
    config = {
        "runtime.version": "Lua 5.2",
        "workspace.library": [library],
        "diagnostics.globals": [
            "data",
            "settings",
            "mods",
            "script",
            "game",
            "helpers",
            "prototypes",
            "commands",
            "remote",
            "rcon",
            "rendering",
            "storage",
            "serpent",
        ],
    }
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate LuaLS annotations from local Factorio API docs.")
    parser.add_argument("--docs", type=Path, default=DEFAULT_DOCS, help="Path containing prototype-api.json and runtime-api.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory for generated Lua annotation files")
    parser.add_argument("--luarc", type=Path, default=Path(".luarc.json"), help="LuaLS config file to write")
    args = parser.parse_args()

    prototype_api = load_json(args.docs / "prototype-api.json")
    runtime_api = load_json(args.docs / "runtime-api.json")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "factorio-prototype.lua").write_text(emit_prototype_types(prototype_api), encoding="utf-8")
    (args.out / "factorio-runtime.lua").write_text(emit_runtime_types(runtime_api), encoding="utf-8")
    write_luarc(args.luarc, args.out)

    print(f"Generated LuaLS annotations in {args.out}")
    print(f"Wrote LuaLS config to {args.luarc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
