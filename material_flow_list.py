"""
Build human-reviewable material flow target lists from material-flow.json.

The text output is intentionally simple:
    ingredient -> recipe -> result

The JSON output keeps the same information in a compact structured form so
future comparison/tuning tools can consume it.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import modlist

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "target-lists"


def default_flow_path(factorio_exe: Path | None = None) -> Path:
    if factorio_exe is None:
        tool_config = modlist.load_tool_config()
        factorio_exe = modlist.config_path_value(tool_config, "factorio", modlist.DEFAULT_FACTORIO)
    root = modlist.factorio_root(factorio_exe or modlist.DEFAULT_FACTORIO)
    return root / "script-output" / "Ingredient_Scrap" / "material-flow.json"


def result_label(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return "?"
    result_type = result.get("type") or "item"
    name = result.get("name") or "?"
    amount = result.get("amount")
    if amount is None:
      amount = result.get("amount_min")
    suffix = f" x{amount:g}" if isinstance(amount, (int, float)) else ""
    return f"{result_type}:{name}{suffix}"


def recipe_result_labels(recipe: dict[str, Any] | None) -> list[str]:
    if not isinstance(recipe, dict):
        return ["?"]
    labels = []
    for result in recipe.get("results") or []:
        if isinstance(result, dict):
            labels.append(result_label(result))
    return labels or ["?"]


def input_label(flow: dict[str, Any]) -> str:
    input_data = flow.get("input") or {}
    input_type = input_data.get("type") or "item"
    name = input_data.get("name") or "?"
    amount = input_data.get("amount")
    suffix = f" x{amount:g}" if isinstance(amount, (int, float)) else ""
    return f"{input_type}:{name}{suffix}"


def recycle_label(recipe: dict[str, Any]) -> str:
    result = recipe.get("result")
    hidden = " hidden" if recipe.get("hidden") else ""
    category = recipe.get("category") or "?"
    return f"{recipe.get('recipe', '?')} [{category}{hidden}] -> {result_label(result)}"


def icon_path(prototype: dict[str, Any] | None) -> str | None:
    if not isinstance(prototype, dict):
        return None
    icon = prototype.get("icon")
    if isinstance(icon, dict):
        return icon.get("path")
    return None


def build_list(flow_data: dict[str, Any], profile: str) -> dict[str, Any]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    source_seen: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)

    for flow in flow_data.get("flows") or []:
        if not isinstance(flow, dict):
            continue
        material = str(flow.get("material") or "?")
        mode = str(flow.get("mode") or "?")
        key = (material, mode)
        source_recipe = flow.get("source_recipe") or {}
        recipe_name = str(source_recipe.get("name") or "?")
        input_name = input_label(flow)
        results = recipe_result_labels(source_recipe)
        result_text = ", ".join(results)
        source_key = (input_name, recipe_name, result_text)

        group = groups.setdefault(key, {
            "material": material,
            "mode": mode,
            "resources": [],
            "sources": [],
            "recycle_targets": [],
        })

        if source_key not in source_seen[key]:
            source_seen[key].add(source_key)
            group["sources"].append({
                "ingredient": input_name,
                "recipe": recipe_name,
                "results": results,
                "main_product": source_recipe.get("main_product"),
                "recipe_category": source_recipe.get("category"),
                "ingredient_icon": icon_path((flow.get("input") or {}).get("prototype")),
            })

        existing_recycles = {item["recipe"] for item in group["recycle_targets"]}
        for recycle in flow.get("recycle_recipes") or []:
            if isinstance(recycle, dict) and recycle.get("recipe") not in existing_recycles:
                existing_recycles.add(recycle.get("recipe"))
                group["recycle_targets"].append({
                    "recipe": recycle.get("recipe"),
                    "category": recycle.get("category"),
                    "hidden": bool(recycle.get("hidden")),
                    "result": result_label(recycle.get("result")),
                    "result_icon": icon_path((recycle.get("result") or {}).get("prototype")),
                })

        existing_resources = {item["resource"] for item in group["resources"]}
        for resource in flow.get("resource_results") or []:
            if isinstance(resource, dict) and resource.get("resource") not in existing_resources:
                existing_resources.add(resource.get("resource"))
                group["resources"].append({
                    "resource": resource.get("resource"),
                    "result": result_label(resource.get("result")),
                    "category": resource.get("category"),
                    "icon": icon_path(resource.get("result_prototype")),
                })

    materials = sorted(groups.values(), key=lambda item: (item["mode"], item["material"]))
    for material in materials:
        material["sources"].sort(key=lambda item: (item["ingredient"], item["recipe"]))
        material["recycle_targets"].sort(key=lambda item: item["recipe"] or "")
        material["resources"].sort(key=lambda item: item["resource"] or "")

    return {
        "schema": "ingredient-scrap-target-list/v1",
        "profile": profile,
        "materials": materials,
    }


def write_text(target_list: dict[str, Any], path: Path) -> None:
    lines = [
        f"Ingredient Scrap Target List: {target_list['profile']}",
        "=" * 80,
        "",
    ]

    for material in target_list["materials"]:
        lines.append(f"[{material['mode']}] {material['material']}")
        if material["resources"]:
            lines.append("  resources:")
            for resource in material["resources"]:
                lines.append(f"    {resource['resource']} -> {resource['result']}")
        if material["recycle_targets"]:
            lines.append("  recycle targets:")
            for recycle in material["recycle_targets"]:
                lines.append(f"    {recycle['recipe']} -> {recycle['result']}")
        lines.append("  source recipes:")
        for source in material["sources"]:
            results = ", ".join(source["results"])
            lines.append(f"    {source['ingredient']} -> {source['recipe']} -> {results}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create text/JSON material target lists from material-flow.json")
    parser.add_argument("--flow", type=Path, help="path to material-flow.json; defaults to the selected Factorio script-output path")
    parser.add_argument("--factorio", type=Path, help="Factorio executable path used to locate material-flow.json when --flow is omitted")
    parser.add_argument("--profile", required=True, help="profile name used in output file names")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="output directory")
    args = parser.parse_args()

    flow_path = args.flow or default_flow_path(args.factorio)
    flow_data = json.loads(flow_path.read_text(encoding="utf-8"))
    target_list = build_list(flow_data, args.profile)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    text_path = args.out_dir / f"list_{args.profile}.txt"
    json_path = args.out_dir / f"list_{args.profile}.json"
    write_text(target_list, text_path)
    json_path.write_text(json.dumps(target_list, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {text_path}")
    print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
