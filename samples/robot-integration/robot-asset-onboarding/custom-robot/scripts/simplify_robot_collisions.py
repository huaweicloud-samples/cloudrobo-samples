#!/usr/bin/env python3
"""Simplify O3DE robot collision assets and prefabs.

The ROS2 robot importer commonly creates PhysX mesh groups with convex
decomposition enabled. For detailed STL collision meshes this can create
hundreds of PhysX shapes per link. This tool provides project-wide cleanup
operations that are not tied to a specific robot name.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PHYSX_MESH_GROUP_TYPE = "{5B03C8E6-8CEE-4DA0-A7FA-CD88689DD45B} MeshGroup"
MESH_COLLIDER_TYPE = "EditorMeshColliderComponent"

QUALITY_PRESETS = {
    "low": {
        "min_hulls": 2,
        "max_hulls": 8,
        "hulls_per_mb": 2.0,
        "max_vertices": 32,
        "resolution": 100000,
        "min_volume_percent_error": 5.0,
        "max_recursion_depth": 6,
        "min_edge_length": 4,
    },
    "medium": {
        "min_hulls": 4,
        "max_hulls": 24,
        "hulls_per_mb": 5.0,
        "max_vertices": 64,
        "resolution": 400000,
        "min_volume_percent_error": 2.5,
        "max_recursion_depth": 8,
        "min_edge_length": 3,
    },
    "high": {
        "min_hulls": 6,
        "max_hulls": 32,
        "hulls_per_mb": 7.0,
        "max_vertices": 80,
        "resolution": 600000,
        "min_volume_percent_error": 1.8,
        "max_recursion_depth": 8,
        "min_edge_length": 3,
    },
    "ultra": {
        "min_hulls": 16,
        "max_hulls": 128,
        "hulls_per_mb": 18.0,
        "max_vertices": 128,
        "resolution": 2000000,
        "min_volume_percent_error": 0.5,
        "max_recursion_depth": 12,
        "min_edge_length": 1,
    },
}


@dataclass
class ChangeSet:
    files_seen: int = 0
    files_changed: int = 0
    physics_groups_seen: int = 0
    physics_groups_changed: int = 0
    mesh_colliders_seen: int = 0
    mesh_colliders_removed: int = 0


@dataclass
class ResolvedInputs:
    assetinfo_files: list[Path]
    prefab_files: list[Path]
    resolved_from_prefabs: int = 0


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def save_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=4)
        stream.write("\n")


def backup(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".collision_bak")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def is_physx_mesh_group(value: Any) -> bool:
    return isinstance(value, dict) and value.get("$type") == PHYSX_MESH_GROUP_TYPE


def source_asset_size_mb(assetinfo_path: Path) -> float:
    source_path = assetinfo_path.with_suffix("")
    if source_path.exists():
        return max(source_path.stat().st_size / (1024 * 1024), 0.001)
    return 1.0


def clamp_int(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


def build_decomposition_params(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    preset = dict(QUALITY_PRESETS[args.quality])
    min_hulls = args.min_hulls if args.min_hulls is not None else preset["min_hulls"]
    max_hulls = args.max_hulls if args.max_hulls is not None else preset["max_hulls"]
    hulls_per_mb = args.hulls_per_mb if args.hulls_per_mb is not None else preset["hulls_per_mb"]
    if args.fixed_hulls is not None:
        hulls = args.fixed_hulls
    else:
        hulls = clamp_int(source_asset_size_mb(path) * hulls_per_mb, min_hulls, max_hulls)

    return {
        "MaxConvexHulls": hulls,
        "Resolution": args.resolution or preset["resolution"],
        "MaxNumVerticesPerConvexHull": args.max_vertices_per_hull or preset["max_vertices"],
        "MinVolumePercentError": (
            args.min_volume_percent_error
            if args.min_volume_percent_error is not None
            else preset["min_volume_percent_error"]
        ),
        "MaxRecursionDepth": args.max_recursion_depth or preset["max_recursion_depth"],
        "ShrinkWrap": not args.no_shrink_wrap,
        "MinEdgeLength": args.min_edge_length or preset["min_edge_length"],
    }


def simplify_assetinfo(path: Path, args: argparse.Namespace) -> tuple[bool, ChangeSet, dict[str, Any] | None]:
    data = load_json(path)
    stats = ChangeSet(files_seen=1)
    values = data.get("values", [])
    if not isinstance(values, list):
        return False, stats, None

    new_values: list[Any] = []
    changed = False
    decompose_params = None

    for value in values:
        if not is_physx_mesh_group(value):
            new_values.append(value)
            continue

        stats.physics_groups_seen += 1

        if args.assetinfo_mode == "remove":
            stats.physics_groups_changed += 1
            changed = True
            continue

        if args.assetinfo_mode == "single-convex":
            if value.get("DecomposeMeshes") is not False:
                value["DecomposeMeshes"] = False
                stats.physics_groups_changed += 1
                changed = True
            new_values.append(value)
            continue

        if args.assetinfo_mode == "decompose":
            decompose_params = build_decomposition_params(path, args)
            desired = {
                "export method": 1,
                "DecomposeMeshes": True,
                "ConvexDecompositionParams": decompose_params,
            }
            for key, desired_value in desired.items():
                if value.get(key) != desired_value:
                    value[key] = desired_value
                    changed = True
            if changed:
                stats.physics_groups_changed += 1
            new_values.append(value)
            continue

        new_values.append(value)

    if changed:
        data["values"] = new_values
        stats.files_changed = 1
    return changed, stats, decompose_params


def simplify_prefab(path: Path, mode: str) -> tuple[bool, ChangeSet]:
    data = load_json(path)
    stats = ChangeSet(files_seen=1)
    if mode == "none":
        return False, stats

    entities = data.get("Entities", {})
    if not isinstance(entities, dict):
        return False, stats

    changed = False
    for entity in entities.values():
        if not isinstance(entity, dict):
            continue
        components = entity.get("Components", {})
        if not isinstance(components, dict):
            continue

        remove_names = [
            name
            for name, component in components.items()
            if isinstance(component, dict) and component.get("$type") == MESH_COLLIDER_TYPE
        ]
        stats.mesh_colliders_seen += len(remove_names)

        if mode == "remove-mesh-colliders":
            for name in remove_names:
                del components[name]
            stats.mesh_colliders_removed += len(remove_names)
            changed = changed or bool(remove_names)

    if changed:
        stats.files_changed = 1
    return changed, stats


def iter_files(paths: list[Path], suffix: str) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_file() and path.name.endswith(suffix):
            result.append(path)
        elif path.is_dir():
            result.extend(sorted(path.rglob(f"*{suffix}")))
    return sorted(set(result))


def walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(walk_strings(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(walk_strings(child))
        return result
    return []


def source_key_from_assetinfo(path: Path) -> str:
    return path.with_suffix("").as_posix().lower()


def source_key_from_pxmesh_hint(hint: str) -> str | None:
    normalized = hint.replace("\\", "/").lower()
    if not normalized.endswith(".pxmesh"):
        return None
    return normalized.removesuffix(".pxmesh")


def prefab_key_from_hint(hint: str) -> str | None:
    normalized = hint.replace("\\", "/").lower()
    if not normalized.endswith(".prefab"):
        return None
    return normalized


def source_keys_from_prefab(path: Path) -> set[str]:
    data = load_json(path)
    keys: set[str] = set()
    for text in walk_strings(data):
        key = source_key_from_pxmesh_hint(text)
        if key:
            keys.add(key)
    return keys


def prefab_keys_from_prefab(path: Path) -> set[str]:
    data = load_json(path)
    keys: set[str] = set()
    for text in walk_strings(data):
        key = prefab_key_from_hint(text)
        if key:
            keys.add(key)
    return keys


def build_assetinfo_index(paths: list[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in paths:
        key = source_key_from_assetinfo(path)
        index[key] = path
        try:
            index[path.with_suffix("").resolve().as_posix().lower()] = path
        except OSError:
            pass
    return index


def build_prefab_index(paths: list[Path], project_root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in paths:
        index[path.as_posix().lower()] = path
        try:
            resolved = path.resolve()
            index[resolved.as_posix().lower()] = path
            index[resolved.relative_to(project_root.resolve()).as_posix().lower()] = path
        except (OSError, ValueError):
            pass
    return index


def resolve_assetinfo_key(key: str, index: dict[str, Path], project_root: Path) -> Path | None:
    direct = index.get(key)
    if direct:
        return direct

    project_relative = (project_root / key).as_posix().lower()
    direct = index.get(project_relative)
    if direct:
        return direct

    # Some asset hints are product paths such as assets/importer/foo.stl.pxmesh
    # while the source path on disk is Assets/Importer/foo.STL.assetinfo. Match
    # case-insensitively by suffix so prefab inputs stay independent of robot name.
    matches = [path for indexed_key, path in index.items() if indexed_key.endswith(key)]
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_prefab_key(key: str, index: dict[str, Path], project_root: Path) -> Path | None:
    direct = index.get(key)
    if direct:
        return direct

    project_relative = (project_root / key).as_posix().lower()
    direct = index.get(project_relative)
    if direct:
        return direct

    matches = [path for indexed_key, path in index.items() if indexed_key.endswith(key)]
    if len(matches) == 1:
        return matches[0]
    return None


def collect_prefab_closure(
    prefab_files: list[Path],
    prefab_index: dict[str, Path],
    project_root: Path,
) -> list[Path]:
    queue = list(prefab_files)
    seen: set[Path] = set()
    ordered: list[Path] = []

    while queue:
        path = queue.pop(0)
        try:
            resolved_path = path.resolve()
        except OSError:
            resolved_path = path
        if resolved_path in seen:
            continue
        seen.add(resolved_path)
        ordered.append(path)

        for key in prefab_keys_from_prefab(path):
            referenced = resolve_prefab_key(key, prefab_index, project_root)
            if referenced:
                queue.append(referenced)

    return ordered


def resolve_inputs(paths: list[Path], project_root: Path) -> ResolvedInputs:
    direct_assetinfo = iter_files(paths, ".assetinfo")
    prefab_files = iter_files(paths, ".prefab")

    search_roots = [
        path if path.is_dir() else path.parent
        for path in paths
    ]
    if project_root not in search_roots:
        search_roots.append(project_root)
    assetinfo_index = build_assetinfo_index(iter_files(search_roots, ".assetinfo"))
    prefab_index = build_prefab_index(iter_files(search_roots, ".prefab"), project_root)
    prefab_closure = collect_prefab_closure(prefab_files, prefab_index, project_root)

    resolved_from_prefabs: set[Path] = set()
    for prefab_path in prefab_closure:
        for key in source_keys_from_prefab(prefab_path):
            resolved = resolve_assetinfo_key(key, assetinfo_index, project_root)
            if resolved:
                resolved_from_prefabs.add(resolved)

    return ResolvedInputs(
        assetinfo_files=sorted(set(direct_assetinfo) | resolved_from_prefabs),
        prefab_files=prefab_files,
        resolved_from_prefabs=len(resolved_from_prefabs),
    )


def add_stats(total: ChangeSet, current: ChangeSet) -> None:
    total.files_seen += current.files_seen
    total.files_changed += current.files_changed
    total.physics_groups_seen += current.physics_groups_seen
    total.physics_groups_changed += current.physics_groups_changed
    total.mesh_colliders_seen += current.mesh_colliders_seen
    total.mesh_colliders_removed += current.mesh_colliders_removed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simplify O3DE robot collision data in .assetinfo and .prefab files."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Files or directories to scan, for example Assets/Importer Levels",
    )
    parser.add_argument(
        "--assetinfo-mode",
        choices=("report", "decompose", "single-convex", "remove"),
        default="report",
        help=(
            "report only counts PhysX mesh groups; decompose writes a bounded multi-hull V-HACD budget; "
            "single-convex keeps one convex mesh per source by disabling decomposition; "
            "remove deletes PhysX mesh groups from assetinfo files"
        ),
    )
    parser.add_argument(
        "--quality",
        choices=tuple(QUALITY_PRESETS),
        default="medium",
        help="Preset used by --assetinfo-mode decompose.",
    )
    parser.add_argument("--min-hulls", type=int, help="Minimum convex hulls per source asset.")
    parser.add_argument("--max-hulls", type=int, help="Maximum convex hulls per source asset.")
    parser.add_argument(
        "--fixed-hulls",
        type=int,
        help="Use the same hull budget for every source asset instead of size-adaptive budgeting.",
    )
    parser.add_argument(
        "--hulls-per-mb",
        type=float,
        help="Adaptive hull budget multiplier based on source asset file size.",
    )
    parser.add_argument(
        "--max-vertices-per-hull",
        type=int,
        help="Maximum vertices per generated convex hull.",
    )
    parser.add_argument("--resolution", type=int, help="V-HACD voxelization resolution.")
    parser.add_argument(
        "--min-volume-percent-error",
        type=float,
        help="V-HACD minimum volume percent error allowed per hull.",
    )
    parser.add_argument("--max-recursion-depth", type=int, help="V-HACD maximum recursion depth.")
    parser.add_argument("--min-edge-length", type=int, help="V-HACD minimum edge length.")
    parser.add_argument(
        "--no-shrink-wrap",
        action="store_true",
        help="Disable shrink wrapping output hulls to the source mesh.",
    )
    parser.add_argument(
        "--prefab-mode",
        choices=("none", "remove-mesh-colliders"),
        default="none",
        help="Optionally remove mesh collider components from prefabs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .collision_bak files when applying changes.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root used to resolve asset hints from prefab files.",
    )
    args = parser.parse_args()

    totals = ChangeSet()

    resolved_inputs = resolve_inputs(args.paths, args.project_root)
    assetinfo_files = resolved_inputs.assetinfo_files
    prefab_files = resolved_inputs.prefab_files
    preview_params: list[tuple[Path, dict[str, Any]]] = []

    for path in assetinfo_files:
        changed, stats, decompose_params = simplify_assetinfo(path, args)
        add_stats(totals, stats)
        if decompose_params is not None and len(preview_params) < 12:
            preview_params.append((path, decompose_params))

    # Re-run assetinfo writes in a separate simple pass to avoid mutating dry-run
    # state before backup. This keeps reporting deterministic.
    if args.apply:
        for path in assetinfo_files:
            data = load_json(path)
            values = data.get("values", [])
            if not isinstance(values, list):
                continue
            changed = False
            new_values: list[Any] = []
            for value in values:
                if not is_physx_mesh_group(value):
                    new_values.append(value)
                    continue
                if args.assetinfo_mode == "remove":
                    changed = True
                    continue
                if args.assetinfo_mode == "single-convex" and value.get("DecomposeMeshes") is not False:
                    value["DecomposeMeshes"] = False
                    changed = True
                if args.assetinfo_mode == "decompose":
                    desired = {
                        "export method": 1,
                        "DecomposeMeshes": True,
                        "ConvexDecompositionParams": build_decomposition_params(path, args),
                    }
                    for key, desired_value in desired.items():
                        if value.get(key) != desired_value:
                            value[key] = desired_value
                            changed = True
                new_values.append(value)
            if changed:
                if not args.no_backup:
                    backup(path)
                data["values"] = new_values
                save_json(path, data)

    for path in prefab_files:
        changed, stats = simplify_prefab(path, args.prefab_mode)
        add_stats(totals, stats)
        if changed and args.apply:
            data = load_json(path)
            for entity in data.get("Entities", {}).values():
                components = entity.get("Components", {})
                for name in list(components):
                    component = components[name]
                    if isinstance(component, dict) and component.get("$type") == MESH_COLLIDER_TYPE:
                        del components[name]
            if not args.no_backup:
                backup(path)
            save_json(path, data)

    action = "applied" if args.apply else "dry-run"
    print(f"Mode: {action}")
    print(f"Files scanned: {totals.files_seen}")
    print(f"Assetinfo files resolved from prefabs: {resolved_inputs.resolved_from_prefabs}")
    print(f"Files that would change: {totals.files_changed}")
    print(f"PhysX mesh groups seen: {totals.physics_groups_seen}")
    print(f"PhysX mesh groups changed: {totals.physics_groups_changed}")
    print(f"Mesh collider components seen: {totals.mesh_colliders_seen}")
    print(f"Mesh collider components removed: {totals.mesh_colliders_removed}")
    if preview_params:
        print("Decomposition preview:")
        for path, params in preview_params:
            print(
                f"  {path}: hulls={params['MaxConvexHulls']}, "
                f"vertices={params['MaxNumVerticesPerConvexHull']}, "
                f"resolution={params['Resolution']}"
            )
    if not args.apply:
        print("No files were modified. Re-run with --apply to write changes.")


if __name__ == "__main__":
    main()
