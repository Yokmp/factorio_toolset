#!/usr/bin/env python3
"""Build a passive ancestry-based scrap prediction from existing debug dumps."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GENERATED_PREFIXES = ("yis-recycle-",)
MIXED_SCRAP_MATERIAL = "yis-mixed"
SIDE_CHAIN_TERMS = (
    "barrel",
    "barreling",
    "recycling",
    "void",
)


@dataclass
class ResolveResult:
    status: str
    composition: dict[str, float]
    recipe: str | None = None
    reasons: list[str] | None = None
    ingredients: list[dict[str, Any]] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "composition": rounded_composition(self.composition),
            "recipe": self.recipe,
            "reasons": self.reasons or [],
            "ingredients": self.ingredients or [],
        }


def rounded_composition(values: dict[str, float]) -> dict[str, float]:
    return {name: round(amount, 6) for name, amount in sorted(values.items()) if amount > 0}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def amount_of(entry: dict[str, Any]) -> float:
    if "amount" in entry and entry["amount"] is not None:
        return float(entry["amount"])
    if "amount_min" in entry and "amount_max" in entry:
        return (float(entry["amount_min"]) + float(entry["amount_max"])) / 2
    return 1.0


def recipe_result_amount(recipe: dict[str, Any], result_name: str) -> float:
    for result in recipe.get("results") or []:
        if result.get("type", "item") == "item" and result.get("name") == result_name:
            return max(amount_of(result), 1.0)
    return 1.0


def markers(recipe: dict[str, Any]) -> set[str]:
    return {marker.get("key", "") for marker in (recipe.get("custom") or {}).get("markers") or []}


def looks_like_side_chain(recipe: dict[str, Any]) -> bool:
    name = recipe.get("name") or ""
    category = recipe.get("category") or ""
    if category == "recycling" or name.endswith("-recycling"):
        return True
    haystack = f"{name} {category}".lower()
    return any(term in haystack for term in SIDE_CHAIN_TERMS)


def is_generated_recipe(recipe: dict[str, Any]) -> bool:
    name = recipe.get("name") or ""
    return name.startswith(GENERATED_PREFIXES)


def recipe_score(recipe: dict[str, Any]) -> int:
    score = 100
    if recipe.get("hidden") is True:
        score -= 30
    if recipe.get("enabled") is False:
        score -= 5
    if recipe.get("auto_recycle") is False:
        score -= 25
    if recipe.get("allow_decomposition") is False:
        score -= 10
    if looks_like_side_chain(recipe):
        score -= 100
    score -= len([ingredient for ingredient in recipe.get("ingredients") or [] if ingredient.get("type", "item") == "fluid"]) * 6
    score -= max(len(recipe.get("ingredients") or []) - 4, 0)
    return score


def exact_component_materials(material_flow: dict[str, Any]) -> set[str]:
    exact = set()
    for flow in material_flow.get("flows") or []:
        material = flow.get("material")
        input_name = (flow.get("input") or {}).get("name")
        if not material or material != input_name:
            continue
        for recycle in flow.get("recycle_recipes") or []:
            result_name = (recycle.get("result") or {}).get("name")
            if result_name == material:
                exact.add(material)
    return exact


def build_resource_root_aliases(material_flow: dict[str, Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for material, resources in (material_flow.get("resources_by_material") or {}).items():
        for resource in resources or []:
            result = resource.get("result") or {}
            if result.get("type", "item") == "item" and result.get("name"):
                aliases[result["name"]] = material
    return aliases


def build_stable_material_root_aliases(material_flow: dict[str, Any], exact_components: set[str]) -> dict[str, str]:
    aliases = build_resource_root_aliases(material_flow)

    for flow in material_flow.get("flows") or []:
        material = flow.get("material")
        input_name = (flow.get("input") or {}).get("name")
        if not material or not input_name or material in exact_components:
            continue
        aliases[input_name] = material
    return aliases


def build_current_root_aliases(material_flow: dict[str, Any], exact_components: set[str]) -> dict[str, str]:
    return build_stable_material_root_aliases(material_flow, exact_components)


def build_hybrid_root_aliases(material_flow: dict[str, Any], exact_components: set[str]) -> dict[str, str]:
    return build_stable_material_root_aliases(material_flow, exact_components)


def build_root_aliases(material_flow: dict[str, Any], exact_components: set[str], policy: str) -> dict[str, str]:
    if policy == "resources":
        return build_resource_root_aliases(material_flow)
    if policy == "hybrid":
        return build_hybrid_root_aliases(material_flow, exact_components)
    return build_current_root_aliases(material_flow, exact_components)


def build_producers(production_flow: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    producers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for recipe in (production_flow.get("recipes") or {}).values():
        if is_generated_recipe(recipe) or looks_like_side_chain(recipe):
            continue
        for result in recipe.get("results") or []:
            if result.get("type", "item") == "item" and result.get("name"):
                producers[result["name"]].append(recipe)

    for recipe_list in producers.values():
        recipe_list.sort(key=lambda recipe: (-recipe_score(recipe), recipe.get("name") or ""))
    return producers


class AncestryResolver:
    def __init__(self, root_aliases: dict[str, str], producers: dict[str, list[dict[str, Any]]], max_depth: int) -> None:
        self.root_aliases = root_aliases
        self.producers = producers
        self.max_depth = max_depth
        self.cache: dict[str, ResolveResult] = {}

    def resolve(self, item_name: str, depth: int = 0, stack: tuple[str, ...] = ()) -> ResolveResult:
        if item_name in self.root_aliases:
            result = ResolveResult("root", {self.root_aliases[item_name]: 1.0}, reasons=["root-alias"])
            self.cache[item_name] = result
            return result
        if item_name in self.cache:
            return self.cache[item_name]
        if item_name in stack:
            return ResolveResult("cycle", {}, reasons=["cycle"], ingredients=[{"name": name} for name in (*stack, item_name)])
        if depth >= self.max_depth:
            return ResolveResult("depth-limit", {}, reasons=["depth-limit"])

        recipes = self.producers.get(item_name) or []
        if not recipes:
            result = ResolveResult("unresolved", {}, reasons=["no-producer"])
            self.cache[item_name] = result
            return result

        recipe = recipes[0]
        result_amount = recipe_result_amount(recipe, item_name)
        composition: dict[str, float] = defaultdict(float)
        ingredient_details: list[dict[str, Any]] = []
        reasons: list[str] = []

        for ingredient in recipe.get("ingredients") or []:
            ingredient_type = ingredient.get("type", "item")
            ingredient_name = ingredient.get("name")
            if not ingredient_name:
                continue
            if ingredient_type != "item":
                reasons.append(f"deferred-{ingredient_type}:{ingredient_name}")
                ingredient_details.append({
                    "type": ingredient_type,
                    "name": ingredient_name,
                    "amount": amount_of(ingredient),
                    "status": "deferred",
                })
                continue

            factor = amount_of(ingredient) / result_amount
            resolved = self.resolve(ingredient_name, depth + 1, (*stack, item_name))
            ingredient_details.append({
                "type": "item",
                "name": ingredient_name,
                "amount": amount_of(ingredient),
                "factor": round(factor, 6),
                "status": resolved.status,
                "composition": rounded_composition(resolved.composition),
            })
            for material, amount in resolved.composition.items():
                composition[material] += amount * factor
            if not resolved.composition:
                reasons.append(f"{resolved.status}:{ingredient_name}")

        if composition:
            status = "composed" if reasons else "resolved"
        else:
            status = "unresolved"
            if not reasons:
                reasons.append("empty-composition")

        result = ResolveResult(
            status=status,
            composition=dict(composition),
            recipe=recipe.get("name"),
            reasons=sorted(set(reasons)),
            ingredients=ingredient_details,
        )
        self.cache[item_name] = result
        return result


def build_comparison(material_flow: dict[str, Any], resolver: AncestryResolver) -> list[dict[str, Any]]:
    return build_comparison_with_limit(material_flow, resolver, None)


def effective_output(ancestry: dict[str, float], limit: int | None) -> dict[str, Any]:
    if not ancestry:
        return {
            "kind": "mixed",
            "reason": "unresolved",
            "materials": {MIXED_SCRAP_MATERIAL: 1.0},
        }
    if limit is not None and len(ancestry) > limit:
        return {
            "kind": "mixed",
            "reason": "width-limit",
            "limit": limit,
            "width": len(ancestry),
            "materials": {MIXED_SCRAP_MATERIAL: 1.0},
        }
    return {
        "kind": "direct",
        "reason": "within-limit",
        "limit": limit,
        "width": len(ancestry),
        "materials": rounded_composition(ancestry),
    }


def build_comparison_with_limit(
    material_flow: dict[str, Any],
    resolver: AncestryResolver,
    mixed_limit: int | None,
) -> list[dict[str, Any]]:
    comparisons = []
    for flow in material_flow.get("flows") or []:
        input_name = (flow.get("input") or {}).get("name")
        if not input_name:
            continue
        resolved = resolver.resolve(input_name)
        current = {flow.get("material"): 1.0} if flow.get("material") else {}
        ancestry = resolved.composition
        if not ancestry:
            status = "unresolved"
        elif set(current) == set(ancestry):
            status = "same"
        else:
            status = "different"
        comparisons.append({
            "status": status,
            "recipe": (flow.get("source_recipe") or {}).get("name"),
            "ingredient": input_name,
            "ingredient_amount": (flow.get("input") or {}).get("amount"),
            "current": rounded_composition(current),
            "ancestry": rounded_composition(ancestry),
            "effective_output": effective_output(ancestry, mixed_limit),
            "resolve_status": resolved.status,
            "resolve_recipe": resolved.recipe,
            "reasons": resolved.reasons or [],
        })
    comparisons.sort(key=lambda entry: (entry["status"], entry["ingredient"], entry.get("recipe") or ""))
    return comparisons


def summarize(comparisons: list[dict[str, Any]], resolver: AncestryResolver) -> dict[str, Any]:
    comparison_counts = Counter(entry["status"] for entry in comparisons)
    resolve_counts = Counter(result.status for result in resolver.cache.values())
    material_widths = Counter(len(entry["ancestry"]) for entry in comparisons if entry["ancestry"])
    effective_counts = Counter((entry.get("effective_output") or {}).get("kind", "unknown") for entry in comparisons)
    effective_reasons = Counter((entry.get("effective_output") or {}).get("reason", "unknown") for entry in comparisons)
    return {
        "comparisons": dict(sorted(comparison_counts.items())),
        "effective_outputs": dict(sorted(effective_counts.items())),
        "effective_reasons": dict(sorted(effective_reasons.items())),
        "resolved_items": dict(sorted(resolve_counts.items())),
        "ancestry_widths": dict(sorted((str(width), count) for width, count in material_widths.items())),
        "max_ancestry_width": max(material_widths.keys(), default=0),
    }


def family_name(name: str | None) -> str:
    if not name:
        return "unknown"
    if "bearing-ball" in name:
        return "bearing-ball"
    if "bearing" in name:
        return "bearing"
    if "gear-wheel" in name:
        return "gear"
    if "pipe" in name:
        return "pipe"
    if "cable" in name:
        return "cable"
    if "circuit-board" in name or name.endswith("board"):
        return "board"
    if "processing-unit" in name or "circuit" in name:
        return "electronics"
    if "battery" in name:
        return "battery"
    return "other"


def review_scrap_name(material: str) -> str:
    return f"yis-{material.removeprefix('yis-')}-scrap"


def unique_review_comparisons(comparisons: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    unique = {}
    for comparison in comparisons:
        ingredient = comparison.get("ingredient")
        if ingredient and comparison.get("status") in {"different", "unresolved"}:
            unique.setdefault(ingredient, comparison)
    return unique


def comparison_width(comparison: dict[str, Any]) -> int:
    return len(comparison.get("ancestry") or {})


def reason_bucket(reasons: list[str]) -> str:
    if not reasons:
        return "clean"
    if any(reason.startswith("deferred-fluid:") for reason in reasons):
        return "fluid-deferred"
    if any(reason.startswith("unresolved:") for reason in reasons):
        return "unresolved-partial"
    if any(reason.startswith("cycle") for reason in reasons):
        return "cycle"
    if any(reason.startswith("depth-limit") for reason in reasons):
        return "depth-limit"
    return "other-reasons"


def decision_group(comparison: dict[str, Any]) -> str:
    ingredient = comparison.get("ingredient", "")
    group = family_name(ingredient)
    reasons = comparison.get("reasons") or []
    if comparison.get("status") == "unresolved":
        return "api_compat_candidate"
    if group == "pipe" and "ceramic" in ingredient:
        return "api_compat_candidate"
    if group in {"electronics", "battery"}:
        return "mixed_candidate"
    if comparison_width(comparison) >= 4:
        return "mixed_candidate"
    if any(reason.startswith("unresolved:") for reason in reasons):
        return "api_compat_candidate"
    if any(reason.startswith("deferred-fluid:") for reason in reasons):
        return "preserve_future_chemistry"
    if group in {"gear", "pipe", "bearing", "bearing-ball", "cable", "board"}:
        return "safe_hybrid"
    return "review"


def format_materials(materials: dict[str, float] | None) -> str:
    return ", ".join(f"{name}:{amount}" for name, amount in (materials or {}).items()) or "-"


def profile_label(data: dict[str, Any]) -> str:
    return data.get("profile") or "explicit-flow"


def write_hybrid_review(output_dir: Path, data: dict[str, Any]) -> Path:
    comparisons = data.get("comparisons") or []
    different = [comparison for comparison in comparisons if comparison.get("status") == "different"]
    unresolved = [comparison for comparison in comparisons if comparison.get("status") == "unresolved"]
    unique = unique_review_comparisons(comparisons)
    by_family = defaultdict(list)
    family_counts = Counter(family_name(comparison.get("ingredient")) for comparison in different)
    width_counts = Counter(comparison_width(comparison) for comparison in different if comparison.get("ancestry"))
    reason_counts = Counter(reason_bucket(comparison.get("reasons") or []) for comparison in different)
    for ingredient, comparison in sorted(unique.items()):
        by_family[family_name(ingredient)].append(comparison)

    lines = [
        f"# {profile_label(data)} Hybrid Ancestry Review",
        "",
        "Generated from `ancestry-flow-hybrid.json`. This is passive review evidence only.",
        "",
        "## Summary",
        "",
        f"- Total flow comparisons: {len(comparisons)}",
        f"- Different flow rows: {len(different)}",
        f"- Unique different ingredients: {len(unique)}",
        f"- Unresolved flow rows: {len(unresolved)}",
        f"- Max ancestry width: {(data.get('summary') or {}).get('max_ancestry_width')}",
        "",
        "## Difference Families",
        "",
    ]
    for name, count in sorted(family_counts.items()):
        lines.append(f"- `{name}`: {count} rows, {len(by_family[name])} unique ingredients")
    lines.extend(["", "## Ancestry Width", ""])
    for width, count in sorted(width_counts.items()):
        lines.append(f"- `{width}` materials: {count} rows")
    lines.extend(["", "## Reason Buckets", ""])
    for name, count in sorted(reason_counts.items()):
        lines.append(f"- `{name}`: {count} rows")
    lines.extend(["", "## Unique Differences By Family", ""])
    for group in sorted(by_family):
        lines.extend([f"### {group}", ""])
        for comparison in by_family[group]:
            current = ", ".join((comparison.get("current") or {}).keys()) or "-"
            ancestry = format_materials(comparison.get("ancestry"))
            reasons = ", ".join(comparison.get("reasons") or []) or "clean"
            lines.append(
                f"- `{comparison['ingredient']}`: current `{current}` -> ancestry `{ancestry}`; "
                f"via `{comparison.get('resolve_recipe')}`; {reasons}"
            )
        lines.append("")
    lines.extend(["## Unresolved", ""])
    if not unresolved:
        lines.append("- None")
    else:
        seen = set()
        for comparison in unresolved:
            key = (comparison.get("ingredient"), tuple(comparison.get("reasons") or []))
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- `{comparison.get('ingredient')}` in `{comparison.get('recipe')}`: "
                f"{', '.join(comparison.get('reasons') or []) or 'unknown'}"
            )
    lines.extend([
        "",
        "## Initial Reading",
        "",
        "- Simple component families mostly look like good ancestry wins: gears, pipes, cables, bearings, and bearing balls collapse to stable base/alloy materials.",
        "- Electronics are intentionally wider and are likely mixed-scrap limit candidates rather than direct one-to-one exact scrap candidates.",
        "- Fluid-deferred rows are not necessarily wrong; they mark places where preserve-shape or a future sludge fallback may matter.",
        "- The single unresolved flow is currently `bob-ceramic-pipe`, blocked by `bob-silicon-nitride`; this is an API/compat candidate or mixed fallback case.",
    ])

    output_path = output_dir / "ancestry-hybrid-review.md"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def write_decision_review(output_dir: Path, data: dict[str, Any]) -> Path:
    unique = unique_review_comparisons(data.get("comparisons") or [])
    groups = defaultdict(list)
    for _, comparison in sorted(unique.items()):
        groups[decision_group(comparison)].append(comparison)

    lines = [
        f"# {profile_label(data)} Hybrid Decisions",
        "",
        "Generated from `ancestry-flow-hybrid.json`. This is a manual review aid, not active mod behavior.",
        "",
        "## Summary",
        "",
        f"- Unique reviewed ingredients: {len(unique)}",
    ]
    for key in ["safe_hybrid", "mixed_candidate", "api_compat_candidate", "preserve_future_chemistry", "review"]:
        if groups.get(key):
            lines.append(f"- `{key}`: {len(groups[key])}")
    lines.append("")

    titles = {
        "safe_hybrid": "Safe Hybrid",
        "mixed_candidate": "Mixed Candidate",
        "api_compat_candidate": "API/Compat Candidate",
        "preserve_future_chemistry": "Preserve/Future Chemistry",
        "review": "Review",
    }
    for key in ["safe_hybrid", "mixed_candidate", "api_compat_candidate", "preserve_future_chemistry", "review"]:
        if not groups.get(key):
            continue
        lines.extend([f"## {titles[key]}", ""])
        for comparison in groups[key]:
            current = ", ".join((comparison.get("current") or {}).keys()) or "-"
            ancestry = format_materials(comparison.get("ancestry"))
            reasons = ", ".join(comparison.get("reasons") or []) or "clean"
            lines.append(
                f"- `{comparison['ingredient']}`: `{current}` -> `{ancestry}`; "
                f"via `{comparison.get('resolve_recipe')}`; {reasons}"
            )
        lines.append("")
    lines.extend([
        "## Reading",
        "",
        "- Safe Hybrid items are the strongest candidates for replacing exact component scrap with ancestry-derived material scrap.",
        "- `bob-basic-circuit-board` is currently in Safe Hybrid because it resolves cleanly and narrowly, but it should be reviewed together with electronics before active use.",
        "- Mixed Candidates should probably use a material-count limit or `yis-mixed-scrap` fallback instead of emitting many separate scrap outputs directly.",
        "- API/Compat Candidates need explicit composition or ignore rules before an active ancestry solver can rely on them.",
        "- Preserve/Future Chemistry entries contain process fluids such as lubricant and should stay out of the first active ancestry pass.",
    ])

    output_path = output_dir / "ancestry-hybrid-decisions.md"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def write_mixed_limit_review(output_dir: Path, data: dict[str, Any]) -> Path:
    unique = unique_review_comparisons(data.get("comparisons") or [])

    def simulated_outcome(comparison: dict[str, Any], limit: int) -> str:
        if not comparison.get("ancestry") or comparison.get("status") == "unresolved":
            return "mixed-unresolved"
        if comparison_width(comparison) > limit:
            return "mixed-width"
        return "direct"

    lines = [
        f"# {profile_label(data)} Mixed-Limit Simulation",
        "",
        "Generated from `ancestry-flow-hybrid.json`. This is passive review evidence only.",
        "",
        "Rules simulated: unresolved entries always become `yis-mixed-scrap`; resolved entries become mixed when ancestry width is greater than the tested limit.",
        "",
        "## Summary Matrix",
        "",
        "| Limit | Direct | Mixed by width | Mixed unresolved |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for limit in range(1, 7):
        counts = Counter(simulated_outcome(comparison, limit) for comparison in unique.values())
        lines.append(f"| {limit} | {counts['direct']} | {counts['mixed-width']} | {counts['mixed-unresolved']} |")
    lines.append("")
    lines.append("## Mixed Ingredients By Limit")
    for limit in range(1, 7):
        mixed = [comparison for comparison in unique.values() if simulated_outcome(comparison, limit) != "direct"]
        lines.extend(["", f"### Limit {limit}", ""])
        if not mixed:
            lines.append("- None")
            continue
        grouped = defaultdict(list)
        for comparison in sorted(mixed, key=lambda item: (family_name(item.get("ingredient")), item.get("ingredient"))):
            grouped[family_name(comparison.get("ingredient"))].append(comparison)
        for group in sorted(grouped):
            names = ", ".join(
                f"`{comparison['ingredient']}` ({simulated_outcome(comparison, limit)}, width {comparison_width(comparison)})"
                for comparison in grouped[group]
            )
            lines.append(f"- `{group}`: {names}")
    lines.extend([
        "",
        "## Reading",
        "",
        "- Limit 1 keeps only single-material ancestry direct. It converts all alloyed cables, boards, electronics, and batteries to mixed.",
        "- Limit 2 keeps simple two-material cables and boards direct, while wide electronics still become mixed.",
        "- Limit 3 keeps most narrow component ancestry direct and still sends advanced electronics to mixed.",
        "- Limits 5-6 start allowing wide electronics directly, which may create too many scrap outputs for normal gameplay.",
    ])

    output_path = output_dir / "ancestry-mixed-limit-simulation.md"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def write_effective_output_review(output_dir: Path, data: dict[str, Any]) -> Path:
    unique = {}
    for comparison in data.get("comparisons") or []:
        ingredient = comparison.get("ingredient")
        if ingredient and (comparison.get("status") != "same" or (comparison.get("effective_output") or {}).get("kind") == "mixed"):
            unique.setdefault(ingredient, comparison)

    grouped_by_output = defaultdict(list)
    for comparison in unique.values():
        output = comparison.get("effective_output") or {}
        key = "direct" if output.get("kind") == "direct" else "mixed-" + (output.get("reason") or "unknown")
        grouped_by_output[key].append(comparison)

    lines = [
        f"# {profile_label(data)} Effective Output Review",
        "",
        f"Generated from `ancestry-flow-hybrid.json` with `mixed_limit = {data.get('mixed_limit')}`. This is passive review evidence only.",
        "",
        "## Summary",
        "",
        f"- Unique reviewed ingredients: {len(unique)}",
    ]
    for key in ["direct", "mixed-width-limit", "mixed-unresolved"]:
        if grouped_by_output.get(key):
            lines.append(f"- `{key}`: {len(grouped_by_output[key])}")
    lines.extend(["", "## Current vs Effective"])

    titles = {
        "direct": "Direct Effective Output",
        "mixed-width-limit": "Mixed By Width Limit",
        "mixed-unresolved": "Mixed By Unresolved Ancestry",
    }
    for key in ["direct", "mixed-width-limit", "mixed-unresolved"]:
        if not grouped_by_output.get(key):
            continue
        lines.extend(["", f"### {titles[key]}", ""])
        grouped = defaultdict(list)
        for comparison in sorted(grouped_by_output[key], key=lambda item: (family_name(item.get("ingredient")), item.get("ingredient"))):
            grouped[family_name(comparison.get("ingredient"))].append(comparison)
        for group in sorted(grouped):
            lines.extend([f"#### {group}", ""])
            for comparison in grouped[group]:
                current = ", ".join(review_scrap_name(name) for name in (comparison.get("current") or {}).keys()) or "-"
                effective = ", ".join(review_scrap_name(name) for name in ((comparison.get("effective_output") or {}).get("materials") or {}).keys()) or "-"
                ancestry = format_materials(comparison.get("ancestry"))
                reason = (comparison.get("effective_output") or {}).get("reason") or "within-limit"
                lines.append(
                    f"- `{comparison['ingredient']}`: current `{current}` -> effective `{effective}`; "
                    f"ancestry `{ancestry}`; {reason}"
                )
            lines.append("")
    lines.extend([
        "## Reading",
        "",
        "- Direct entries are the strongest candidates for replacing exact component scrap with ancestry-derived material scrap.",
        "- Mixed-by-width entries are the expected advanced electronics candidates at limit 3.",
        "- Mixed-unresolved entries should stay safe because `yis-mixed-scrap` can be migrated or rebalanced later.",
    ])

    output_path = output_dir / "ancestry-effective-output-review.md"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def write_review_files(output_path: Path, data: dict[str, Any]) -> list[Path]:
    output_dir = output_path.parent
    return [
        write_hybrid_review(output_dir, data),
        write_decision_review(output_dir, data),
        write_mixed_limit_review(output_dir, data),
        write_effective_output_review(output_dir, data),
    ]


def output_name_for_policy(policy: str) -> str:
    if policy == "current":
        return "ancestry-flow.json"
    return f"ancestry-flow-{policy}.json"


def profile_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    if args.material_flow and args.production_flow:
        material_path = Path(args.material_flow)
        production_path = Path(args.production_flow)
        output_path = Path(args.output) if args.output else material_path.with_name(output_name_for_policy(args.root_policy))
        return material_path, production_path, output_path

    if not args.profile:
        raise SystemExit("Use --profile or pass both --material-flow and --production-flow.")

    profile_dir = Path(args.dump_dir) / args.profile
    material_path = profile_dir / "material-flow.json"
    production_path = profile_dir / "production-flow.json"
    output_path = Path(args.output) if args.output else profile_dir / output_name_for_policy(args.root_policy)
    return material_path, production_path, output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a passive ancestry-flow comparison from Ingredient Scrap JSON dumps.")
    parser.add_argument("--profile", help="Dump profile under tools/toolset/dumps, e.g. bob_angels_full_is.")
    parser.add_argument("--dump-dir", default="tools/toolset/dumps", help="Directory containing profile dump folders.")
    parser.add_argument("--material-flow", help="Explicit material-flow.json path.")
    parser.add_argument("--production-flow", help="Explicit production-flow.json path.")
    parser.add_argument("--output", help="Output ancestry-flow.json path.")
    parser.add_argument("--max-depth", type=int, default=8, help="Maximum recursive recipe ancestry depth.")
    parser.add_argument("--mixed-limit", type=int, default=3, help="Material width limit before effective output becomes yis-mixed-scrap.")
    parser.add_argument("--write-reviews", action="store_true", help="Write Markdown review files next to the ancestry JSON.")
    parser.add_argument(
        "--root-policy",
        choices=("current", "resources", "hybrid"),
        default="current",
        help="Root stop policy: current uses current material-flow roots and aliases; resources only uses mined resource results; hybrid uses stable material stops while exact components keep resolving.",
    )
    args = parser.parse_args()

    material_path, production_path, output_path = profile_paths(args)
    material_flow = load_json(material_path)
    production_flow = load_json(production_path)

    exact_components = exact_component_materials(material_flow)
    root_aliases = build_root_aliases(material_flow, exact_components, args.root_policy)
    producers = build_producers(production_flow)
    resolver = AncestryResolver(root_aliases, producers, args.max_depth)

    comparisons = build_comparison_with_limit(material_flow, resolver, args.mixed_limit)
    resolved_items = {
        item_name: resolver.resolve(item_name).as_json()
        for item_name in sorted({(flow.get("input") or {}).get("name") for flow in material_flow.get("flows") or [] if (flow.get("input") or {}).get("name")})
    }
    data = {
        "schema": "ingredient-scrap-ancestry-flow/v1",
        "profile": args.profile,
        "root_policy": args.root_policy,
        "mixed_limit": args.mixed_limit,
        "material_flow": str(material_path),
        "production_flow": str(production_path),
        "root_aliases": dict(sorted(root_aliases.items())),
        "exact_components": sorted(exact_components),
        "summary": summarize(comparisons, resolver),
        "items": resolved_items,
        "comparisons": comparisons,
    }
    write_json(output_path, data)
    if args.write_reviews:
        for review_path in write_review_files(output_path, data):
            print(f"Review: {review_path}")

    summary = data["summary"]
    print(f"Ancestry flow: {output_path}")
    print(f"Items: {len(resolved_items)}")
    print(f"Comparisons: {summary['comparisons']}")
    print(f"Resolved items: {summary['resolved_items']}")
    print(f"Max ancestry width: {summary['max_ancestry_width']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
