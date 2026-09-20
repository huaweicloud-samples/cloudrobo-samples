import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import Command, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node

def generate_launch_description():
    robot_name_in_model = 'ur5e'

    # Set the path. 
    default_urdf_model_path = os.path.join(get_package_share_directory('ur5e_description'), 'urdf/ur5e.urdf')
    default_rviz_config_path = PathJoinSubstitution([get_package_share_directory('ur5e_description'), 'rviz', 'urdf.rviz'])

    with open(default_urdf_model_path, 'r') as infp:
        robot_desc = infp.read()

    # Launch configuration variables specific to simulation
    use_rviz = LaunchConfiguration('use_rviz')
    gui = LaunchConfiguration('gui')
    
    # Declare launch arguments        
    declare_urdf_model_path_cmd = DeclareLaunchArgument(
        name='urdf_model', 
        default_value=default_urdf_model_path, 
        description='Absolute path to robot urdf file',
    )  

    declare_rviz_cmd = DeclareLaunchArgument(
        name='use_rviz',
        default_value= default_rviz_config_path,
        description = 'Path to the RViz config file to use',
    )

    declare_gui_cmd = DeclareLaunchArgument(
        name='gui',
        default_value='true',
        choices = ['true' , 'false'],
        description='Flag to enable joint_state_publisher_gui',
    )
    
    # Subscribe to the joint states of the robot, and publish the 3D pose of each link.
    start_robot_state_publisher_cmd = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{'robot_description': robot_desc}],
    )
    
    start_joint_state_publisher_cmd = Node(
        package='joint_state_publisher',
        executable='joint_state_publisher',
        name='joint_state_publisher',
        condition=IfCondition(gui),
    )

    start_joint_state_publisher_gui_cmd = Node(
        package='joint_state_publisher_gui',
        executable='joint_state_publisher_gui',
        name='joint_state_publisher_gui',
        condition=IfCondition(gui),
    )    

    rviz_cmd = Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            arguments=['-d', LaunchConfiguration('use_rviz')],
            output='screen',
    )
    
    ld = LaunchDescription()

    ld.add_action(declare_urdf_model_path_cmd)
    ld.add_action(declare_rviz_cmd)
    ld.add_action(declare_gui_cmd)

    ld.add_action(start_robot_state_publisher_cmd)
    ld.add_action(start_joint_state_publisher_gui_cmd)  # 可注释

    ld.add_action(rviz_cmd)

    return ld