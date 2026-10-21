#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generate <robot>.yaml for custom robot onboarding.

Normal usage:
    python3 /obs/scripts/generate_robot_yaml.py \
      --package-path /data/workspace/eail/Assets/robots/robot_abc/abc_config \
      --urdf /data/workspace/eail/Assets/robots/robot_abc/abc_description/urdf/abc.urdf

Xacro input is also supported explicitly:
    python3 /obs/scripts/generate_robot_yaml.py \
      --package-path /data/workspace/eail/Assets/robots/robot_abc/abc_config \
      --urdf /data/workspace/eail/Assets/robots/robot_abc/abc_description/urdf/abc.urdf.xacro

The MoveIt config package and robot-description paths are explicit. For a
*.xacro / *.urdf.xacro input, the script expands Xacro first and then runs the
same URDF parsing logic. It does not scan the workspace or guess the input file.

Final design rules
==================
1. fixed_base is NEVER guessed. Output: fixed_base: ""
2. Only single-arm and dual-arm robots are supported.
3. Arm detection follows patch_moveit_config.py semantics:
      arm candidates = SRDF <chain> groups ∩ kinematics.yaml groups
      1 candidate -> single arm
      2 candidates -> dual arm, and exactly one candidate must contain 'left'
                      and exactly one candidate must contain 'right'
      otherwise -> fail, never guess
   NOTE: left/right matching is ONLY done inside arm candidates, never across all
   SRDF groups, so groups such as r1_left_grippers do not interfere.
4. Arm joint order comes from the URDF kinematic path base_link -> tip_link.
   fixed joints are skipped; all other joint types are kept.
5. Gripper groups are NOT inferred from group names.
   Single arm:
      group_state 'open' and 'close' must belong to the same SRDF group.
   Dual arm:
      'left_open'/'left_close' must belong to the same SRDF group;
      'right_open'/'right_close' must belong to the same SRDF group;
      left and right gripper groups must be different.
   If no gripper states are configured at all, gripper sections are omitted.
   If a side is partially configured or ambiguous, generation fails.
6. Gripper membership comes from the SRDF group's direct joints and the joints
   present in its open/close Robot Pose states. Output joint_names follow URDF
   declaration order, never SRDF click/declaration order. URDF fixed joints are
   filtered out. Incomplete state values are warned about and emitted partially.
   Direct-joint torso groups also follow URDF declaration order.
7. init_joints comes ONLY from group_state 'init_pose'.
   It is independent from gripper open/close. If init_pose includes gripper
   joints, they are preserved; if not, no gripper initial values are added.
   If init_pose does not exist, init_joints is omitted.
8. Exactly ONE JointTrajectoryController is supported by the current O3DE
   integration. ros2_controllers.yaml and moveit_controllers.yaml must:
      - name the same controller
      - contain the same joint set (list order may differ)
   Other movable URDF joints are allowed and do not need to belong to the trajectory controller.
9. topic_controller is generated ONLY for platform roles:
      single: arm, optional hand, optional torso
       dual:   left_arm, right_arm, optional left_gripper, optional right_gripper,
               optional torso
   A torso is generated only when SRDF contains exactly one direct-joint group
   whose name contains 'torso' (case-insensitive). Other extra SRDF groups are ignored.
10. Topic names follow the current platform convention and are not guessed from
    Planning Group names.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


# -----------------------------------------------------------------------------
# Platform topic conventions
# -----------------------------------------------------------------------------

ARM_TOPIC_SINGLE = "/arm_position_controller/commands"
HAND_TOPIC_SINGLE = "/hand_position_controller/commands"
HAND_COMMAND_TOPIC_SINGLE = "/hand_gripper_controller"

LEFT_ARM_TOPIC = "/left_arm_position_controller/commands"
RIGHT_ARM_TOPIC = "/right_arm_position_controller/commands"
LEFT_GRIPPER_TOPIC = "/left_gripper_position_controller/commands"
RIGHT_GRIPPER_TOPIC = "/right_gripper_position_controller/commands"
LEFT_GRIPPER_COMMAND_TOPIC = "/left_gripper_controller"
RIGHT_GRIPPER_COMMAND_TOPIC = "/right_gripper_controller"
TORSO_TOPIC = "/torso_position_controller/commands"



# -----------------------------------------------------------------------------
# Generic readers
# -----------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _parse_xml(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Failed to parse XML file {path}: {exc}") from exc


def _parse_robot_description(path: Path) -> ET.Element:
    """Return the final URDF XML root for a .urdf or Xacro input.

    Plain URDF is parsed directly. Files ending in .xacro (including
    .urdf.xacro) are expanded with the ROS xacro executable first, so all
    macros/includes/properties are resolved before the existing URDF logic runs.
    """
    if path.name.lower().endswith(".xacro"):
        xacro_executable = shutil.which("xacro")
        if xacro_executable is None:
            raise ValueError(
                "Xacro input was provided but the 'xacro' executable was not found in PATH. "
                "Source the ROS environment first, e.g. 'source /opt/ros/humble/setup.bash'."
            )

        try:
            result = subprocess.run(
                [xacro_executable, str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise ValueError(f"Failed to expand Xacro file {path}{suffix}") from exc

        try:
            return ET.fromstring(result.stdout)
        except ET.ParseError as exc:
            raise ValueError(
                f"Xacro expansion did not produce valid URDF XML for {path}: {exc}"
            ) from exc

    return _parse_xml(path)


# -----------------------------------------------------------------------------
# URDF
# -----------------------------------------------------------------------------

def _parse_urdf(urdf_path: Path):
    """Parse URDF/Xacro joints and build child_link -> parent joint map."""
    root = _parse_robot_description(urdf_path)

    joints: "OrderedDict[str, dict]" = OrderedDict()
    child_to_joint: Dict[str, dict] = {}

    for elem in root.findall("joint"):
        name = elem.get("name")
        joint_type = elem.get("type")
        parent_elem = elem.find("parent")
        child_elem = elem.find("child")

        if not name or not joint_type or parent_elem is None or child_elem is None:
            continue

        parent_link = parent_elem.get("link")
        child_link = child_elem.get("link")
        if not parent_link or not child_link:
            continue

        info = {
            "name": name,
            "type": joint_type,
            "parent": parent_link,
            "child": child_link,
        }
        joints[name] = info

        if child_link in child_to_joint:
            raise ValueError(
                f"URDF is not a tree: child link {child_link!r} has multiple parent joints"
            )
        child_to_joint[child_link] = info

    if not joints:
        raise ValueError(f"No joints found in URDF: {urdf_path}")

    return joints, child_to_joint


def _all_movable_urdf_joints(joints: "OrderedDict[str, dict]") -> List[str]:
    """Return every non-fixed URDF joint in URDF declaration order."""
    return [
        joint["name"]
        for joint in joints.values()
        if joint["type"] != "fixed"
    ]


def _urdf_ordered_non_fixed_joint_names(
    candidate_names: List[str],
    urdf_joints: "OrderedDict[str, dict]",
    context: str,
) -> List[str]:
    """Filter fixed joints and order known joints by URDF declaration order.

    SRDF group declaration order is user-editable and must not determine the
    Float64MultiArray value order. A joint absent from the supplied URDF cannot
    be safely ordered or classified, so it is warned about and omitted instead
    of blocking this semi-automatic generator.
    """
    candidate_set = set(candidate_names)
    ordered = [
        name
        for name, info in urdf_joints.items()
        if name in candidate_set and info["type"] != "fixed"
    ]
    unknown = [name for name in candidate_names if name not in urdf_joints]
    if unknown:
        print(
            f"[WARN] {context}: joints not found in URDF and omitted from the generated joint list: {unknown}",
            file=sys.stderr,
        )
    return ordered


def _chain_joint_names(
    base_link: str,
    tip_link: str,
    child_to_joint: Dict[str, dict],
) -> List[str]:
    """Return movable joints on the URDF path base_link -> tip_link.

    SRDF serial-chain semantics define base_link as the parent link of the first
    joint and tip_link as the child link of the last joint. We therefore walk
    from tip back to base through URDF parent joints, reverse the path, then skip
    fixed joints.
    """
    reverse_path: List[dict] = []
    current_link = tip_link
    visited = set()

    while current_link != base_link:
        if current_link in visited:
            raise ValueError(
                f"Cycle detected while resolving URDF chain "
                f"{base_link!r} -> {tip_link!r}"
            )
        visited.add(current_link)

        joint = child_to_joint.get(current_link)
        if joint is None:
            raise ValueError(
                f"Cannot resolve URDF chain {base_link!r} -> {tip_link!r}: "
                f"link {current_link!r} has no parent joint before reaching base_link"
            )

        reverse_path.append(joint)
        current_link = joint["parent"]

    result: List[str] = []
    for joint in reversed(reverse_path):
        if joint["type"] == "fixed":
            continue
        result.append(joint["name"])

    if not result:
        raise ValueError(
            f"Arm chain {base_link!r} -> {tip_link!r} has no movable joints"
        )
    return result


# -----------------------------------------------------------------------------
# SRDF
# -----------------------------------------------------------------------------

def _parse_srdf(srdf_path: Path):
    root = _parse_xml(srdf_path)

    groups: "OrderedDict[str, dict]" = OrderedDict()
    for group_elem in root.findall("group"):
        name = group_elem.get("name")
        if not name:
            continue

        chain = None
        chain_elem = group_elem.find("chain")
        if chain_elem is not None:
            base_link = chain_elem.get("base_link")
            tip_link = chain_elem.get("tip_link")
            if base_link and tip_link:
                chain = (base_link, tip_link)

        groups[name] = {
            "chain": chain,
            "joints": [
                e.get("name")
                for e in group_elem.findall("joint")
                if e.get("name")
            ],
            "subgroups": [
                e.get("name")
                for e in group_elem.findall("group")
                if e.get("name")
            ],
        }

    group_states: List[dict] = []
    for state_elem in root.findall("group_state"):
        state_name = state_elem.get("name")
        group_name = state_elem.get("group")
        if not state_name or not group_name:
            continue

        values: "OrderedDict[str, float]" = OrderedDict()
        for joint_elem in state_elem.findall("joint"):
            joint_name = joint_elem.get("name")
            raw_value = joint_elem.get("value")
            if not joint_name or raw_value is None:
                continue
            try:
                values[joint_name] = float(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid joint value in group_state {state_name!r}: "
                    f"{joint_name}={raw_value!r}"
                ) from exc

        group_states.append(
            {
                "name": state_name,
                "group": group_name,
                "values": values,
            }
        )

    return groups, group_states


def _find_states_by_name(group_states: List[dict], name: str) -> List[dict]:
    return [state for state in group_states if state["name"] == name]


def _find_unique_state(group_states: List[dict], name: str) -> Optional[dict]:
    matches = _find_states_by_name(group_states, name)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(
            f"group_state name {name!r} is ambiguous: found in groups "
            f"{[state['group'] for state in matches]}. State names used by the "
            "generator must be unique."
        )
    return matches[0]


def _find_state_in_group(
    group_states: List[dict],
    state_name: str,
    group_name: str,
) -> Optional[dict]:
    matches = [
        state
        for state in group_states
        if state["name"] == state_name and state["group"] == group_name
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(
            f"Duplicate group_state {state_name!r} for group {group_name!r}"
        )
    return matches[0]


def _resolve_group_joint_order(
    group_name: str,
    groups: dict,
    child_to_joint: Dict[str, dict],
    resolving: Optional[set] = None,
) -> List[str]:
    """Resolve an SRDF group's semantic joint order.

    - direct <joint> entries keep SRDF declaration order
    - a <chain> uses URDF base->tip movable-joint order
    - subgroups are expanded in SRDF subgroup declaration order
    """
    if group_name not in groups:
        raise ValueError(f"SRDF group {group_name!r} does not exist")

    if resolving is None:
        resolving = set()
    if group_name in resolving:
        raise ValueError(f"Recursive SRDF subgroup reference at {group_name!r}")

    resolving.add(group_name)
    cfg = groups[group_name]
    result: List[str] = list(cfg.get("joints", []))

    if cfg.get("chain") is not None:
        base_link, tip_link = cfg["chain"]
        result.extend(_chain_joint_names(base_link, tip_link, child_to_joint))

    for subgroup in cfg.get("subgroups", []):
        result.extend(
            _resolve_group_joint_order(
                subgroup,
                groups,
                child_to_joint,
                resolving,
            )
        )

    resolving.remove(group_name)

    deduped: List[str] = []
    seen = set()
    for joint_name in result:
        if joint_name not in seen:
            seen.add(joint_name)
            deduped.append(joint_name)
    return deduped


def _ordered_state_values(
    state: dict,
    groups: dict,
    child_to_joint: Dict[str, dict],
) -> "OrderedDict[str, float]":
    """Order init_pose by the group's semantic order, matching values by name."""
    if state["group"] not in groups:
        raise ValueError(
            f"init_pose references unknown SRDF group {state['group']!r}"
        )

    group_order = _resolve_group_joint_order(
        state["group"], groups, child_to_joint
    )
    group_set = set(group_order)

    extra = [name for name in state["values"] if name not in group_set]
    if extra:
        raise ValueError(
            f"init_pose contains joints not belonging to group "
            f"{state['group']!r}: {extra}"
        )

    result: "OrderedDict[str, float]" = OrderedDict()
    for joint_name in group_order:
        if joint_name in state["values"]:
            result[joint_name] = state["values"][joint_name]
    return result


# -----------------------------------------------------------------------------
# Arm detection (same policy as patch_moveit_config.py)
# -----------------------------------------------------------------------------

def _kinematics_groups(kinematics_path: Path) -> List[str]:
    data = _load_yaml(kinematics_path)
    return [name for name, cfg in data.items() if isinstance(cfg, dict)]


def _detect_arms(groups: dict, kinematics_path: Path) -> dict:
    chain_groups = {
        name for name, cfg in groups.items() if cfg.get("chain") is not None
    }
    candidates = [
        name for name in _kinematics_groups(kinematics_path)
        if name in chain_groups
    ]

    if len(candidates) == 1:
        return {
            "arm_type": "single",
            "arm_group": candidates[0],
        }

    if len(candidates) == 2:
        # IMPORTANT: left/right is checked ONLY inside arm candidates.
        left = [name for name in candidates if "left" in name.lower()]
        right = [name for name in candidates if "right" in name.lower()]

        if len(left) != 1 or len(right) != 1 or left[0] == right[0]:
            raise ValueError(
                "Dual-arm robot detected, but left/right arm roles cannot be "
                "identified safely. Arm candidates are "
                f"{candidates}; left matches={left}; right matches={right}. "
                "For the current dual-arm convention, the two ARM candidate "
                "group names must make left/right unambiguous. Other SRDF "
                "groups are ignored during this check."
            )

        return {
            "arm_type": "dual",
            "left_arm_group": left[0],
            "right_arm_group": right[0],
        }

    if len(candidates) == 0:
        raise ValueError(
            "No arm planning group detected. Expected exactly one or two groups "
            "that are both SRDF <chain> groups and entries in kinematics.yaml."
        )

    raise ValueError(
        f"Unsupported robot: detected {len(candidates)} arm planning groups "
        f"{candidates}. Current onboarding supports only single-arm and dual-arm robots."
    )


# -----------------------------------------------------------------------------
# Gripper detection from explicit Robot Pose state names (never group-name guess)
# -----------------------------------------------------------------------------

def _direct_joint_group(
    groups: dict,
    group_name: str,
    urdf_joints: "OrderedDict[str, dict]",
) -> List[str]:
    if group_name not in groups:
        raise ValueError(f"SRDF gripper group {group_name!r} does not exist")

    joints = list(groups[group_name].get("joints", []))
    if not joints:
        raise ValueError(
            f"Gripper group {group_name!r} must explicitly list its joints with "
            "<joint name=.../> entries."
        )
    if len(joints) != len(set(joints)):
        raise ValueError(
            f"Duplicate joints in gripper group {group_name!r}: {joints}"
        )

    # SRDF determines membership only. The generated array order always follows
    # the URDF declaration, because SRDF joint order may reflect arbitrary UI clicks.
    return _urdf_ordered_non_fixed_joint_names(
        joints, urdf_joints, f"Gripper group {group_name!r}"
    )


def _gripper_joint_names_from_states(
    group_joint_names: List[str],
    open_state: dict,
    close_state: dict,
) -> List[str]:
    """Keep group members actually represented by either pose state.

    group_joint_names is already filtered and ordered by URDF declaration order.
    """
    state_joints = set(open_state["values"]) | set(close_state["values"])
    return [name for name in group_joint_names if name in state_joints]


def _state_values_for_gripper(
    state: dict,
    expected_group: str,
    joint_names: List[str],
    urdf_joints: "OrderedDict[str, dict]",
) -> "OrderedDict[str, float]":
    if state["group"] != expected_group:
        raise ValueError(
            f"State {state['name']!r} belongs to group {state['group']!r}, "
            f"expected {expected_group!r}"
        )

    # Do not require open/close states to cover exactly the same joint set.
    # MoveIt may omit fixed joints or leave a pose partially specified; preserve
    # available values in the same URDF order and warn instead of aborting YAML.
    missing = [joint_name for joint_name in joint_names if joint_name not in state["values"]]
    if missing:
        print(
            f"[WARN] Robot Pose state {state['name']!r} for gripper group "
            f"{expected_group!r} has no values for {missing}; those entries are omitted from this pose.",
            file=sys.stderr,
        )

    return OrderedDict(
        (joint_name, state["values"][joint_name])
        for joint_name in joint_names
        if joint_name in state["values"]
        and urdf_joints.get(joint_name, {}).get("type") != "fixed"
    )


def _resolve_single_gripper(
    groups: dict,
    group_states: List[dict],
    urdf_joints: "OrderedDict[str, dict]",
) -> Optional[dict]:
    opens = _find_states_by_name(group_states, "open")
    closes = _find_states_by_name(group_states, "close")

    if not opens and not closes:
        return None

    if len(opens) != 1 or len(closes) != 1:
        raise ValueError(
            "Single-arm gripper configuration is incomplete or ambiguous. "
            "Exactly one group_state named 'open' and one named 'close' are "
            "required when a gripper is configured."
        )

    open_state = opens[0]
    close_state = closes[0]
    if open_state["group"] != close_state["group"]:
        raise ValueError(
            "Single-arm 'open' and 'close' states must belong to the same "
            f"gripper group, got {open_state['group']!r} and {close_state['group']!r}."
        )

    group_name = open_state["group"]
    group_joint_names = _direct_joint_group(groups, group_name, urdf_joints)
    joint_names = _gripper_joint_names_from_states(
        group_joint_names, open_state, close_state
    )

    return {
        "group": group_name,
        "joint_names": joint_names,
        "open": _state_values_for_gripper(
            open_state, group_name, joint_names, urdf_joints
        ),
        "close": _state_values_for_gripper(
            close_state, group_name, joint_names, urdf_joints
        ),
    }


def _resolve_dual_grippers(
    groups: dict,
    group_states: List[dict],
    urdf_joints: "OrderedDict[str, dict]",
) -> dict:
    state_names = ["left_open", "left_close", "right_open", "right_close"]
    states = {name: _find_states_by_name(group_states, name) for name in state_names}

    if all(not matches for matches in states.values()):
        return {"left": None, "right": None}

    # Once any dual-gripper state exists, require the complete four-state contract.
    for name, matches in states.items():
        if len(matches) != 1:
            raise ValueError(
                "Dual-arm gripper configuration is incomplete or ambiguous. "
                "Exactly one each of left_open, left_close, right_open and "
                f"right_close is required. State {name!r} has {len(matches)} matches."
            )

    left_open = states["left_open"][0]
    left_close = states["left_close"][0]
    right_open = states["right_open"][0]
    right_close = states["right_close"][0]

    if left_open["group"] != left_close["group"]:
        raise ValueError(
            "left_open and left_close must belong to the same gripper group, "
            f"got {left_open['group']!r} and {left_close['group']!r}."
        )
    if right_open["group"] != right_close["group"]:
        raise ValueError(
            "right_open and right_close must belong to the same gripper group, "
            f"got {right_open['group']!r} and {right_close['group']!r}."
        )

    left_group = left_open["group"]
    right_group = right_open["group"]
    if left_group == right_group:
        raise ValueError(
            f"Left and right grippers cannot use the same SRDF group {left_group!r}."
        )

    left_group_joints = _direct_joint_group(groups, left_group, urdf_joints)
    right_group_joints = _direct_joint_group(groups, right_group, urdf_joints)
    left_joints = _gripper_joint_names_from_states(
        left_group_joints, left_open, left_close
    )
    right_joints = _gripper_joint_names_from_states(
        right_group_joints, right_open, right_close
    )

    return {
        "left": {
            "group": left_group,
            "joint_names": left_joints,
            "open": _state_values_for_gripper(
                left_open, left_group, left_joints, urdf_joints
            ),
            "close": _state_values_for_gripper(
                left_close, left_group, left_joints, urdf_joints
            ),
        },
        "right": {
            "group": right_group,
            "joint_names": right_joints,
            "open": _state_values_for_gripper(
                right_open, right_group, right_joints, urdf_joints
            ),
            "close": _state_values_for_gripper(
                right_close, right_group, right_joints, urdf_joints
            ),
        },
    }


def _optional_torso_joints(
    groups: dict,
    urdf_joints: "OrderedDict[str, dict]",
) -> Optional[List[str]]:
    """Return joints from the unique direct-joint Planning Group containing 'torso'."""
    torso_groups = [
        name for name in groups
        if "torso" in name.lower()
    ]

    if not torso_groups:
        return None

    if len(torso_groups) != 1:
        raise ValueError(
            f"Ambiguous torso Planning Groups: {torso_groups}. "
            "Exactly one SRDF group name containing 'torso' is required."
        )

    group_name = torso_groups[0]
    cfg = groups[group_name]

    if cfg.get("chain") is not None:
        raise ValueError(
            f"SRDF torso Planning Group {group_name!r} must be a direct-joint group, "
            "not a <chain> group"
        )

    if cfg.get("subgroups"):
        raise ValueError(
            f"SRDF torso Planning Group {group_name!r} must list joints directly; "
            "subgroups are not supported for automatic torso generation"
        )

    group_joints = list(cfg.get("joints", []))
    if not group_joints:
        raise ValueError(
            f"SRDF torso Planning Group {group_name!r} contains no direct <joint> entries"
        )

    if len(group_joints) != len(set(group_joints)):
        raise ValueError(
            f"SRDF torso Planning Group {group_name!r} contains duplicate joints: {group_joints}"
        )

    joints = _urdf_ordered_non_fixed_joint_names(
        group_joints, urdf_joints, f"Torso group {group_name!r}"
    )
    if not joints:
        raise ValueError(
            f"SRDF torso Planning Group {group_name!r} contains no non-fixed joints"
        )

    return joints


# -----------------------------------------------------------------------------
# Controller contract validation
# -----------------------------------------------------------------------------

def _parse_ros2_trajectory_controllers(path: Path) -> Dict[str, List[str]]:
    data = _load_yaml(path)
    manager = data.get("controller_manager", {}).get("ros__parameters", {})
    if not isinstance(manager, dict):
        raise ValueError(
            f"Invalid ros2_controllers.yaml: controller_manager.ros__parameters missing in {path}"
        )

    result: Dict[str, List[str]] = OrderedDict()
    for name, declaration in manager.items():
        if not isinstance(declaration, dict):
            continue
        if declaration.get("type") != "joint_trajectory_controller/JointTrajectoryController":
            continue

        controller_cfg = data.get(name, {}).get("ros__parameters", {})
        joints = controller_cfg.get("joints", []) if isinstance(controller_cfg, dict) else []
        if not isinstance(joints, list) or not joints:
            raise ValueError(
                f"JointTrajectoryController {name!r} has no non-empty joints list in {path}"
            )
        result[name] = [str(joint) for joint in joints]

    return result


def _parse_moveit_trajectory_controllers(path: Path) -> Dict[str, List[str]]:
    data = _load_yaml(path)
    manager = data.get("moveit_simple_controller_manager", {})
    if not isinstance(manager, dict):
        raise ValueError(
            f"Invalid moveit_controllers.yaml: moveit_simple_controller_manager missing in {path}"
        )

    names = manager.get("controller_names", [])
    if not isinstance(names, list):
        raise ValueError(
            f"Invalid moveit_controllers.yaml: controller_names must be a list in {path}"
        )

    result: Dict[str, List[str]] = OrderedDict()
    for raw_name in names:
        name = str(raw_name)
        cfg = manager.get(name, {})
        if not isinstance(cfg, dict):
            raise ValueError(
                f"MoveIt controller {name!r} has no config block in {path}"
            )
        if cfg.get("type") != "FollowJointTrajectory":
            continue
        joints = cfg.get("joints", [])
        if not isinstance(joints, list) or not joints:
            raise ValueError(
                f"MoveIt FollowJointTrajectory controller {name!r} has no non-empty joints list in {path}"
            )
        result[name] = [str(joint) for joint in joints]

    return result


def _validate_controller_contract(
    ros2_controllers_path: Path,
    moveit_controllers_path: Path,
    all_movable_urdf_joints: List[str],
) -> Tuple[str, List[str]]:
    ros2 = _parse_ros2_trajectory_controllers(ros2_controllers_path)
    moveit = _parse_moveit_trajectory_controllers(moveit_controllers_path)

    if len(ros2) != 1:
        raise ValueError(
            "Current O3DE integration requires exactly ONE "
            "JointTrajectoryController in ros2_controllers.yaml. "
            f"Found: {list(ros2.keys())}"
        )
    if len(moveit) != 1:
        raise ValueError(
            "Current O3DE integration requires exactly ONE MoveIt "
            "FollowJointTrajectory controller in moveit_controllers.yaml. "
            f"Found: {list(moveit.keys())}"
        )

    ros2_name, ros2_joints = next(iter(ros2.items()))
    moveit_name, moveit_joints = next(iter(moveit.items()))

    if ros2_name != moveit_name:
        raise ValueError(
            "Controller name mismatch between ros2_controllers.yaml and "
            f"moveit_controllers.yaml: {ros2_name!r} != {moveit_name!r}"
        )

    ros2_set = set(ros2_joints)
    moveit_set = set(moveit_joints)
    if ros2_set != moveit_set:
        missing_in_moveit = [j for j in ros2_joints if j not in moveit_set]
        missing_in_ros2 = [j for j in moveit_joints if j not in ros2_set]
        raise ValueError(
            "Controller joint mismatch between ros2_controllers.yaml and "
            "moveit_controllers.yaml. They must contain the same joint set; "
            "list order may differ. "
            f"missing_in_moveit={missing_in_moveit}, "
            f"missing_in_ros2={missing_in_ros2}"
        )

    if len(ros2_joints) != len(ros2_set):
        raise ValueError(
            f"Controller {ros2_name!r} contains duplicate joints in "
            f"ros2_controllers.yaml: {ros2_joints}"
        )
    if len(moveit_joints) != len(moveit_set):
        raise ValueError(
            f"Controller {moveit_name!r} contains duplicate joints in "
            f"moveit_controllers.yaml: {moveit_joints}"
        )

    return ros2_name, ros2_joints


# -----------------------------------------------------------------------------
# Explicit input path resolution
# -----------------------------------------------------------------------------

def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise ValueError(f"{label} not found: {path}")
    return path


def _find_unique_file(directory: Path, pattern: str, label: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {label} under {directory}, found: "
            f"{[p.name for p in matches]}"
        )
    return matches[0]


def _resolve_paths(args) -> dict:
    package_path = Path(args.package_path).expanduser().resolve()
    if not package_path.is_dir():
        raise ValueError(f"MoveIt config package not found: {package_path}")

    package_name = package_path.name
    if not package_name.endswith("_config") or package_name == "_config":
        raise ValueError(
            "--package-path must point to a <robot>_config package, got: "
            f"{package_path}"
        )

    robot = package_name[:-len("_config")]
    robot_root = package_path.parent
    config_dir = package_path / "config"
    if not config_dir.is_dir():
        raise ValueError(f"MoveIt config directory not found: {config_dir}")

    urdf = _require_file(
        Path(args.urdf).expanduser().resolve(),
        "URDF/Xacro",
    )

    preferred_srdf = config_dir / f"{robot}.srdf"
    srdf = (
        preferred_srdf
        if preferred_srdf.is_file()
        else _find_unique_file(config_dir, "*.srdf", "SRDF")
    )

    kinematics = _require_file(config_dir / "kinematics.yaml", "kinematics.yaml")
    ros2_controllers = _require_file(
        config_dir / "ros2_controllers.yaml",
        "ros2_controllers.yaml",
    )
    moveit_controllers = _require_file(
        config_dir / "moveit_controllers.yaml",
        "moveit_controllers.yaml",
    )

    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else robot_root / f"{robot}.yaml"
    )

    return {
        "package_path": package_path,
        "robot": robot,
        "urdf": urdf,
        "srdf": srdf,
        "kinematics": kinematics,
        "ros2_controllers": ros2_controllers,
        "moveit_controllers": moveit_controllers,
        "output": output,
    }

# -----------------------------------------------------------------------------
# YAML generation
# -----------------------------------------------------------------------------

def _make_gripper_spec(
    topic_name: str,
    command_topic: str,
    gripper: dict,
) -> OrderedDict:
    return OrderedDict(
        [
            ("topic_name", topic_name),
            ("gripper_command_topic", command_topic),
            ("joint_names", gripper["joint_names"]),
            (
                "gripper_positions",
                OrderedDict(
                    [
                        ("open", gripper["open"]),
                        ("close", gripper["close"]),
                    ]
                ),
            ),
        ]
    )


def generate_config(paths: dict) -> Tuple[OrderedDict, dict]:
    urdf_joints, child_to_joint = _parse_urdf(paths["urdf"])
    groups, group_states = _parse_srdf(paths["srdf"])

    all_movable = _all_movable_urdf_joints(urdf_joints)
    arm_info = _detect_arms(groups, paths["kinematics"])
    moveit_controller, controller_joints = _validate_controller_contract(
        paths["ros2_controllers"],
        paths["moveit_controllers"],
        all_movable,
    )

    cfg: OrderedDict = OrderedDict()
    # Intentionally blank. User/platform must provide O3DE articulation fixed base.
    cfg["fixed_base"] = ""
    cfg["moveit_controller"] = moveit_controller

    topic_controller: OrderedDict = OrderedDict()
    unresolved_fields: List[str] = []

    def _resolve_arm_joints_or_empty(
        base_link: str, tip_link: str, field_name: str
    ) -> List[str]:
        try:
            # Arms are serial chains, so retain their semantic base->tip order.
            # The URDF declaration-order sorting is for direct-joint groups whose
            # SRDF order can be changed by arbitrary UI selections.
            return _chain_joint_names(base_link, tip_link, child_to_joint)
        except ValueError as exc:
            print(f"[WARN] {exc}", file=sys.stderr)
            print(
                f"[WARN] Cannot infer {field_name}. Please add/confirm the arm "
                f"tool link {tip_link!r} in the URDF and connect it to the arm "
                "end link with a joint, or correct the SRDF Planning Group tip_link. "
                "The field is left empty for manual completion.",
                file=sys.stderr,
            )
            unresolved_fields.append(field_name)
            return []

    if arm_info["arm_type"] == "single":
        arm_group = arm_info["arm_group"]
        base_link, tip_link = groups[arm_group]["chain"]
        arm_joints = _resolve_arm_joints_or_empty(
            base_link, tip_link, "topic_controller.arm.joint_names"
        )

        topic_controller["arm"] = OrderedDict(
            [
                ("topic_name", ARM_TOPIC_SINGLE),
                ("joint_names", arm_joints),
            ]
        )

        gripper = _resolve_single_gripper(groups, group_states, urdf_joints)
        if gripper is not None:
            topic_controller["hand"] = _make_gripper_spec(
                HAND_TOPIC_SINGLE,
                HAND_COMMAND_TOPIC_SINGLE,
                gripper,
            )

    else:
        left_arm_group = arm_info["left_arm_group"]
        right_arm_group = arm_info["right_arm_group"]

        left_base, left_tip = groups[left_arm_group]["chain"]
        right_base, right_tip = groups[right_arm_group]["chain"]

        left_arm_joints = _resolve_arm_joints_or_empty(
            left_base, left_tip, "topic_controller.left_arm.joint_names"
        )
        right_arm_joints = _resolve_arm_joints_or_empty(
            right_base, right_tip, "topic_controller.right_arm.joint_names"
        )

        grippers = _resolve_dual_grippers(groups, group_states, urdf_joints)

        # Output order: left_arm, right_arm, left_gripper, right_gripper.
        topic_controller["left_arm"] = OrderedDict(
            [
                ("topic_name", LEFT_ARM_TOPIC),
                ("joint_names", left_arm_joints),
            ]
        )
        topic_controller["right_arm"] = OrderedDict(
            [
                ("topic_name", RIGHT_ARM_TOPIC),
                ("joint_names", right_arm_joints),
            ]
        )

        if grippers["left"] is not None:
            topic_controller["left_gripper"] = _make_gripper_spec(
                LEFT_GRIPPER_TOPIC,
                LEFT_GRIPPER_COMMAND_TOPIC,
                grippers["left"],
            )
        if grippers["right"] is not None:
            topic_controller["right_gripper"] = _make_gripper_spec(
                RIGHT_GRIPPER_TOPIC,
                RIGHT_GRIPPER_COMMAND_TOPIC,
                grippers["right"],
            )

    torso_joints = _optional_torso_joints(groups, urdf_joints)
    if torso_joints is not None:
        topic_controller["torso"] = OrderedDict(
            [
                ("topic_name", TORSO_TOPIC),
                ("joint_names", torso_joints),
            ]
        )

    cfg["topic_controller"] = topic_controller

    init_pose = _find_unique_state(group_states, "init_pose")
    if init_pose is not None:
        init_joints = _ordered_state_values(
            init_pose,
            groups,
            child_to_joint,
        )
        if init_joints:
            cfg["init_joints"] = init_joints

    debug = {
        "arm_info": arm_info,
        "controller": moveit_controller,
        "controller_joints": controller_joints,
        "movable_urdf_joints": all_movable,
        "unresolved_fields": unresolved_fields,
    }
    return cfg, debug


# -----------------------------------------------------------------------------
# YAML output
# -----------------------------------------------------------------------------

class _Dumper(yaml.SafeDumper):
    pass


def _represent_str(dumper, data):
    if data == "":
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style='"'
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _represent_str)
_Dumper.add_representer(
    OrderedDict,
    lambda dumper, data: dumper.represent_dict(data.items()),
)


def _write_config(cfg: OrderedDict, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)

    body = yaml.dump(
        cfg,
        Dumper=_Dumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=120,
    )

    header = (
        "# Auto-generated from URDF/SRDF/MoveIt config.\n"
        "# REQUIRED: fill fixed_base before running file_convert.py.\n"
        "# fixed_base is intentionally never inferred by this script.\n"
    )
    output.write_text(header + body, encoding="utf-8")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate <robot>.yaml from an explicit MoveIt config package."
    )
    parser.add_argument(
        "--package-path",
        required=True,
        help="MoveIt config package path, e.g. .../robot_abc/abc_config",
    )
    parser.add_argument(
        "--urdf",
        required=True,
        help=(
            "URDF or Xacro file path, e.g. .../abc.urdf or .../abc.urdf.xacro"
        ),
    )
    parser.add_argument(
        "--output",
        help="Optional output path. Default: sibling <robot>.yaml",
    )
    return parser

def main() -> int:
    args = build_arg_parser().parse_args()

    try:
        paths = _resolve_paths(args)
        cfg, debug = generate_config(paths)
        _write_config(cfg, paths["output"])
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Robot: {paths['robot']}")
    if debug.get("unresolved_fields"):
        print(f"[PARTIAL] Generated: {paths['output']}")
        print("[IMPORTANT] Please fill these unresolved fields manually:")
        for field_name in debug["unresolved_fields"]:
            print(f"  - {field_name}")
    else:
        print(f"[OK] Generated: {paths['output']}")
    print(f"[INPUT] Robot description: {paths['urdf']}")
    print(f"[INPUT] SRDF: {paths['srdf']}")
    print(f"[INPUT] kinematics: {paths['kinematics']}")
    print(f"[INPUT] ros2_controllers: {paths['ros2_controllers']}")
    print(f"[INPUT] moveit_controllers: {paths['moveit_controllers']}")
    print(f"[DETECT] arm: {debug['arm_info']}")
    print(f"[DETECT] moveit_controller: {debug['controller']}")
    print(
        "[IMPORTANT] fixed_base is intentionally empty. "
        "Fill it before running file_convert.py."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())