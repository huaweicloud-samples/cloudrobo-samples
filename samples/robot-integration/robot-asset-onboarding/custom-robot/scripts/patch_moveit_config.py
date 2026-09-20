#!/usr/bin/env python3
"""
Patch an existing MoveIt Setup Assistant config package for custom robot use.

Full patch mode:
  - generate Pilz planning configuration
  - normalize joint limits
  - update runtime dependencies
  - generate demo.launch.py
  - generate demo_me.launch.py
  - generate rviz_only.launch.py
  - include the custom gripper controller node
  - generate robot_profile.yaml

Profile-only mode:
  --profile-only re-analyzes the current robot configuration and regenerates
  robot_profile.yaml without modifying MoveIt configuration or launch files.

robot_profile data sources:
  - MoveIt package / robot name: MoveIt config package and SRDF
  - planning group / tool link: kinematics.yaml + SRDF
  - base link: prefab config fixed_base
  - arm controller topic: prefab config YAML
  - gripper command topic: Float64 subscriptions detected from gripper C++ source

Provide --package-path for in-place patching, plus an optional small
--reference-config YAML for project-specific extras.
Package name, robot name, and URDF file are inferred from the GUI-generated
package unless explicitly overridden.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PILZ_PIPELINE = "pilz_industrial_motion_planner"
PILZ_DEPEND = "pilz_industrial_motion_planner"

PILZ_PLANNING = {
    "planning_plugin": "pilz_industrial_motion_planner/CommandPlanner",
    "request_adapters": (
        "default_planner_request_adapters/FixWorkspaceBounds "
        "default_planner_request_adapters/FixStartStateBounds "
        "default_planner_request_adapters/FixStartStateCollision "
        "default_planner_request_adapters/FixStartStatePathConstraints"
    ),
    "default_planner_config": "PTP",
    "capabilities": (
        "pilz_industrial_motion_planner/MoveGroupSequenceAction "
        "pilz_industrial_motion_planner/MoveGroupSequenceService"
    ),
}

DEFAULT_CARTESIAN_LIMITS = {
    "max_trans_vel": 1.0,
    "max_trans_acc": 2.25,
    "max_trans_dec": -5.0,
    "max_rot_vel": 1.57,
}


@dataclass(frozen=True)
class ExtraNode:
    package: str
    executable: str
    name: str | None = None
    variable_name: str | None = None
    output: str = "screen"
    respawn: bool = True
    parameters: list[dict[str, Any]] | None = None


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def dump_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def as_path(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def resolve_package_path(root: Path, requested: str | None, reference: dict[str, Any]) -> Path:
    requested_path = as_path(root, requested)
    if requested_path:
        return requested_path

    reference_output = reference.get("package", {}).get("output")
    reference_path = as_path(root, reference_output)
    if reference_path and (reference_path / "config").is_dir():
        return reference_path

    raise FileNotFoundError("pass --package-path, or set package.output in the reference YAML")


def load_reference_config(root: Path, requested: str | None) -> dict[str, Any]:
    reference_path = as_path(root, requested)
    return load_yaml(reference_path) if reference_path else {}


def package_name(package_path: Path, explicit: str | None) -> str:
    if explicit:
        return explicit

    package_xml = package_path / "package.xml"
    if not package_xml.exists():
        return package_path.name
    root = ET.parse(package_xml).getroot()
    name = root.findtext("name")
    return name.strip() if name else package_path.name


def _robot_name_from_srdf(srdf_path: Path) -> str:
    """从 SRDF XML 根元素的 name 属性提取机器人名。"""
    tree = ET.parse(srdf_path)
    root = tree.getroot()
    name = root.get("name")
    if not name:
        raise ValueError(f"SRDF robot name is missing in {srdf_path}")
    return name


def robot_name(package_path: Path, reference: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return explicit

    # 从 SRDF <robot name="..."> 读取，不使用 reference YAML 覆盖
    setup_assistant = load_yaml(package_path / ".setup_assistant")
    srdf_relative = (
        setup_assistant.get("moveit_setup_assistant_config", {})
        .get("srdf", {})
        .get("relative_path")
    )
    if srdf_relative:
        srdf_path = package_path / srdf_relative
        if srdf_path.exists():
            return _robot_name_from_srdf(srdf_path)

    # 回退：config/ 下只有一个 SRDF 时直接解析
    srdf_files = sorted((package_path / "config").glob("*.srdf"))
    if len(srdf_files) == 1:
        return _robot_name_from_srdf(srdf_files[0])

    raise ValueError("robot name not found; pass --robot-name")


def urdf_file(package_path: Path, reference: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return explicit

    robot_config = reference.get("robot", {})
    configured = robot_config.get("urdf_file") or robot_config.get("generated_urdf_file")
    if configured and (package_path / "config" / configured).exists():
        return str(configured)

    # 1. 在 config/config 目录下找 .urdf.xacro / .xacro / .urdf
    for pattern in ("*.urdf.xacro", "*.xacro", "*.urdf"):
        found = sorted((package_path / "config").glob(pattern))
        if found:
            return found[0].name

    # 2. 在同级 description 包的 urdf/ 目录下找
    for desc_dir in package_path.parent.glob("*_description"):
        urdf_dir = desc_dir / "urdf"
        if urdf_dir.is_dir():
            for pattern in ("*.urdf.xacro", "*.xacro", "*.urdf"):
                found = sorted(urdf_dir.glob(pattern))
                if found:
                    return str(found[0])

    raise FileNotFoundError(
        f"no URDF/xacro file found in {package_path / 'config'} "
        f"or sibling *_description/urdf/; pass --urdf-file"
    )


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def split_csv(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return result


def bool_from_string(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def configured_controllers(reference: dict[str, Any]) -> list[dict[str, Any]]:
    controllers = reference.get("controllers", [])
    if isinstance(controllers, dict):
        return [
            {"name": name, **data}
            for name, data in controllers.items()
            if isinstance(data, dict)
        ]
    if isinstance(controllers, list):
        return [controller for controller in controllers if isinstance(controller, dict)]
    return []


def reference_joint_limits(reference: dict[str, Any]) -> dict[str, dict[str, Any]]:
    options = reference.get("joint_limits", {})
    configured = options.get("joints", {})
    result: dict[str, dict[str, Any]] = {}

    if isinstance(configured, list):
        for joint in configured:
            if not isinstance(joint, dict) or not joint.get("name"):
                continue
            result[str(joint["name"])] = {
                key: value for key, value in joint.items() if key != "name"
            }
    elif isinstance(configured, dict):
        result.update(
            {
                str(name): values if isinstance(values, dict) else {}
                for name, values in configured.items()
            }
        )

    overrides = options.get("overrides", {})
    if isinstance(overrides, dict):
        for name, values in overrides.items():
            current = result.setdefault(str(name), {})
            if isinstance(values, dict):
                current.update(values)

    return result


def controller_joint_names(reference: dict[str, Any]) -> list[str]:
    joints: list[str] = []
    for controller in configured_controllers(reference):
        joints.extend(str(joint) for joint in controller.get("joints", []))
    return unique(joints)


def normalize_joint_limits(
    package_path: Path,
    reference: dict[str, Any],
    *,
    default_velocity: float,
    default_acceleration: float,
) -> None:
    options = reference.get("joint_limits", {})
    if options.get("normalize", True) is False:
        return

    limits_path = package_path / "config" / options.get("file", "joint_limits.yaml")
    limits = load_yaml(limits_path)
    joint_limits = limits.setdefault("joint_limits", {})
    if not isinstance(joint_limits, dict):
        raise ValueError(f"{limits_path} joint_limits must be a mapping")

    configured_limits = reference_joint_limits(reference)
    for name in unique(list(configured_limits) + controller_joint_names(reference)):
        joint = configured_limits.get(name, {})
        joint_data = joint_limits.setdefault(name, {})
        joint_data.setdefault("max_velocity", joint.get("max_velocity", default_velocity))
        joint_data.setdefault(
            "max_acceleration",
            joint.get("max_acceleration", default_acceleration),
        )

    for name, joint_data in joint_limits.items():
        if not isinstance(joint_data, dict):
            continue
        joint_data["has_velocity_limits"] = True
        joint_data["has_acceleration_limits"] = True
        if float(joint_data.get("max_velocity") or 0.0) == 0.0:
            joint_data["max_velocity"] = default_velocity
        if float(joint_data.get("max_acceleration") or 0.0) == 0.0:
            joint_data["max_acceleration"] = default_acceleration

    dump_yaml(limits_path, limits)


def generated_controller_configs(reference: dict[str, Any]) -> dict[str, Any]:
    controllers = configured_controllers(reference)
    if not controllers:
        return {}

    ros2_control = reference.get("ros2_control", {})
    if not isinstance(ros2_control, dict):
        ros2_control = {}
    default_command_interfaces = ros2_control.get("command_interfaces", [])
    default_state_interfaces = ros2_control.get("state_interfaces", [])

    moveit_simple: dict[str, Any] = {
        "controller_names": [controller["name"] for controller in controllers],
    }
    controller_manager_params: dict[str, Any] = {
        "update_rate": ros2_control.get("update_rate", 100),
    }
    ros2_config: dict[str, Any] = {
        "controller_manager": {"ros__parameters": controller_manager_params},
    }

    for index, controller in enumerate(controllers):
        name = controller["name"]
        joints = controller.get("joints", [])
        controller_manager_params[name] = {
            "type": controller.get(
                "ros2_type",
                "joint_trajectory_controller/JointTrajectoryController",
            )
        }
        moveit_simple[name] = {
            "type": controller.get("moveit_type", "FollowJointTrajectory"),
            "joints": joints,
            "action_ns": controller.get("action_ns", "follow_joint_trajectory"),
            "default": bool_from_string(controller.get("default", index == 0)),
        }
        ros2_config[name] = {
            "ros__parameters": {
                "joints": joints,
                "command_interfaces": controller.get(
                    "command_interfaces",
                    default_command_interfaces,
                ),
                "state_interfaces": controller.get(
                    "state_interfaces",
                    default_state_interfaces,
                ),
            }
        }

    if ros2_control.get("joint_state_broadcaster", True):
        controller_manager_params["joint_state_broadcaster"] = {
            "type": "joint_state_broadcaster/JointStateBroadcaster"
        }

    return {
        "moveit_controllers.yaml": {
            "moveit_controller_manager": (
                "moveit_simple_controller_manager/MoveItSimpleControllerManager"
            ),
            "moveit_simple_controller_manager": moveit_simple,
        },
        "ros2_controllers.yaml": ros2_config,
    }


def pilz_enabled(reference: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.disable_pilz:
        return False
    if args.enable_pilz:
        return True
    return bool(reference.get("pilz", {}).get("enabled", True))


def write_pilz_configs(package_path: Path, reference: dict[str, Any], enabled: bool) -> None:
    if not enabled:
        return

    pilz = reference.get("pilz", {})
    planning = PILZ_PLANNING | pilz.get("planning", {})
    dump_yaml(
        package_path / "config" / "pilz_industrial_motion_planner_planning.yaml",
        planning,
    )

    cartesian_limits = pilz.get("cartesian_limits", DEFAULT_CARTESIAN_LIMITS)
    dump_yaml(
        package_path / "config" / "pilz_cartesian_limits.yaml",
        {"cartesian_limits": cartesian_limits},
    )


def sync_reference_configs(package_path: Path, reference: dict[str, Any], names: list[str]) -> None:
    generated = reference.get("generated_configs", {}) | generated_controller_configs(reference)
    for filename in names:
        data = generated.get(filename)
        if data:
            dump_yaml(package_path / "config" / filename, data)


def copy_config_file(root: Path, package_path: Path, source_arg: str, destination: str) -> None:
    source = as_path(root, source_arg)
    if not source or not source.is_file():
        raise FileNotFoundError(f"config source not found: {source_arg}")
    target = package_path / "config" / destination
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def ensure_trajectory_execution_config(package_path: Path) -> None:
    path = package_path / "config" / "moveit_controllers.yaml"
    if not path.exists():
        return

    data = load_yaml(path)
    data.setdefault(
        "trajectory_execution",
        {
            "allowed_execution_duration_scaling": 1.2,
            "allowed_goal_duration_margin": 0.5,
            "allowed_start_tolerance": 0.15,
            "execution_duration_monitoring": True,
        },
    )
    dump_yaml(path, data)


def validate_moveit_config(package_path: Path) -> None:
    """校验生成 launch 文件前必需的配置文件是否存在。"""
    config_dir = package_path / "config"
    required_files = [
        config_dir / "kinematics.yaml",
        config_dir / "moveit_controllers.yaml",
    ]
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "MoveIt config is missing required files: "
            + ", ".join(missing)
        )


def parse_extra_node(value: str) -> ExtraNode:
    parts = value.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError("--extra-node must be PACKAGE:EXECUTABLE[:NAME]")
    package, executable = parts[0], parts[1]
    name = parts[2] if len(parts) == 3 and parts[2] else executable
    return ExtraNode(package=package, executable=executable, name=name)


def reference_extra_nodes(reference: dict[str, Any]) -> list[ExtraNode]:
    result: list[ExtraNode] = []
    demo_me = reference.get("demo_me", {})
    gripper_node = demo_me.get("gripper_controller_node")
    nodes = []
    if isinstance(gripper_node, dict):
        nodes.append({"variable_name": "gripper_controller_node", **gripper_node})
    nodes.extend(demo_me.get("extra_nodes", []))

    for node in nodes:
        package = node.get("package")
        executable = node.get("executable")
        if not package or not executable:
            continue
        result.append(
            ExtraNode(
                package=package,
                executable=executable,
                name=node.get("name"),
                variable_name=node.get("variable_name"),
                output=node.get("output", "screen"),
                respawn=bool_from_string(node.get("respawn", True)),
                parameters=node.get("parameters"),
            )
        )
    return result


def selected_extra_nodes(reference: dict[str, Any], args: argparse.Namespace) -> list[ExtraNode]:
    if args.no_reference_extra_nodes:
        nodes: list[ExtraNode] = []
    else:
        nodes = reference_extra_nodes(reference)
    nodes.extend(parse_extra_node(value) for value in args.extra_node or [])
    return nodes


def ensure_package_dependencies(package_path: Path, dependencies: list[str]) -> None:
    package_xml = package_path / "package.xml"
    if not package_xml.exists():
        return

    tree = ET.parse(package_xml)
    root = tree.getroot()
    existing = {element.text for element in root.findall("exec_depend")}
    for dep in unique(dependencies):
        if not dep or dep in existing:
            continue
        element = ET.Element("exec_depend")
        element.text = dep
        export = root.find("export")
        insert_at = list(root).index(export) if export is not None else len(root)
        root.insert(insert_at, element)
        existing.add(dep)

    ET.indent(tree, space="  ")
    tree.write(package_xml, encoding="unicode", xml_declaration=True)


def builder_block(
    robot: str,
    package: str,
    urdf: str,
    pipelines: list[str],
    indent: str = "            ",
) -> str:
    # 处理 URDF 路径：config/ 下文件名 or description 包绝对路径
    urdf_path = Path(urdf)
    if urdf_path.is_absolute():
        # 从绝对路径往上找 description 包的 package.xml
        desc_pkg = None
        rel_path = urdf
        current = urdf_path.parent
        while current != current.parent:
            if (current / "package.xml").exists():
                desc_pkg = package_name(current, explicit=None)
                rel_path = str(urdf_path.relative_to(current))
                break
            current = current.parent
        if desc_pkg:
            robot_desc = (
                f".robot_description(\n"
                f"            file_path=os.path.join(\n"
                f"                get_package_share_directory({desc_pkg!r}),\n"
                f"                {rel_path!r}\n"
                f"            )\n"
                f"        )"
            )
        else:
            robot_desc = (
                f".robot_description("
                f"file_path={f'config/{urdf_path.name}'!r})"
            )
    else:
        robot_desc = (
            f".robot_description("
            f"file_path={f'config/{urdf}'!r})"
        )

    block = f"""
    MoveItConfigsBuilder(robot_name={robot!r}, package_name={package!r})
    {robot_desc}
    .trajectory_execution(file_path="config/moveit_controllers.yaml")
    .planning_scene_monitor(
        publish_robot_description=True,
        publish_robot_description_semantic=True,
    )
    .planning_pipelines(
        pipelines={pipelines!r},
    )
    .to_moveit_configs()
    """
    return textwrap.indent(textwrap.dedent(block).strip(), indent)


def write_demo_launch(
    package_path: Path,
    package: str,
    robot: str,
    urdf: str,
    pipelines: list[str],
    filename: str,
) -> None:
    content = f"""
    import os
    from ament_index_python.packages import get_package_share_directory
    from moveit_configs_utils import MoveItConfigsBuilder
    from moveit_configs_utils.launches import generate_demo_launch


    def generate_launch_description():
        moveit_config = (
{builder_block(robot, package, urdf, pipelines)}
        )
        return generate_demo_launch(moveit_config)
    """
    write_text(package_path / "launch" / filename, content)


def extra_node_variable_name(node: ExtraNode, index: int) -> str:
    return node.variable_name or f"extra_node_{index}"


def extra_node_definition(node: ExtraNode, index: int) -> str:
    parameters = node.parameters if node.parameters is not None else [{"use_sim_time": True}]
    name = node.name or node.executable
    variable_name = extra_node_variable_name(node, index)
    return f"""
    {variable_name} = Node(
        package={node.package!r},
        executable={node.executable!r},
        name={name!r},
        parameters={parameters!r},
        output={node.output!r},
        respawn={node.respawn!r},
    )
    """


def write_runtime_launch(
    package_path: Path,
    package: str,
    robot: str,
    urdf: str,
    pipelines: list[str],
    extra_nodes: list[ExtraNode],
    filename: str,
) -> None:
    extra_defs = "\n".join(
        textwrap.dedent(extra_node_definition(node, index)).rstrip()
        for index, node in enumerate(extra_nodes)
    )
    return_items = [
        "rviz_node",
        "robot_state_publisher",
        "run_move_group_node",
        *[
            extra_node_variable_name(node, index)
            for index, node in enumerate(extra_nodes)
        ],
    ]
    if extra_defs:
        extra_defs = "\n" + textwrap.indent(extra_defs, "        ") + "\n"

    content = f"""
    import os
    import subprocess
    from pathlib import Path

    from ament_index_python.packages import get_package_share_directory
    from launch import LaunchDescription
    from launch.actions import DeclareLaunchArgument, OpaqueFunction
    from launch.conditions import IfCondition
    from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
    from launch_ros.actions import Node
    from moveit_configs_utils import MoveItConfigsBuilder


    def _check_existing_runtime() -> None:
        \"\"\"检查是否已有完整机器人 runtime 在运行。\"\"\"
        try:
            result = subprocess.run(
                ["ros2", "node", "list"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            nodes = {{line.strip() for line in result.stdout.splitlines() if line.strip()}}
            if "/move_group" in nodes:
                raise RuntimeError(
                    "检测到 /move_group 已存在，说明已有完整机器人 runtime 在运行。\\n"
                    "请使用 rviz_only.launch.py 连接现有 runtime，而不是启动新的 demo_me。\\n"
                    "如果确实需要重启，请先停止现有的 MoveIt 调试环境。\\n"
                    "如果刚重启过轨迹生成服务，请等待 10-20 秒后重试。"
                )
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            pass


    def generate_launch_description():
        declared_arguments = [
            DeclareLaunchArgument(
                "use_rviz",
                default_value="true",
                description="Whether to launch RViz",
            ),
            DeclareLaunchArgument(
                "check_existing_runtime",
                default_value="true",
                description="Whether to check for existing /move_group before launching",
            ),
        ]
        return LaunchDescription(declared_arguments + [OpaqueFunction(function=launch_setup)])


    def launch_setup(context, *args, **kwargs):
        check_runtime = LaunchConfiguration("check_existing_runtime")
        if check_runtime.perform(context) == "true":
            _check_existing_runtime()

        current_directory = str(Path(__file__).resolve().parent)
        use_sim_time = {{"use_sim_time": True}}
        use_rviz = LaunchConfiguration("use_rviz")

        moveit_config = (
{builder_block(robot, package, urdf, pipelines)}
        )

        run_move_group_node = Node(
            package="moveit_ros_move_group",
            executable="move_group",
            output="screen",
            parameters=[moveit_config.to_dict() | use_sim_time],
        )

        rviz_node = Node(
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            output="log",
            arguments=["-d", PathJoinSubstitution([current_directory, "moveit.rviz"])],
            parameters=[
                moveit_config.robot_description,
                moveit_config.robot_description_semantic,
                moveit_config.robot_description_kinematics,
                moveit_config.planning_pipelines,
                moveit_config.joint_limits,
                use_sim_time,
            ],
            condition=IfCondition(use_rviz),
        )

        robot_state_publisher = Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="both",
            parameters=[moveit_config.robot_description, use_sim_time],
        )
{extra_defs}
        return [
            {", ".join(return_items)}
        ]
    """
    write_text(package_path / "launch" / filename, content)


def write_rviz_only_launch(
    package_path: Path,
    package: str,
    robot: str,
    urdf: str,
    pipelines: list[str],
    filename: str,
) -> None:
    """生成只启动 RViz 的 launch 文件，用于连接已有 runtime。"""
    content = f"""
    import os
    from pathlib import Path

    from ament_index_python.packages import get_package_share_directory
    from launch import LaunchDescription
    from launch.substitutions import PathJoinSubstitution
    from launch_ros.actions import Node
    from moveit_configs_utils import MoveItConfigsBuilder


    def generate_launch_description():
        current_directory = str(Path(__file__).resolve().parent)
        use_sim_time = {{"use_sim_time": True}}

        moveit_config = (
{builder_block(robot, package, urdf, pipelines)}
        )

        rviz_node = Node(
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            output="log",
            arguments=["-d", PathJoinSubstitution([current_directory, "moveit.rviz"])],
            parameters=[
                moveit_config.robot_description,
                moveit_config.robot_description_semantic,
                moveit_config.robot_description_kinematics,
                moveit_config.planning_pipelines,
                moveit_config.joint_limits,
                use_sim_time,
            ],
        )

        return LaunchDescription([rviz_node])
    """
    write_text(package_path / "launch" / filename, content)


def selected_pipelines(reference: dict[str, Any], args: argparse.Namespace, enable_pilz: bool) -> list[str]:
    configured = split_csv(args.pipeline)
    if not configured:
        reference_pipelines = reference.get("planning_pipelines", ["ompl"])
        configured = reference_pipelines if isinstance(reference_pipelines, list) else [str(reference_pipelines)]
    if enable_pilz:
        configured.append(PILZ_PIPELINE)
    return unique(configured)


def dependencies(reference: dict[str, Any], extra_nodes: list[ExtraNode], enable_pilz: bool, args: argparse.Namespace) -> list[str]:
    deps = split_csv(args.exec_depend)
    deps.extend(reference.get("package", {}).get("exec_depends", []))
    deps.extend(node.package for node in extra_nodes)
    if enable_pilz:
        deps.append(PILZ_DEPEND)
    return unique(deps)


def _find_srdf_file(package_path: Path, robot: str) -> Path:
    """定位 SRDF 文件，优先匹配 {robot}.srdf。"""
    config_dir = package_path / "config"

    expected = config_dir / f"{robot}.srdf"
    if expected.exists():
        return expected

    srdf_files = sorted(config_dir.glob("*.srdf"))

    if len(srdf_files) == 1:
        return srdf_files[0]

    if not srdf_files:
        raise FileNotFoundError(f"No SRDF found under {config_dir}")

    raise RuntimeError(
        "Multiple SRDF files found and cannot determine which to use: "
        f"{srdf_files}. Expected '{robot}.srdf' or a single .srdf file."
    )


def _parse_srdf_chains(package_path: Path, robot: str) -> dict[str, str]:
    """从 SRDF 中提取每个 group 的 <chain> tip_link。

    返回 {group_name: tip_link}，
    仅包含含有 <chain> 元素的 group。
    base_link 不从 SRDF 读取（robot_profile 的 base_link 来自 prefab config 的 fixed_base）。
    """
    srdf_path = _find_srdf_file(package_path, robot)
    print(f"[custom robot] Using SRDF: {srdf_path}")

    tree = ET.parse(srdf_path)
    root = tree.getroot()

    chains: dict[str, str] = {}
    for group_elem in root.findall("group"):
        group_name = group_elem.get("name", "")
        chain_elem = group_elem.find("chain")
        if chain_elem is not None:
            chains[group_name] = chain_elem.get("tip_link", "")
    return chains


def _identify_left_right(groups: list[str]) -> tuple[str, str]:
    """通过名称识别左臂和右臂 group。

    要求恰好一个 group 名包含 'left'，一个包含 'right'，否则报错。
    """
    left_candidates = [g for g in groups if "left" in g.lower()]
    right_candidates = [g for g in groups if "right" in g.lower()]

    if len(left_candidates) != 1 or len(right_candidates) != 1:
        raise ValueError(
            "Cannot uniquely identify left/right arm groups: "
            f"{groups}. Expected group names containing "
            "'left' and 'right'."
        )

    return left_candidates[0], right_candidates[0]


def _detect_arm_type(
    o3de_config: dict[str, Any],
    chains: dict[str, str],
    kinematics: dict[str, Any],
) -> tuple[str, list[str]]:
    """检测 arm_type 和选中的 arm groups。

    优先从 o3de_config 读取；否则从 SRDF chain 数量自动检测。
    返回 (arm_type, arm_groups)。
    """
    all_groups = list(kinematics.keys())
    arm_groups = [g for g in all_groups if g in chains]

    if not arm_groups:
        raise ValueError(
            f"Cannot identify arm planning group: "
            f"kinematics groups={all_groups}, "
            f"SRDF chain groups={list(chains)}"
        )

    arm_type = o3de_config.get("arm_type")
    if not arm_type:
        if len(arm_groups) == 1:
            arm_type = "single"
            print(f"[custom robot] Auto-detected arm_type: single (1 arm group: {arm_groups[0]})")
        elif len(arm_groups) == 2:
            try:
                _identify_left_right(arm_groups)
                arm_type = "dual"
                print(f"[custom robot] Auto-detected arm_type: dual (groups: {arm_groups})")
            except ValueError:
                raise ValueError(
                    f"Found 2 arm groups but cannot identify left/right: {arm_groups}. "
                    f"Please specify arm_type in prefab config, or rename groups to contain 'left'/'right'."
                )
        else:
            raise ValueError(
                f"Cannot auto-detect arm_type: found {len(arm_groups)} arm groups: {arm_groups}. "
                f"Expected 1 (single) or 2 (dual). "
                f"Please specify arm_type in prefab config."
            )

    return arm_type, arm_groups


def _runtime_arm_prefix(arm_side: str | None = None) -> str:
    """TrajectoryGen 运行时命名约定，与 SRDF group 名解耦。

    这不是 MoveIt 的事实，而是 custom_robot_move_pose_server.launch.py
    和 move_pose_server 节点使用的命名约定。
    单臂 -> "arm"，双臂 -> "left_arm" / "right_arm"。
    """
    if arm_side == "left":
        return "left_arm"
    if arm_side == "right":
        return "right_arm"
    return "arm"


def _derive_service_name(prefix: str) -> str:
    """约定：<prefix>_move_pose_server"""
    return f"{prefix}_move_pose_server"


def _derive_execution_service_name(planning_service_name: str) -> str:
    """约定：arm_move_pose_server -> arm_move_pose_execution_server"""
    if planning_service_name.endswith("_server"):
        return planning_service_name[: -len("_server")] + "_execution_server"
    return planning_service_name + "_execution_server"


def _detect_gripper_command_topics(gripper_package_path: Path, arm_type: str) -> dict[str, str]:
    """从夹爪 C++ 源码扫描 Float64 subscription，得到 TrajectoryGen 使用的夹爪命令 topic。

    半硬编码规范：
    - 单臂：要求恰好 1 个 Float64 subscription
    - 双臂：要求 2 个 Float64 subscription，且能通过 left/right 区分

    返回：
    - 单臂：{"arm": "hand_gripper_controller"}
    - 双臂：{"left_arm": "left_gripper_controller", "right_arm": "right_gripper_controller"}
    """
    # 匹配 create_subscription<Float64>("topic_name", ...)
    pattern = re.compile(
        r'create_subscription\s*<\s*std_msgs::msg::Float64\s*>\s*\(\s*["\']([^"\']+)["\']',
        re.MULTILINE
    )

    topics = []
    for cpp_file in gripper_package_path.rglob("*.cpp"):
        content = cpp_file.read_text(encoding='utf-8', errors='ignore')
        topics.extend(pattern.findall(content))

    # 去重
    topics = unique(topics)

    if arm_type == "single":
        if len(topics) == 0:
            raise ValueError(
                f"单臂夹爪 C++ 未找到 Float64 subscription: {gripper_package_path}"
            )
        if len(topics) > 1:
            raise ValueError(
                f"单臂夹爪 C++ 找到多个 Float64 subscription，无法确定使用哪个: {topics}"
            )
        return {"arm": topics[0].lstrip("/")}

    elif arm_type == "dual":
        if len(topics) != 2:
            raise ValueError(
                f"双臂夹爪 C++ 需要恰好 2 个 Float64 subscription，但找到 {len(topics)} 个: {topics}"
            )

        # 尝试通过 left/right 区分
        left_topic = None
        right_topic = None

        for topic in topics:
            topic_lower = topic.lower()
            if 'left' in topic_lower:
                if left_topic is not None:
                    raise ValueError(
                        f"双臂夹爪 C++ 找到多个包含 'left' 的 topic: {topics}"
                    )
                left_topic = topic
            elif 'right' in topic_lower:
                if right_topic is not None:
                    raise ValueError(
                        f"双臂夹爪 C++ 找到多个包含 'right' 的 topic: {topics}"
                    )
                right_topic = topic

        if left_topic is None or right_topic is None:
            raise ValueError(
                f"双臂夹爪 C++ 的 2 个 topic 无法通过 left/right 区分: {topics}。"
                f"请确保 topic 名包含 'left' 和 'right'。"
            )

        return {
            "left_arm": left_topic.lstrip("/"),
            "right_arm": right_topic.lstrip("/")
        }

    else:
        raise ValueError(f"未知的 arm_type: {arm_type}")


def _strip_slash(topic_name: str) -> str:
    """去掉 topic 名的前导 /。"""
    if topic_name and topic_name.startswith("/"):
        return topic_name[1:]
    return topic_name or ""


def write_robot_profile(
    package_path: Path,
    package: str,
    robot: str,
    urdf: str,
    demo_launch: str,
    o3de_config_path: Path | None = None,
    output_path: Path | None = None,
    *,
    gripper_command_topics: dict[str, str] | None = None,
    arm_type: str | None = None,
    chains: dict[str, str] | None = None,
    arm_groups: list[str] | None = None,
) -> Path:
    """生成 robot_profile.yaml。

    参数：
        package_path:        MoveIt config 包目录
        package:             MoveIt config 包名
        robot:               MoveIt robot 名
        urdf:                config/ 下的机器人描述文件名（来自 patch_package）
        demo_launch:         运行时 launch 文件名
        o3de_config_path:    prefab_convert 配置 YAML，提供机械臂 controller topic，arm_type 可选
        output_path:         输出路径（默认输出到 MoveIt config package 的同级目录）
        gripper_command_topics: 从夹爪 C++ 扫描得到的夹爪命令 topic
                               单臂：{"arm": "hand_gripper_controller"}
                               双臂：{"left_arm": "left_gripper_controller", "right_arm": "right_gripper_controller"}
        arm_type:            预计算的 arm_type（可选，不传则自动检测）
        chains:              预计算的 SRDF chain {group_name: tip_link}（可选，不传则自动解析）
        arm_groups:          预计算的 arm group 列表（可选，不传则自动检测）

    注意：
        - base_link 从 prefab config 的 fixed_base 读取，表示 TrajectoryGen 进行 TF/目标位姿转换时使用的统一参考坐标系
        - tool_link 从对应 MoveIt planning group 的 SRDF chain.tip_link 读取
    """
    if o3de_config_path is None:
        raise ValueError(
            "--prefab-config is required to generate robot_profile.yaml. "
            "Please provide the prefab_convert config YAML (xxx_config.yaml)."
        )
    if not o3de_config_path.exists():
        raise FileNotFoundError(
            f"--prefab-config file not found: {o3de_config_path}"
        )

    # 读取 O3DE 配置（prefab config）
    o3de_config = load_yaml(o3de_config_path)

    # 如果未预计算，自动检测（兼容独立调用）
    if chains is None:
        chains = _parse_srdf_chains(package_path, robot)
    if arm_type is None or arm_groups is None:
        kinematics = load_yaml(package_path / "config" / "kinematics.yaml")
        detected_type, detected_groups = _detect_arm_type(o3de_config, chains, kinematics)
        if arm_type is None:
            arm_type = detected_type
        if arm_groups is None:
            arm_groups = detected_groups

    # ── 显式 override ──
    SINGLE_TASK = (
        "trajectory_gen_py.task.simple_pick_place_task.SimplePickPlaceTask"
    )
    DUAL_TASK = (
        "trajectory_gen_py.task.dual_arm_pick_place_task."
        "DualArmPickPlaceTask"
    )

    topic_controllers = o3de_config.get("topic_controller", {})

    if arm_type == "single":
        if len(arm_groups) == 1:
            group = arm_groups[0]
        else:
            raise ValueError(
                "arm_type=single is ambiguous because multiple "
                f"arm groups were found: {arm_groups}. "
                "Please ensure only one arm group in SRDF."
            )
        task_class = SINGLE_TASK
        selected_groups = [group]
    elif arm_type == "dual":
        # 从 SRDF 自动检测 left/right
        if len(arm_groups) == 2:
            left, right = _identify_left_right(arm_groups)
        else:
            raise ValueError(
                f"arm_type=dual requires exactly 2 arm groups, but found {len(arm_groups)}: {arm_groups}."
            )
        task_class = DUAL_TASK
        selected_groups = [left, right]
    else:
        raise ValueError(
            f"Invalid arm_type: {arm_type}. Expected 'single' or 'dual'."
        )

    # 6. 从 prefab config 读取 fixed_base 作为 TrajectoryGen 的统一参考坐标系
    # 注意：fixed_base 来自 prefab_convert 阶段，表示机器人在 O3DE/任务侧使用的根参考 frame
    # 它不等同于 MoveIt SRDF planning group 的 chain.base_link
    planning_frame = o3de_config.get("fixed_base")
    if not planning_frame:
        raise ValueError(
            "prefab config 缺少 fixed_base，"
            "无法确定 TrajectoryGen 使用的参考坐标系。"
        )

    # 7. 构建每条手臂的配置
    def _build_arm_config(
        group_name: str, arm_side: str | None = None
    ) -> dict[str, Any]:
        tip_link = chains.get(group_name)
        if not tip_link:
            raise ValueError(
                f"Planning group '{group_name}' has no SRDF <chain>"
            )

        prefix = _runtime_arm_prefix(arm_side)
        svc_name = _derive_service_name(prefix)

        # 自定义机器人必须在 prefab config 中显式提供机械臂 controller topic
        if prefix not in topic_controllers:
            raise ValueError(
                f"缺少 topic_controller.{prefix}.topic_name，"
                f"请在 prefab config 中配置。"
            )
        controller_config = topic_controllers[prefix]
        if not isinstance(controller_config, dict):
            raise ValueError(f"topic_controller.{prefix} must be a mapping")
        topic = controller_config.get("topic_name")
        if not topic:
            raise ValueError(f"缺少 topic_controller.{prefix}.topic_name")
        controller_topic = _strip_slash(topic)

        arm_config = {
            "planning_group": group_name,
            "planning_service_name": svc_name,
            "execution_service_name": _derive_execution_service_name(svc_name),
            "controller_topic_name": controller_topic,
            "base_link": planning_frame,  # 从 prefab config 的 fixed_base 读取
            "tool_link": tip_link,        # 从 SRDF chain.tip_link 读取
        }

        # 写入当前手臂对应的夹爪命令 topic（从 C++ 扫描结果中获取）
        # 格式：单臂 {"arm": "..."}，双臂 {"left_arm": "...", "right_arm": "..."}
        if gripper_command_topics:
            # prefix 是 "arm" / "left_arm" / "right_arm"
            gripper_topic = gripper_command_topics.get(prefix)
            if gripper_topic:
                arm_config["gripper_controller"] = gripper_topic

        return arm_config

    # 7. 组装 profile
    profile: dict[str, Any] = {
        "schema_version": 1,
        "arm_type": arm_type,
        "moveit_config_package": package,
        "moveit_robot_name": robot,
        "moveit_demo_launch": demo_launch,
        "task_class": task_class,
    }

    if arm_type == "single":
        profile["arm_config"] = _build_arm_config(selected_groups[0])
    else:
        profile["left_arm_config"] = _build_arm_config(
            selected_groups[0], "left"
        )
        profile["right_arm_config"] = _build_arm_config(
            selected_groups[1], "right"
        )

    # 8. 写入文件
    if output_path is None:
        output_path = package_path.parent / "robot_profile.yaml"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "# ============================================================\n"
        "# robot_profile.yaml\n"
        "# 由 patch_moveit_config.py 自动生成，供轨迹生成运行时读取。\n"
        "#\n"
        "# 主要字段来源：\n"
        "#   moveit_config_package  <- MoveIt config package / package.xml\n"
        "#   moveit_robot_name      <- SRDF <robot name=\"...\">\n"
        "#   planning_group         <- SRDF + kinematics.yaml\n"
        "#   base_link              <- prefab config fixed_base\n"
        "#   tool_link              <- SRDF planning group chain.tip_link\n"
        "#   controller_topic_name  <- prefab config topic_controller\n"
        "#   gripper_controller     <- gripper C++ Float64 subscription topic\n"
        "#   planning/execution service names <- 轨迹生成 runtime naming convention\n"
        "#\n"
        "# 如果配置不正确，请优先修改机器人/MoveIt 的原始配置，\n"
        "# 然后重新运行 patch_moveit_config.py 生成本文件。\n"
        "#\n"
        "# 临时测试时可以直接修改本文件验证参数，\n"
        "# 但重新生成 robot_profile.yaml 时，手工修改会被覆盖。\n"
        "# ============================================================\n"
    )
    with output_path.open("w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(profile, f, allow_unicode=True, sort_keys=False)

    print(f"[custom robot] robot_profile.yaml written to: {output_path}")
    print("[custom robot] Generated robot profile:")
    print(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False))
    return output_path


def patch_package(root: Path, reference: dict[str, Any], args: argparse.Namespace) -> Path:
    if args.profile_only and args.skip_robot_profile:
        raise ValueError(
            "--profile-only cannot be used together with --skip-robot-profile"
        )
    package_path = resolve_package_path(root, args.package_path, reference)
    package = package_name(package_path, args.package_name)
    robot = robot_name(package_path, reference, args.robot_name)
    urdf = urdf_file(package_path, reference, args.urdf_file)

    # ===== robot_profile 所需分析 =====
    o3de_config_path = as_path(root, args.prefab_config)
    gripper_command_topics = None
    o3de_config = None
    arm_type = None
    chains = None
    arm_groups = None

    # 自动检测夹爪包
    gripper_path = None
    if args.gripper_controller_path:
        gripper_path = Path(args.gripper_controller_path)
    else:
        candidates = []
        for pattern in ["*_gripper_controller", "*_gripper_control"]:
            candidates.extend(package_path.parent.glob(pattern))

        if len(candidates) == 1:
            gripper_path = candidates[0]
            print(f"[patch] Auto-detected gripper controller: {gripper_path.name}")
        elif len(candidates) > 1:
            names = ", ".join(c.name for c in candidates)
            raise ValueError(
                f"检测到多个夹爪控制包（{names}），"
                f"请通过 --gripper-controller-path 指定其中一个。"
            )

    # 分析 + 夹爪 topic 扫描只在生成 robot_profile 时执行
    if not args.skip_robot_profile:
        if o3de_config_path is None:
            raise ValueError(
                "--prefab-config is required when generating robot_profile.yaml"
            )

        o3de_config = load_yaml(o3de_config_path)
        chains = _parse_srdf_chains(package_path, robot)
        kinematics = load_yaml(package_path / "config" / "kinematics.yaml")
        arm_type, arm_groups = _detect_arm_type(o3de_config, chains, kinematics)
        print(f"[patch] Detected arm_type={arm_type}, arm_groups={arm_groups}")

        if gripper_path:
            if not gripper_path.exists():
                raise FileNotFoundError(f"夹爪 controller 路径不存在: {gripper_path}")

            print(f"[patch] Scanning gripper C++ for arm_type={arm_type}...")
            gripper_command_topics = _detect_gripper_command_topics(gripper_path, arm_type)
            print(f"[patch] Detected gripper topics: {gripper_command_topics}")
    # ===== 分析结束 =====

    print("[patch][1/6] 分析机器人配置...")

    # --profile-only：只重新分析输入并生成 robot_profile，不修改 MoveIt 配置
    if args.profile_only:
        print("[patch] --profile-only mode: skipping full patch, only generating robot_profile...")
        if not args.skip_robot_profile:
            print("[patch] Generating robot_profile.yaml...")
            profile_output = as_path(root, args.robot_profile_output)

            write_robot_profile(
                package_path=package_path,
                package=package,
                robot=robot,
                urdf=urdf,
                demo_launch=args.runtime_launch_name,
                o3de_config_path=o3de_config_path,
                output_path=profile_output,
                gripper_command_topics=gripper_command_topics,
                arm_type=arm_type,
                chains=chains,
                arm_groups=arm_groups,
            )
        return package_path

    enable_pilz = pilz_enabled(reference, args)
    pipelines = selected_pipelines(reference, args, enable_pilz)
    extra_nodes = selected_extra_nodes(reference, args)

    # 只有 full patch 才需要向 demo_me.launch.py 添加 gripper node
    if gripper_path:
        gripper_pkg = package_name(gripper_path, explicit=None)
        if not any(n.package == gripper_pkg for n in extra_nodes):
            extra_nodes.append(
                ExtraNode(
                    package=gripper_pkg,
                    executable=gripper_pkg,
                    name=gripper_pkg,
                    variable_name="gripper_controller_node",
                )
            )
            print(f"[patch] Added gripper controller node: {gripper_pkg}")

    print(f"[patch] package={package}, robot={robot}, urdf={urdf}")
    print("[patch][2/6] 生成 Pilz 配置...")
    write_pilz_configs(package_path, reference, enable_pilz)
    print("[patch][3/6] 规整关节限位...")
    if not args.skip_joint_limits:
        joint_limit_options = reference.get("joint_limits", {})
        normalize_joint_limits(
            package_path,
            reference,
            default_velocity=joint_limit_options.get(
                "fallback_max_velocity",
                args.default_max_velocity,
            ),
            default_acceleration=joint_limit_options.get(
                "fallback_max_acceleration",
                args.default_max_acceleration,
            ),
        )

    sync_names = split_csv(args.sync_generated_config)
    if args.sync_default_controllers or configured_controllers(reference):
        sync_names.extend(["moveit_controllers.yaml", "ros2_controllers.yaml"])
    sync_reference_configs(package_path, reference, unique(sync_names))

    if args.moveit_controllers:
        copy_config_file(root, package_path, args.moveit_controllers, "moveit_controllers.yaml")
    if args.ros2_controllers:
        copy_config_file(root, package_path, args.ros2_controllers, "ros2_controllers.yaml")
    ensure_trajectory_execution_config(package_path)
    validate_moveit_config(package_path)

    print("[patch][4/6] 生成 launch 文件...")
    if not args.skip_demo_launch:
        write_demo_launch(package_path, package, robot, urdf, pipelines, args.demo_launch_name)
    if not args.skip_runtime_launch:
        write_runtime_launch(package_path, package, robot, urdf, pipelines, extra_nodes, args.runtime_launch_name)
    if hasattr(args, 'rviz_only_launch_name') and args.rviz_only_launch_name:
        write_rviz_only_launch(package_path, package, robot, urdf, pipelines, args.rviz_only_launch_name)

    print("[patch][5/6] 更新 package.xml...")
    if not args.skip_package_xml:
        ensure_package_dependencies(package_path, dependencies(reference, extra_nodes, enable_pilz, args))

    if not args.skip_robot_profile:
        print("[patch][6/6] 生成 robot_profile.yaml...")
        profile_output = as_path(root, args.robot_profile_output)

        write_robot_profile(
            package_path=package_path,
            package=package,
            robot=robot,
            urdf=urdf,
            demo_launch=args.runtime_launch_name,
            o3de_config_path=o3de_config_path,
            output_path=profile_output,
            gripper_command_topics=gripper_command_topics,
            arm_type=arm_type,
            chains=chains,
            arm_groups=arm_groups,
        )

    return package_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-path", help="MoveIt config package to patch")
    parser.add_argument("--reference-config", help="YAML recipe used as defaults and config source")
    parser.add_argument("--package-name", help="Override package name used by MoveItConfigsBuilder")
    parser.add_argument("--robot-name", help="Override robot name used by MoveItConfigsBuilder")
    parser.add_argument("--urdf-file", help="URDF/Xacro filename under the package config directory")

    parser.add_argument("--pipeline", action="append", help="Planning pipeline, repeatable or comma-separated")
    parser.add_argument("--enable-pilz", action="store_true", help="Write Pilz configs and include the Pilz pipeline")
    parser.add_argument("--disable-pilz", action="store_true", help="Do not write Pilz configs or add the Pilz pipeline")

    parser.add_argument("--skip-joint-limits", action="store_true", help="Do not normalize joint_limits.yaml")
    parser.add_argument("--default-max-velocity", type=float, default=5.0)
    parser.add_argument("--default-max-acceleration", type=float, default=5.0)

    parser.add_argument(
        "--sync-default-controllers",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Sync or generate moveit_controllers.yaml and ros2_controllers.yaml from the reference config",
    )
    parser.add_argument(
        "--sync-generated-config",
        action="append",
        help="Generated config filename to sync from reference YAML, repeatable or comma-separated",
    )
    parser.add_argument("--moveit-controllers", help="Source file to copy to config/moveit_controllers.yaml")
    parser.add_argument("--ros2-controllers", help="Source file to copy to config/ros2_controllers.yaml")

    parser.add_argument("--extra-node", action="append", help="Runtime node as PACKAGE:EXECUTABLE[:NAME]")
    parser.add_argument(
        "--no-reference-extra-nodes",
        action="store_true",
        help="Ignore demo_me runtime nodes from the reference YAML",
    )
    parser.add_argument("--exec-depend", action="append", help="Extra package.xml exec_depend, repeatable or comma-separated")

    parser.add_argument("--demo-launch-name", default="demo.launch.py")
    parser.add_argument("--runtime-launch-name", default="demo_me.launch.py")
    parser.add_argument("--rviz-only-launch-name", default="rviz_only.launch.py")
    parser.add_argument("--skip-demo-launch", action="store_true")
    parser.add_argument("--skip-runtime-launch", action="store_true")
    parser.add_argument("--skip-package-xml", action="store_true")

    # 自定义机器人支持
    parser.add_argument(
        "--prefab-config",
        help="prefab_convert 配置 YAML（xxx_config.yaml），与 file_convert.py --yaml 相同。"
             "提供精确的 controller/topic 名用于 robot_profile.yaml。",
    )
    parser.add_argument(
        "--robot-profile-output",
        help="robot_profile.yaml 输出路径（默认输出到 package-path 同级目录）",
    )
    parser.add_argument("--skip-robot-profile", action="store_true", help="不生成 robot_profile.yaml")
    parser.add_argument(
        "--gripper-controller-path",
        help="夹爪 controller package 的路径，用于扫描 C++ 源码得到夹爪命令 topic。"
             "单臂要求恰好 1 个 Float64 subscription，双臂要求 2 个且能通过 left/right 区分。"
             "未指定时自动在 package_path 同级目录查找 *_gripper_controller 或 *_gripper_control。",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help=(
            "高级调试选项：仅重新分析机器人配置并生成 robot_profile.yaml，"
            "不修改 MoveIt 配置和 launch 文件。"
            "普通接入流程无需使用此选项。"
        ),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    root = workspace_root()
    try:
        reference = load_reference_config(root, args.reference_config)
        package_path = patch_package(root, reference, args)
    except Exception as exc:
        print(f"\n[patch_moveit_config][ERROR] {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    print(f"patched MoveIt config package: {package_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
