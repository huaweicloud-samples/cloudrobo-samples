# 机器人碰撞模型简化

本文档记录当前推荐的 O3DE 机器人 STL 碰撞模型简化流程。

## 背景

ROS2 robot importer 会根据每个导入的 STL 生成 PhysX 碰撞数据。对于复杂的机器人连杆，过于精细的碰撞网格可能生成大量碰撞形状，导致关卡运行帧率很低，或者在使用过高精度分解后关卡无法打开。

当前主工具是 `collision_simplification/simplify_robot_collisions.py`。它会向 `.assetinfo` 文件写入受限的 V-HACD 凸分解参数。每个 STL 会按照质量预设和源网格大小，被近似成多个凸包。这样可以在运行速度和碰撞精度之间做权衡，而不是把每个 STL 强制近似成单个凸包。

该工具与从具体 O3DE 工程解耦，可以从任意目录运行。处理某个 O3DE 工程时，建议显式传入 `--project-root`，让脚本可以正确解析 prefab 中的嵌套 prefab 和 `.pxmesh` asset hint。

## 支持的输入

该工具不绑定某一款具体的机器人。为了方便起见，下面以星海图r1机器人为例进行介绍。

支持的输入路径包括：

- 机器人 prefab，例如 `$PROJECT_ROOT/Assets/Importer/r1_robot.prefab`
- 关卡 prefab，例如 `$PROJECT_ROOT/Levels/r1_empty/r1_empty.prefab`
- 机器人资源目录，例如 `$PROJECT_ROOT/Assets/Importer/2457699799_panda`
- 较大的导入目录，例如 `$PROJECT_ROOT/Assets/Importer`
- 直接传入 `.assetinfo` 文件

当输入是 prefab 时，工具会递归跟随其中的嵌套 prefab `Source` 引用，读取 `.pxmesh` asset hint，并反向解析到对应的源 `.assetinfo` 文件。

下面示例使用变量表示路径。`PROJECT_ROOT` 是 O3DE 工程目录，请替换成你本机的工程路径：

```bash
PROJECT_ROOT=<O3DE_PROJECT_ROOT>
O3DE_ENGINE=<O3DE_ENGINE_ROOT>
TOOL=collision_simplification/simplify_robot_collisions.py
```

## 推荐流程

推荐顺序是：

1. 先 dry run，确认将要处理的 `.assetinfo` 数量和凸分解参数。
2. 加 `--apply` 写入修改。
3. 运行 AssetProcessorBatch 重新生成 product assets。
4. 在 O3DE 中打开 `physx_Debug 1` 检查碰撞形状。

## 预览运行

建议始终先做 dry run。没有 `--apply` 时，命令不会修改任何文件。

```bash
$TOOL "$PROJECT_ROOT/Levels/r1_empty/r1_empty.prefab" \
  --project-root "$PROJECT_ROOT" \
  --assetinfo-mode decompose \
  --quality low
```

常用示例：

```bash
$TOOL "$PROJECT_ROOT/Assets/Importer/r1_robot.prefab" --project-root "$PROJECT_ROOT" --assetinfo-mode decompose --quality high
$TOOL "$PROJECT_ROOT/Assets/Importer/panda.prefab" --project-root "$PROJECT_ROOT" --assetinfo-mode decompose --quality high
$TOOL "$PROJECT_ROOT/Levels/r1_empty/r1_empty.prefab" --project-root "$PROJECT_ROOT" --assetinfo-mode decompose --quality low
$TOOL "$PROJECT_ROOT/Assets/Importer" --project-root "$PROJECT_ROOT" --assetinfo-mode decompose --quality medium
```

重点查看输出中的这些字段：

- `Assetinfo files resolved from prefabs`：通过 prefab 引用找到的碰撞源文件数量。
- `PhysX mesh groups seen`：将要处理的 PhysX mesh group 数量。
- `Files that would change`：当前设置下会发生变化的文件数量。
- `Decomposition preview`：即将写入的凸分解参数预览。

## 应用修改

确认 dry run 输出符合预期后，加入 `--apply`。

处理 R1 prefab：

```bash
$TOOL "$PROJECT_ROOT/Assets/Importer/r1_robot.prefab" \
  --project-root "$PROJECT_ROOT" \
  --assetinfo-mode decompose \
  --quality high \
  --apply
```

处理单个机器人 prefab：

```bash
$TOOL "$PROJECT_ROOT/Assets/Importer/panda.prefab" \
  --project-root "$PROJECT_ROOT" \
  --assetinfo-mode decompose \
  --quality high \
  --apply
```

处理所有已导入的机器人资源：

```bash
$TOOL "$PROJECT_ROOT/Assets/Importer" \
  --project-root "$PROJECT_ROOT" \
  --assetinfo-mode decompose \
  --quality medium \
  --apply
```

默认情况下，被修改的文件旁边会生成 `.collision_bak` 备份。只有在明确不需要备份时，才使用 `--no-backup`。

## 质量预设

当前预设如下：

| 质量 | 每个源文件的凸包数量 | 每个凸包顶点数 | Resolution | 说明 |
| --- | ---: | ---: | ---: | --- |
| `low` | 2-8 | 32 | 100000 | 运行最快，碰撞最粗略 |
| `medium` | 4-24 | 64 | 400000 | 平衡默认值 |
| `high` | 6-32 | 80 | 600000 | 当前项目使用的较安全高精度设置 |
| `ultra` | 16-128 | 128 | 2000000 | 开销很高，可能导致资源处理或关卡加载很慢 |

凸包数量会根据源网格大小自适应计算。如果需要手动覆盖，可以这样运行：

```bash
$TOOL "$PROJECT_ROOT/Assets/Importer/r1_robot.prefab" \
  --project-root "$PROJECT_ROOT" \
  --assetinfo-mode decompose \
  --fixed-hulls 12 \
  --resolution 300000 \
  --max-vertices-per-hull 48 \
  --apply
```

## 重新处理资源

应用修改后，需要运行 AssetProcessorBatch，让 O3DE 重新生成 product assets：

```bash
ulimit -n 65535
"$O3DE_ENGINE/bin/Linux/profile/Default/AssetProcessorBatch" \
  --project-path="$PROJECT_ROOT" \
  --project-cache-path="$PROJECT_ROOT/Cache" \
  --engine-path="$O3DE_ENGINE"
```

正常结果中应该看到：

- `Number of Assets Failed to Process: 0`
- `Number of Errors Reported: 0`

## 可视化碰撞模型

项目的 `project.json` 中已经启用了 `PhysX5Debug`。在 O3DE 控制台中运行：

```text
physx_Debug 1
```

常用调试命令：

```text
physx_Debug 0
physx_Debug 1
physx_Debug 3
physx_CullingBoxSize 50
```

`physx_Debug 1` 用于显示碰撞形状和边线。`physx_Debug 3` 可用于查看 mesh collider 的近距离可视化效果。

## 恢复方式

如果使用 `high` 或 `ultra` 后导致关卡无法打开，可以退回到 `low` 或 `medium`，然后重新处理资源：

```bash
$TOOL "$PROJECT_ROOT/Levels/r1_empty/r1_empty.prefab" \
  --project-root "$PROJECT_ROOT" \
  --assetinfo-mode decompose \
  --quality low \
  --apply
ulimit -n 65535
"$O3DE_ENGINE/bin/Linux/profile/Default/AssetProcessorBatch" \
  --project-path="$PROJECT_ROOT" \
  --project-cache-path="$PROJECT_ROOT/Cache" \
  --engine-path="$O3DE_ENGINE"
```

如果存在备份，原始源文件元数据会保存为：

```text
*.assetinfo.collision_bak
*.prefab.collision_bak
```

也可以直接从 `.collision_bak` 文件手动恢复对应的 `.assetinfo` 或 `.prefab`，再重新运行 AssetProcessorBatch。

## 工程解耦说明

`simplify_robot_collisions.py` 是普通 Python 脚本，不依赖 O3DE Editor Python 或 `azlmbr`。它只读写传入路径下的 `.assetinfo` 和 `.prefab` JSON 文件。

因此，脚本和文档可以放在工作区的 `collision_simplification/` 目录里，不需要留在具体 O3DE 工程目录中。真正和工程绑定的是输入路径、`--project-root`，以及后续的 AssetProcessorBatch 资源重新处理步骤。
