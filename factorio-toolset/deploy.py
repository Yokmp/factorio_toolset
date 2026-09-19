#!/usr/bin/env python3
"""Build and publish filtered Factorio mod release archives.

The script is intended to live inside a Factorio mod folder, for example
``mods/Ingredient_Scrap/tools/toolset/deploy.py``. It locates the mod root by
walking upwards until it finds ``info.json``. Use ``--mod-root`` when the script
is stored elsewhere.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

APP_VERSION = "2.0.0"
ARTIFACT_PUBLIC = "public"
ARTIFACT_PORTAL = "portal"
IGNORE_FILENAME = ".deployignore"

EXCLUDE_DIRS = {
    "_legacy",
    "_release_",
    "_lib",
    "_working",
    ".agents",
    ".codex",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".vs",
    ".vscode",
    "__pycache__",
    "lua-format",
    "orig",
    "new",
    "old",
    "_single",
    "_multi",
    "test",
    "tools",
    "workspace",
}

EXCLUDE_FILES = {
    ".gitattributes",
    ".gitignore",
    ".luarc.json",
    "locale/en/test.cfg",
}

EXCLUDE_EXTENSIONS = {
    ".7z",
    ".py",
    ".pyc",
    ".pyo",
    ".xcf",
}

EXCLUDE_NAME_PREFIXES = (
)

EXCLUDE_NAME_CONTAINS = (
    "_testing.lua",
)

PORTAL_EXCLUDE_EXTENSIONS = {
    ".md",
}

PORTAL_EXCLUDE_NAME_PREFIXES = (
    "shot_",
    "shot-",
)


@dataclass(frozen=True)
class ModInfo:
    name: str
    title: str
    version: str


@dataclass(frozen=True)
class DeployConfig:
    mod_root: Path
    release_dir: Path
    strip_debug: bool
    public: bool
    verbose: bool

    @property
    def info_path(self) -> Path:
        return self.mod_root / "info.json"

    @property
    def work_dir(self) -> Path:
        return self.release_dir / ".deploy-work"

    @property
    def public_work_dir(self) -> Path:
        return self.work_dir / "public"

    @property
    def portal_work_dir(self) -> Path:
        return self.work_dir / "portal"


@dataclass
class CollectStats:
    copied: int = 0
    excluded: int = 0
    stripped_files: int = 0
    stripped_regions: int = 0
    bytes_written: int = 0


def default_ignore_text() -> str:
    """Render the existing release filters as an editable mod-local template."""
    common = [f"{name}/" for name in sorted(EXCLUDE_DIRS)]
    common += sorted(EXCLUDE_FILES)
    common += [f"*{ext}" for ext in sorted(EXCLUDE_EXTENSIONS)]
    common += [f"{prefix}*" for prefix in EXCLUDE_NAME_PREFIXES]
    common += [f"*{fragment}*" for fragment in EXCLUDE_NAME_CONTAINS]
    portal = [f"*{ext}" for ext in sorted(PORTAL_EXCLUDE_EXTENSIONS)]
    portal += [f"{prefix}*" for prefix in PORTAL_EXCLUDE_NAME_PREFIXES]
    return ("# Paths are relative to the mod root. A pattern without / matches any name.\n"
            "# A trailing / matches directories; # starts a comment.\n"
            "[common]\n" + "\n".join(common) + "\n\n"
            "[public]\n\n"
            "[portal]\n" + "\n".join(portal) + "\n")


def load_ignore_patterns(mod_root: Path) -> dict[str, list[str]]:
    """Create the template if absent and load common and archive-specific patterns."""
    path = mod_root / IGNORE_FILENAME
    if not path.exists():
        path.write_text(default_ignore_text(), encoding="utf-8")
        print(f"Created ignore template: {path}")
    patterns: dict[str, list[str]] = {"common": [], ARTIFACT_PUBLIC: [], ARTIFACT_PORTAL: []}
    section = "common"
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            if section not in patterns:
                raise SystemExit(f"Unknown section in {path}:{number}: {line}")
            continue
        patterns[section].append(line.replace("\\", "/"))
    return patterns


def matches_ignore(relative: str, is_dir: bool, patterns: list[str]) -> bool:
    """Match root-relative paths, names at any depth, and directory rules."""
    parts = relative.split("/")
    for pattern in patterns:
        directory_only = pattern.endswith("/")
        rule = pattern.rstrip("/")
        if not rule:
            continue
        candidates = parts if is_dir else parts[:-1] if directory_only else parts
        if "/" in rule:
            if fnmatch.fnmatchcase(relative, rule) and (is_dir or not directory_only):
                return True
            if directory_only and any(fnmatch.fnmatchcase("/".join(parts[:index]), rule) for index in range(1, len(parts))):
                return True
        elif any(fnmatch.fnmatchcase(part, rule) for part in candidates):
            return True
    return False


def find_mod_root(start: Path) -> Path:
    """Return the nearest parent directory containing info.json."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "info.json").is_file():
            return candidate
    raise SystemExit(f"No Factorio mod root found above: {start}")


def load_info(mod_root: Path) -> ModInfo:
    """Load mod identity from info.json."""
    info_path = mod_root / "info.json"
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing info.json in mod root: {mod_root}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid info.json: {exc}") from exc

    missing = [key for key in ("name", "title", "version") if not data.get(key)]
    if missing:
        raise SystemExit(f"info.json is missing required field(s): {', '.join(missing)}")
    return ModInfo(name=data["name"], title=data["title"], version=data["version"])


def relpath(path: Path, root: Path) -> str:
    """Return a stable slash-separated path relative to root."""
    return path.relative_to(root).as_posix()


def is_relative_to(path: Path, root: Path) -> bool:
    """Return true when path is inside root."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_remove_tree(path: Path, allowed_root: Path) -> None:
    """Remove a directory only when it is safely inside allowed_root."""
    path = path.resolve()
    allowed_root = allowed_root.resolve()
    if path == allowed_root or not is_relative_to(path, allowed_root):
        raise RuntimeError(f"Refusing to remove unsafe path: {path}")
    if path.exists():
        shutil.rmtree(path)


def safe_empty_directory(path: Path, allowed_root: Path) -> None:
    """Delete all children from path while preserving the directory itself."""
    path = path.resolve()
    allowed_root = allowed_root.resolve()
    if path == allowed_root or not is_relative_to(path, allowed_root):
        raise RuntimeError(f"Refusing to empty unsafe path: {path}")
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def should_exclude(
    path: Path, mod_root: Path, is_dir: bool = False, artifact: str = ARTIFACT_PUBLIC,
    patterns: dict[str, list[str]] | None = None, release_dir: Path | None = None,
) -> bool:
    """Return true when a path must not be included in release archives."""
    if path.resolve() == mod_root.resolve():
        return False

    if (artifact == ARTIFACT_PORTAL and path.name == IGNORE_FILENAME) or (release_dir is not None and is_relative_to(path, release_dir)):
        return True

    relative = relpath(path, mod_root)
    name = path.name

    if patterns is not None and matches_ignore(relative, is_dir, patterns["common"] + patterns[artifact]):
        return True
    if is_dir and name.endswith("_" + load_info(mod_root).version):
        return True
    return False


def format_size(value: int) -> str:
    """Return a compact human-readable byte size."""
    labels = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    index = 0
    while size >= 1024 and index < len(labels) - 1:
        size /= 1024
        index += 1
    return f"{size:.2f} {labels[index]}"


def strip_lua_debug_regions(text: str, source_path: Path) -> tuple[str, int]:
    """Remove --#region debug blocks from Lua source text."""
    output: list[str] = []
    depth = 0
    removed = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == "--#region debug":
            depth += 1
            removed += 1
            continue
        if stripped == "--#endregion":
            if depth == 0:
                raise RuntimeError(f"Unmatched debug region end in {source_path}")
            depth -= 1
            continue
        if depth == 0:
            output.append(line)

    if depth != 0:
        raise RuntimeError(f"Unclosed debug region in {source_path}")
    return "".join(output), removed


def copy_release_file(source: Path, target: Path, config: DeployConfig, stats: CollectStats) -> bool:
    """Copy one release file and optionally strip Lua debug regions."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if config.strip_debug and source.suffix == ".lua":
        try:
            text = source.read_text(encoding="utf-8")
            stripped, removed = strip_lua_debug_regions(text, source)
            if removed > 0:
                stats.stripped_files += 1
                stats.stripped_regions += removed
            if removed > 0 and stripped.strip() == "":
                if config.verbose:
                    print(f"skip empty stripped lua: {relpath(source, config.mod_root)}")
                return False
            with target.open("w", encoding="utf-8", newline="") as handle:
                handle.write(stripped)
            stats.bytes_written += target.stat().st_size
            return True
        except UnicodeDecodeError:
            pass

    shutil.copy2(source, target)
    stats.bytes_written += target.stat().st_size
    return True


def iter_release_files(
    config: DeployConfig, artifact: str = ARTIFACT_PUBLIC,
    patterns: dict[str, list[str]] | None = None,
) -> Iterable[Path]:
    """Yield files that belong in release archives."""
    if patterns is None:
        patterns = load_ignore_patterns(config.mod_root)
    for root, subdirs, files in os.walk(config.mod_root):
        root_path = Path(root)
        subdirs[:] = [
            subdir
            for subdir in sorted(subdirs)
            if not should_exclude(root_path / subdir, config.mod_root, is_dir=True, artifact=artifact, patterns=patterns, release_dir=config.release_dir)
        ]
        if should_exclude(root_path, config.mod_root, is_dir=True, artifact=artifact, patterns=patterns, release_dir=config.release_dir):
            continue
        for filename in sorted(files):
            path = root_path / filename
            if not should_exclude(path, config.mod_root, artifact=artifact, patterns=patterns, release_dir=config.release_dir):
                yield path


def collect_release_tree(
    config: DeployConfig, target_root: Path, artifact: str = ARTIFACT_PUBLIC,
    patterns: dict[str, list[str]] | None = None,
) -> CollectStats:
    """Copy filtered release files into target_root."""
    stats = CollectStats()
    target_root.mkdir(parents=True, exist_ok=True)
    for source in iter_release_files(config, artifact, patterns):
        relative = source.relative_to(config.mod_root)
        target = target_root / relative
        copied = copy_release_file(source, target, config, stats)
        if copied:
            stats.copied += 1
            if config.verbose:
                print(f"copy {relative.as_posix()}")
    return stats


def scan_release(
    config: DeployConfig, artifact: str = ARTIFACT_PUBLIC,
    patterns: dict[str, list[str]] | None = None,
) -> CollectStats:
    """Validate release files and debug regions without copying them."""
    stats = CollectStats()
    all_files = {path for path in config.mod_root.rglob("*") if path.is_file()}
    included = set(iter_release_files(config, artifact, patterns))
    stats.excluded = len(all_files - included)
    for source in sorted(included):
        if config.strip_debug and source.suffix == ".lua":
            try:
                _, removed = strip_lua_debug_regions(source.read_text(encoding="utf-8"), source)
            except UnicodeDecodeError:
                removed = 0
            if removed > 0:
                stats.stripped_files += 1
                stats.stripped_regions += removed
        stats.copied += 1
        stats.bytes_written += source.stat().st_size
    return stats


def create_zip_from_tree(source_dir: Path, zip_path: Path, root_arcname: str) -> None:
    """Create a zip archive from source_dir under root_arcname."""
    if zip_path.exists():
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, subdirs, files in os.walk(source_dir):
            subdirs.sort()
            for filename in sorted(files):
                file_path = Path(root) / filename
                relative = file_path.relative_to(source_dir)
                archive_name = Path(root_arcname) / relative
                archive.write(file_path, archive_name.as_posix())


def build_release(config: DeployConfig) -> None:
    """Build release archives."""
    info = load_info(config.mod_root)
    patterns = load_ignore_patterns(config.mod_root)
    full_name = f"{info.name}_{info.version}"
    portal_zip = config.release_dir / f"{full_name}.zip"
    public_zip = config.release_dir / "public.zip"

    safe_remove_tree(config.work_dir, config.release_dir)
    config.release_dir.mkdir(parents=True, exist_ok=True)
    try:
        public_stats = collect_release_tree(config, config.public_work_dir / info.name, ARTIFACT_PUBLIC, patterns)
        if config.public:
            create_zip_from_tree(config.public_work_dir / info.name, public_zip, info.name)
        portal_stats = collect_release_tree(config, config.portal_work_dir / full_name, ARTIFACT_PORTAL, patterns)
        create_zip_from_tree(config.portal_work_dir / full_name, portal_zip, full_name)
    finally:
        safe_remove_tree(config.work_dir, config.release_dir)

    print(f"Release: {info.title} {info.version}")
    print(f"Public files: {public_stats.copied} ({format_size(public_stats.bytes_written)})")
    print(f"Mod Portal files: {portal_stats.copied} ({format_size(portal_stats.bytes_written)})")
    print(f"Debug regions stripped: {portal_stats.stripped_regions} in {portal_stats.stripped_files} file(s)")
    if config.public:
        print(f"Public zip: {public_zip} ({format_size(public_zip.stat().st_size)})")
    print(f"Mod Portal zip: {portal_zip} ({format_size(portal_zip.stat().st_size)})")


def check_release(config: DeployConfig) -> None:
    """Validate release inputs without creating final archives."""
    info = load_info(config.mod_root)
    patterns = load_ignore_patterns(config.mod_root)
    public_stats = scan_release(config, ARTIFACT_PUBLIC, patterns)
    portal_stats = scan_release(config, ARTIFACT_PORTAL, patterns)
    print(f"Mod root: {config.mod_root}")
    print(f"Release dir: {config.release_dir}")
    print(f"Mod: {info.name} {info.version}")
    print(f"Public files: {public_stats.copied}")
    print(f"Public excluded files: {public_stats.excluded}")
    print(f"Mod Portal files: {portal_stats.copied}")
    print(f"Mod Portal excluded files: {portal_stats.excluded}")
    print(f"Debug regions: {portal_stats.stripped_regions} in {portal_stats.stripped_files} file(s)")
    print(f"Expected public zip: {config.release_dir / 'public.zip'}")
    print(f"Expected Mod Portal zip: {config.release_dir / (info.name + '_' + info.version + '.zip')}")


def clean_release(config: DeployConfig) -> None:
    """Remove the release directory under the mod root."""
    if not is_relative_to(config.release_dir, config.mod_root):
        raise SystemExit(f"Refusing to clean release dir outside mod root: {config.release_dir}")
    if config.release_dir.exists():
        shutil.rmtree(config.release_dir)
        print(f"Removed {config.release_dir}")
    else:
        print(f"Nothing to clean: {config.release_dir}")


def ensure_gitignore(mod_root: Path) -> None:
    """Ensure _release_ is ignored in the mod root gitignore."""
    gitignore = mod_root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    normalized = {line.strip() for line in lines}
    if "/_release_" in normalized or "/_release_/" in normalized:
        print(".gitignore already ignores /_release_/")
        return
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.append("/_release_/")
    gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Added /_release_/ to .gitignore")


def run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command and return its completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def require_git_repo(mod_root: Path) -> None:
    """Exit if mod_root is not inside a git repository."""
    try:
        result = run_git(["rev-parse", "--show-toplevel"], cwd=mod_root)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit("publish-public requires git and a git repository") from exc
    if Path(result.stdout.strip()).resolve() != mod_root.resolve():
        raise SystemExit("publish-public expects the mod root to be the git repository root")


def extract_zip_to(zip_path: Path, target_dir: Path) -> None:
    """Extract a zip archive into target_dir, dropping the archive root folder."""
    with zipfile.ZipFile(zip_path, "r") as archive:
        members = archive.infolist()
        roots = {Path(member.filename).parts[0] for member in members if Path(member.filename).parts}
        strip_root = len(roots) == 1
        for member in members:
            parts = Path(member.filename).parts
            if not parts:
                continue
            relative_parts = parts[1:] if strip_root else parts
            if not relative_parts:
                continue
            target = target_dir.joinpath(*relative_parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)


def branch_exists(mod_root: Path, branch: str) -> bool:
    """Return true when origin/branch exists."""
    result = run_git(["ls-remote", "--exit-code", "--heads", "origin", branch], cwd=mod_root, check=False)
    return result.returncode == 0


def publish_public(config: DeployConfig, branch: str, build_first: bool, dry_run: bool) -> None:
    """Publish public.zip contents to a target branch using a temporary worktree."""
    require_git_repo(config.mod_root)
    info = load_info(config.mod_root)
    if build_first and not dry_run:
        build_release(config)

    public_zip = config.release_dir / "public.zip"
    worktree = config.release_dir / f".public-worktree-{branch}"
    commit_message = f"Release public source {info.version}"

    print(f"Branch: {branch}")
    print(f"Public zip: {public_zip}")
    print(f"Worktree: {worktree}")
    print(f"Commit: {commit_message}")
    if dry_run:
        print("Dry run: no worktree, commit, or push will be created.")
        return

    if not public_zip.exists():
        raise SystemExit(f"Missing public.zip: {public_zip}. Run build first or use --build-first.")
    if worktree.exists():
        raise SystemExit(f"Temporary worktree already exists: {worktree}")

    exists = branch_exists(config.mod_root, branch)
    try:
        if exists:
            run_git(["fetch", "origin", branch], cwd=config.mod_root)
            run_git(["worktree", "add", "--detach", str(worktree), f"origin/{branch}"], cwd=config.mod_root)
            run_git(["checkout", "-B", branch], cwd=worktree)
        else:
            run_git(["worktree", "add", "--detach", str(worktree), "HEAD"], cwd=config.mod_root)
            run_git(["checkout", "--orphan", branch], cwd=worktree)

        safe_empty_directory(worktree, config.release_dir)
        extract_zip_to(public_zip, worktree)
        run_git(["add", "-A"], cwd=worktree)
        status = run_git(["status", "--porcelain"], cwd=worktree).stdout.strip()
        if not status:
            print("public branch already up to date")
            return
        run_git(["commit", "-m", commit_message], cwd=worktree)
        run_git(["push", "origin", f"HEAD:{branch}"], cwd=worktree)
        print(f"Published public source to origin/{branch}")
    finally:
        run_git(["worktree", "remove", "--force", str(worktree)], cwd=config.mod_root, check=False)


def make_config(args: argparse.Namespace) -> DeployConfig:
    """Build deploy config from parsed CLI args."""
    start = Path(args.mod_root) if args.mod_root else Path(__file__)
    mod_root = find_mod_root(start)
    release_dir = Path(args.release_dir) if args.release_dir else mod_root / "_release_"
    if not release_dir.is_absolute():
        release_dir = mod_root / release_dir
    return DeployConfig(
        mod_root=mod_root.resolve(),
        release_dir=release_dir.resolve(),
        strip_debug=not getattr(args, "no_strip_debug", False),
        public=not getattr(args, "no_public", False),
        verbose=getattr(args, "verbose", False),
    )


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add shared CLI options."""
    parser.add_argument("--mod-root", type=Path, help="Factorio mod root containing info.json")
    parser.add_argument("--release-dir", type=Path, help="release output directory; defaults to <mod-root>/_release_")
    parser.add_argument("--verbose", action="store_true", help="print copied files and detailed actions")
    parser.add_argument("--no-strip-debug", action="store_true", help="keep --#region debug blocks in Lua files")


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Build and publish Factorio mod release archives.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build Mod Portal and public source zips")
    add_common_args(build)
    build.add_argument("--no-public", action="store_true", help="do not create public.zip")

    check = subparsers.add_parser("check", help="validate release filters and debug regions")
    add_common_args(check)

    clean = subparsers.add_parser("clean", help="remove the release directory")
    add_common_args(clean)

    gitignore = subparsers.add_parser("ensure-gitignore", help="ensure /_release_/ is ignored")
    gitignore.add_argument("--mod-root", type=Path, help="Factorio mod root containing info.json")

    publish = subparsers.add_parser("publish-public", help="publish public.zip to a git branch")
    add_common_args(publish)
    publish.add_argument("--branch", default="main", help="target branch; defaults to main")
    publish.add_argument("--build-first", action="store_true", help="run build before publishing")
    publish.add_argument("--dry-run", action="store_true", help="show publish plan without mutating git state")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the deploy CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ensure-gitignore":
        mod_root = find_mod_root(Path(args.mod_root) if args.mod_root else Path(__file__))
        ensure_gitignore(mod_root)
        return 0

    config = make_config(args)
    if args.command == "build":
        build_release(config)
    elif args.command == "check":
        check_release(config)
    elif args.command == "clean":
        clean_release(config)
    elif args.command == "publish-public":
        publish_public(config, args.branch, args.build_first, args.dry_run)
    else:
        parser.error(f"Unknown command: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
