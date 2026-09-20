# custom-robot

本项目基于开源ur5e机器臂提供样例工程，支持通过 `scripts/init_project.py` 一键初始化任意目标机器人的工程，完成该工程的调试后，您可在平台使用自定义本体进行全链路的仿真任务。
`scripts`文件夹下包含调试需要使用的工具脚本，可参考指导文档执行。
下文重点介绍 `Assets/robots/robot_ur5e/` 目录下各子模块与文件的用途。

## 目录总览

```
Assets/robots/robot_ur5e/
├── metadata.json               # 机器人元数据（类型、回灌 ROS topic 列表）
├── robot_profile.yaml          # 机器人档案（机械臂类型、MoveIt 配置、任务类）
├── ur5e.yaml                   # 关节初始位姿与控制器映射
├── r2c_ur5e.yaml               # R2C 运行时配置（采样频率、订阅/发布、转换器映射）
├── robot_ur5e.prefab           # ME生成的机器人prefab（视觉/物理装配）
├── ur5e_config/                # MoveIt + ros2_control 配置包
├── ur5e_description/           # URDF + mesh 描述包
├── ur5e_gripper_controller/    # 自定义 ros2_control 夹爪控制器源码
└── ur5e_transformer/           # R2C 转换器（夹爪手指↔行程）
```

---

## 顶层文件

### `metadata.json`
需要手动填写，机器人元信息。`robot_type` 标识机器人类型；`record_topic` 列出在数据回灌/同步录制时需要订阅的关键 ROS topic（包括关节状态、控制器命令、相机图像等）。

### `robot_profile.yaml`
无需手动填写，运行 `patch_moveit_config.py` 自动生成的机器人档案，描述机械臂构型（`single`）、MoveIt 配置包名、MoveIt 演示启动文件、任务类入口，以及臂/夹爪的 planning group、service 名称、控制器 topic、base/tool link 等关键映射参数。**请勿手工修改**，配置变更后重新运行 patch 脚本。

### `ur5e.yaml`
需要手动填写，机器人控制器映射与初始位姿：
- `fixed_base` / `moveit_controller`：底盘 link 与 MoveIt 主控制器名。
- `topic_controller`：定义 arm 与 hand 各关节对应的 ROS topic 与关节名。
- `init_joints`：6 个臂关节 + 2 个夹爪手指关节的初始角度（弧度）。

### `r2c_ur5e.yaml`
需要手动填写，R2C（Record-to-Control）运行时配置，是机器人与上层策略通信的核心：
- `runtime`：发布频率、最大运行时、`dry_run` 模式、动作响应超时、动作 chunk 对齐、键盘控制等。
- `hardware`：硬件适配器类型 (`ros2`)、节点名、初始关节下发、订阅（`joint_states` / 头部相机 / 外置相机）、发布（arm / hand 命令）、action 目标。
- `translator`：转换器类型 (`configurable`)。
- `device_to_r2c` / `r2c_to_device`：设备数据与 R2C 格式之间的字段映射，包含关节切片、夹爪行程转换、ROS 图像转 JPEG 等。

### `robot_ur5e.prefab`
无需手动填写，ME自动生成的机器人prefab文件，整合机器人视觉外观、物理碰撞体、关节绑定与脚本引用。

---

## `ur5e_config/` —— MoveIt + ros2_control 配置包
无需手动填写，MoveIt Assistant生成，标准的 ROS2 MoveIt 配置包，提供运动规划、控制器管理与启动脚本。

```
ur5e_config/
├── CMakeLists.txt
├── package.xml
├── .setup_assistant            # MoveIt Setup Assistant 生成的元信息
├── config/                     # 参数文件
└── launch/                     # 启动文件
```

### 根目录
- `CMakeLists.txt` / `package.xml`：colcon 构建与 ROS2 包描述。
- `.setup_assistant`：MoveIt Setup Assistant 写入的辅助文件，记录生成时间等元信息。

### `config/`
- `initial_positions.yaml`：MoveIt 中各关节的初始位姿。
- `joint_limits.yaml`：关节速度、加速度、位置上下限等运动学极限。
- `kinematics.yaml`：运动学求解器配置（IK 解算参数）。
- `moveit.rviz`：MoveIt RViz 显示预设。
- `moveit_controllers.yaml`：MoveIt 与 ros2_control 控制器之间的映射。
- `pilz_cartesian_limits.yaml`：Pilz 工业运动规划器的笛卡尔限速/限加速度。
- `ros2_controllers.yaml`：ros2_control 各硬件接口（位置/速度/力矩）的具体配置。
- `sensors_3d.yaml`：3D 传感器（点云）配置。
- `umi.ros2_control.xacro`：ros2_control 硬件接口的 xacro 宏定义。
- `umi.srdf`：MoveIt 使用的 Semantic Robot Description Format（规划组、虚拟关节、自碰撞矩阵）。
- `umi.urdf.xacro`：URDF 的 xacro 顶层入口，被 SRDF 引用以生成最终描述。

### `launch/`
- `demo.launch.py`：基础 MoveIt + RViz 演示启动。
- `demo_me.launch.py`：本工程定制的演示启动（含自定控制器等）。
- `moveit_rviz.launch.py`：仅启动 RViz + MoveIt 场景。
- `move_group.launch.py`：启动 move_group 节点。
- `rsp.launch.py`：启动 robot_state_publisher，发布 TF。
- `setup_assistant.launch.py`：调用 MoveIt Setup Assistant。
- `spawn_controllers.launch.py`：加载并激活 ros2_control 控制器。
- `static_virtual_joint_tfs.launch.py`：发布 static_transform_publisher（连接世界与机器人基座）。
- `warehouse_db.launch.py`：启动 MoveIt Warehouse 数据库服务（用于持久化场景/状态）。

---

## `ur5e_description/` —— 机器人描述包

URDF、网格和启动脚本所在的 ROS2 包，用于在 RViz / Unity 中可视化机器人并提供碰撞/惯性几何。

```
ur5e_description/
├── CMakeLists.txt
├── package.xml
├── config/
│   └── ur5e_joint_limits.yaml   # URDF 层面关节极限
├── launch/
│   └── display.launch.py        # 仅可视化启动脚本
├── rviz/
│   └── urdf.rviz                # URDF 可视化 RViz 预设
├── urdf/
│   ├── ur5e.urdf                # 主 URDF
│   └── ur5e copy.urdf           # URDF 副本（备份/调试）
└── meshes/                      # 网格资源（详见下）
```

### 根目录
- `CMakeLists.txt` / `package.xml`：colcon 构建与 ROS2 包描述（导出 meshes 等资源）。
- `config/ur5e_joint_limits.yaml`：URDF 中关节的位置/速度/力矩极限。
- `launch/display.launch.py`：仅启动 robot_state_publisher + joint_state_publisher_gui + RViz，便于纯可视化。
- `rviz/urdf.rviz`：URDF 可视化的 RViz 配置。
- `urdf/ur5e.urdf`：机器人本体 URDF（运动学/动力学/视觉/碰撞定义）。
- `urdf/ur5e copy.urdf`：URDF 的本地副本，用于备份或离线对比。

### `meshes/` —— 网格资源
- 顶层夹爪与桌面网格：`left_finger.{dae,stl}`、`right_finger.{dae,stl}`、`table.dae`。
- `gripper_realsense/`：夹爪 + RealSense 相机装配体的视觉/碰撞网格 `hand.{dae,stl}`。
- `realsense2/`：各类 RealSense 相机（D405/D415/D435/D455/L515）的 STL/DAE 模型，以及 USB plug 网格。
- `texture/`：纹理贴图（如 `satin_finished_rosewood.jpg`），用于 PBR 渲染。
- `ur5e/visual/`：UR5e 本体各连杆（base、shoulder、upperarm、forearm、wrist1/2/3）的 DAE 高模视觉网格。
- `ur5e/collision/`：UR5e 各连杆对应的 STL 简化碰撞网格。

---

## `ur5e_gripper_controller/` —— 夹爪 ros2_control 控制器

自定义 ros2_control 硬件接口实现，用于把 MoveIt 的位置命令转换为夹爪控制信号。

```
ur5e_gripper_controller/
├── CMakeLists.txt               # ament_cmake 构建
├── package.xml                  # ROS2 包描述（依赖 rclcpp / hardware_interface 等）
└── src/
    └── ur5e_gripper_controller.cpp   # 控制器实现
```

- `CMakeLists.txt` / `package.xml`：声明 ros2_control 控制器插件导出。
- `src/ur5e_gripper_controller.cpp`：实现 `hardware_interface::SystemInterface` 或 `ControllerInterface`，负责在 `read()` / `write()` 周期与底层夹爪硬件通信。

---

## `ur5e_transformer/` —— 第三方 R2C 转换器

通过 Python entry_point 机制向 R2C SDK 注册自定义值转换器，演示如何在不修改 SDK 的前提下扩展 `transform` 字段。

```
ur5e_transformer/
├── pyproject.toml                              # 构建与 entry_point 注册
└── my_parallel_gripper_transformers.py        # 平行夹爪两手指 ↔ 行程转换
```

- `pyproject.toml`：声明包元信息（依赖 `hw-r2c-sdk`），并通过 `[project.entry-points."r2c_sdk.transformers"]` 注册：
  - `my_gripper_fingers_to_stroke`
  - `my_gripper_stroke_to_fingers`
- `my_parallel_gripper_transformers.py`：提供两个 `IValueTransformer` 子类——
  - `MyGripperFingersToStrokeTransformer`：`[left, right]` → 行程 `(left+right)*1250`。
  - `MyGripperStrokeToFingersTransformer`：行程 → `[S/2500, S/2500]`（往返对称）。

这些转换器可在 `r2c_ur5e.yaml` 的 `device_to_r2c` / `r2c_to_device` 映射里直接通过名字引用。

---

## 项目初始化

通过 `scripts/init_project.py` 新建名字为 `robot_<新名字>/`的新工程：

```bash
python scripts/init_project.py <新机器人名>
```
