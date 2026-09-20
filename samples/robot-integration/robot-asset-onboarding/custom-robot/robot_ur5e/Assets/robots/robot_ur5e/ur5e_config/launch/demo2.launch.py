from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch.conditions import IfCondition, UnlessCondition
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.actions import ExecuteProcess
from ament_index_python.packages import get_package_share_directory
from moveit_configs_utils import MoveItConfigsBuilder

from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode

def generate_launch_description():
    declared_arguments = [
        DeclareLaunchArgument(
            "use_rviz",
            default_value="false",
            description="Whether to launch RViz"
        )
    ]
    
    return LaunchDescription(
        declared_arguments + [OpaqueFunction(function=launch_setup)]
    )

def launch_setup(context, *args, **kwargs):

    current_file_path = Path(__file__).resolve()
    current_directory = str(current_file_path.parent)

    use_sim_time = {"use_sim_time": True}
    use_rviz = LaunchConfiguration("use_rviz")

    moveit_config = (
        MoveItConfigsBuilder(robot_name="umi", package_name="ur5e_config")
        .robot_description(file_path="config/umi.urdf.xacro")
        .planning_scene_monitor(
            publish_robot_description=True, publish_robot_description_semantic=True
        )
        .planning_pipelines(
            pipelines=["ompl", "pilz_industrial_motion_planner"],
        )
        .to_moveit_configs()
    )    

    config_dict = moveit_config.to_dict()
    run_move_group_node = Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=[ config_dict | use_sim_time],
    )

    rviz_config = PathJoinSubstitution(
        [current_directory, "moveit.rviz"]
    )

    gripper_control_node = Node(
        package='ur5e_gripper_controller',
        executable='ur5e_gripper_controller',
        name='ur5e_gripper_controller',
        parameters=[{'use_sim_time': True}],
        output="screen",
        respawn=True,
    )    

    # RViz
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="log",
        arguments=["-d", rviz_config],
        parameters=[
            moveit_config.robot_description,
            moveit_config.robot_description_semantic,
            moveit_config.robot_description_kinematics,
            moveit_config.planning_pipelines,
            moveit_config.joint_limits,
            use_sim_time
        ],
        condition=IfCondition(use_rviz)
    )
    
    # Publish TF
    robot_state_publisher = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="robot_state_publisher",
        output="both",
        parameters=[moveit_config.robot_description, use_sim_time],
    )


    nodes_to_start = [
        rviz_node,
        robot_state_publisher,
        gripper_control_node,
        run_move_group_node
    ]

    return nodes_to_start
