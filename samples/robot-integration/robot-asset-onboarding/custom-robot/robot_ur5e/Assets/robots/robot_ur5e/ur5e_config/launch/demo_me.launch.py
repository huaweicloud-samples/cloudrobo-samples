import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from moveit_configs_utils import MoveItConfigsBuilder


def generate_launch_description():
    declared_arguments = [
        DeclareLaunchArgument(
            "use_rviz",
            default_value="true",
            description="Whether to launch RViz",
        )
    ]
    return LaunchDescription(declared_arguments + [OpaqueFunction(function=launch_setup)])


def launch_setup(context, *args, **kwargs):
    current_directory = str(Path(__file__).resolve().parent)
    use_sim_time = {"use_sim_time": True}
    use_rviz = LaunchConfiguration("use_rviz")

    moveit_config = (
        MoveItConfigsBuilder(robot_name='umi', package_name='ur5e_config')
        .robot_description(
                file_path=os.path.join(
                    get_package_share_directory('ur5e_description'),
                    'urdf/ur5e.urdf'
                )
            )
        .trajectory_execution(file_path="config/moveit_controllers.yaml")
        .planning_scene_monitor(
            publish_robot_description=True,
            publish_robot_description_semantic=True,
        )
        .planning_pipelines(
            pipelines=['ompl', 'pilz_industrial_motion_planner'],
        )
        .to_moveit_configs()
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


    gripper_controller_node = Node(
        package='ur5e_gripper_controller',
        executable='ur5e_gripper_controller',
        name='ur5e_gripper_controller',
        parameters=[{'use_sim_time': True}],
        output='screen',
        respawn=True,
    )

    return [
        rviz_node, robot_state_publisher, run_move_group_node, gripper_controller_node
    ]
