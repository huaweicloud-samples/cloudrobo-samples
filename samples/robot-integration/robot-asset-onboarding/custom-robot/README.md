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
由脚本生成但是仍需手动补全相机topic等，`init_project.py` 生成初版、`generate_metadata.py` 自动补全 `robot_type` 与 `record_topic`；`camera_introduce` 等相机 topic 需手工补充。`robot_type` 标识机器人类型；`record_topic` 列出在数据回灌/同步录制时需要订阅的关键 ROS topic（包括关节状态、控制器命令、相机图像等）。

### `robot_profile.yaml`
无需手动填写，运行 `patch_moveit_config.py` 自动生成的机器人档案，描述机械臂构型（`single`）、MoveIt 配置包名、MoveIt 演示启动文件、任务类入口，以及臂/夹爪的 planning group、service 名称、控制器 topic、base/tool link 等关键映射参数。**请勿手工修改**，配置变更后重新运行 patch 脚本。机械臂 planning group 的识别规则见下方[《机械臂 planning group 约定》](#机械臂-planning-group-约定)。

### `ur5e.yaml`
由脚本生成但是仍需手动补全fixed_base以及可能没有正确解析出来的字段，`generate_robot_yaml.py` 自动生成，其中 `fixed_base` 留空，需手工补填后再继续。机器人控制器映射与初始位姿：
- `fixed_base` / `moveit_controller`：底盘 link 与 MoveIt 主控制器名。
- `topic_controller`：定义各角色对应的控制器映射。
  - `arm`（单臂）/ `left_arm` + `right_arm`（双臂）：机械臂的低层命令 topic 与关节名。
  - `hand`（单臂）/ `left_gripper` + `right_gripper`（双臂）：夹爪配置，字段包括：
    - `topic_name`：**低层**关节命令 topic（`Float64MultiArray`，生成的控制器发布到此）。
    - `joint_names`：关节名，同时决定 `Float64MultiArray` 的输出顺序。
    - `gripper_command_topic`：**高层**夹爪命令 topic（`Float64`，`0.1=开 / 0.0=关`，生成的控制器订阅此 topic）。
    - `gripper_positions.open` / `.close`：每个关节在开/关时的目标值，用于生成线性映射。
  - `generate_gripper_controller.py` 会读取上述夹爪字段，生成 `ur5e_gripper_controller/src/*.cpp`。
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

## 机械臂 planning group 约定

`patch_moveit_config.py` 生成 `robot_profile.yaml` 时，需要确定哪些 MoveIt planning group 是机械臂。

候选 = **同时**满足以下两个条件的 group：
1. 在 `kinematics.yaml` 中配置了 IK solver；
2. 在 SRDF 中定义为 `<chain>`。

脚本只按候选**数量**自动识别，**不依据 group 名猜测**。约定如下：

- **单臂**：SRDF 中为机械臂定义一个 `<chain>` group，并在 `kinematics.yaml` 中**只**给它配置 IK。躯干等非臂 chain 若无笛卡尔需求，**不要**写入 `kinematics.yaml`。满足即可自动识别为 `single`。
- **双臂**：左右臂各定义一个 `<chain>` group，各配一个 IK；group 名需包含 `left` / `right`（如 `left_arm` / `right_arm`）。满足即可自动识别为 `dual`。

若配置不满足上述约定（例如躯干也被配了 IK，导致候选 ≥2 且无法区分左右），脚本会报错并列出候选 group。此时用下列参数**显式指定**即可（显式参数优先级最高）：

```bash
# 单臂：显式指定机械臂 group
python scripts/patch_moveit_config.py \
  --package-path <moveit_pkg> \
  --prefab-config <xxx_config.yaml> \
  --arm-group <arm_group_name>

# 双臂：显式指定左右臂 group
python scripts/patch_moveit_config.py \
  --package-path <moveit_pkg> \
  --prefab-config <xxx_config.yaml> \
  --left-arm-group <left_group> \
  --right-arm-group <right_group>
```

> `--arm-group` 与 `--left-arm-group`/`--right-arm-group` 互斥；双臂必须同时提供 left/right 两个。

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
- `joint_limits.yaml`：关节速度、加速度、位置上下限等运动学极限。`patch_moveit_config.py` 会补全缺失的关节限位，并把 `max_velocity` / `max_acceleration` 统一写成浮点数（避免 ROS 参数按整数解析）。
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

## `ur5e_gripper_controller/` —— 夹爪控制器

把**高层**夹爪命令翻译成**低层**关节位置命令的 ROS2 节点：订阅 `gripper_command_topic`（`Float64`，`0.1=开 / 0.0=关`），按 `ur5e.yaml` 的 `gripper_positions` 线性映射成每根手指的关节值，发布到低层 `topic_name`（`Float64MultiArray`）。

```
ur5e_gripper_controller/
├── CMakeLists.txt               # ament_cmake 构建
├── package.xml                  # ROS2 包描述
└── src/
    └── ur5e_gripper_controller.cpp   # 控制器实现（由脚本生成）
```

- `CMakeLists.txt` / `package.xml`：构建与安装可执行文件。
- `src/ur5e_gripper_controller.cpp`：**由 `scripts/generate_gripper_controller.py` 根据 `<robot>.yaml` 自动生成，请勿手工修改**。随后由 `patch_moveit_config.py` 扫描该 C++ 中的 `Float64` 订阅 topic（`--gripper-controller-path`，默认自动查找 `*_gripper_controller` 目录），写入 `robot_profile.yaml` 的 `gripper_controller` 字段。

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

所有脚本都在 `custom-robot/` 根目录下运行，并且会**自动探测唯一的 `robot_<name>` 工程**（当模板 `robot_ur5e` 与目标机器人共存时，选非 `ur5e` 的那个）。

### 步骤 1：创建工程

通过 `scripts/init_project.py` 新建名字为 `robot_<新名字>/`的新工程，并写入初版 `metadata.json`：

```bash
python scripts/init_project.py <新机器人名>
```

机器人名规则：必须以小写字母开头，只能包含小写字母、数字和下划线，且下划线不能位于开头、结尾或连续出现（如 `r1`、`ur5e`、`galaxi_r1` 合法；`1r`、`_r1`、`galaxi_`、`galaxi__r1` 非法）。

### 完整流程（按生成依赖排序）

`init_project.py` 会把模板里的 MoveIt 配置、URDF、mesh 等清空为占位内容，所以**先用手工补齐你自己的 MoveIt Assistant 配置包与 URDF**，再按下列顺序运行生成脚本。

| 顺序 | 步骤 | 作用 | 输入 → 输出 |
| --- | --- | --- | --- |
| 1 | `init_project.py <name>` | 复制模板、改名、清空占位、写初版 metadata | `robot_ur5e/` → `robot_<name>/` |
| 2 | （手工）补齐 MoveIt 配置与 URDF | 填回被清空的 `<name>_config/` 与 `<name>_description/urdf/` | MoveIt Assistant 包 + URDF |
| 3 | `generate_robot_yaml.py` | 由 URDF/SRDF/MoveIt 生成机器人配置 | URDF+SRDF+controllers → `<name>.yaml` |
| 4 | `generate_gripper_controller.py` | 生成夹爪控制器 C++ | `<name>.yaml` → `<name>_gripper_controller/src/*.cpp` |
| 5 | `patch_moveit_config.py` | 扫描夹爪 C++、规整关节限位（浮点数）、生成机器人档案 | C++ → `robot_profile.yaml` |
| 6 | `generate_metadata.py` | 生成 `record_topic` | `<name>.yaml`+controllers → `metadata.json` |

**依赖关系：**

- `generate_robot_yaml.py` 需要 `<name>_config/config` 与 `<name>_description/urdf` **已经填好**。若缺失且仓库里仍有模板 `robot_ur5e`，它会**退回并基于模板 ur5e 生成**——所以务必先完成步骤 2。
- `generate_gripper_controller.py` 依赖 `<name>.yaml`（步骤 3 的产物）。
- `patch_moveit_config.py` 需要在 `generate_gripper_controller.py` **之后**运行，才能扫描到生成的夹爪 C++（`--gripper-controller-path`，默认自动查找）。
- `generate_robot_yaml.py` 输出的 `fixed_base` **故意留空**，需手工填写后再继续后续步骤。
- `generate_robot_yaml.py` 采用半自动化：机械臂、夹爪、躯干等无法自动推导的字段会留空并打印 `[WARN]` 与 `[PARTIAL]`，不会中断生成，需手工补齐后再继续。

```bash
cd samples/robot-integration/robot-asset-onboarding/custom-robot

python scripts/init_project.py <新机器人名>           # 1
# 2 手工放入 MoveIt Assistant 配置包与 URDF
python scripts/generate_robot_yaml.py                 # 3（生成后手填 fixed_base）
python scripts/generate_gripper_controller.py         # 4
python scripts/patch_moveit_config.py                 # 5
python scripts/generate_metadata.py                   # 6
```

> 各脚本的具体参数（`--package-path` / `--prefab-config` 等）以其 `--help` 为准；正常使用下多数路径都能自动探测。
