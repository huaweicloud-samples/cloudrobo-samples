# CloudRobo 示例撰写规范

## 目录层级

示例路径固定为：`samples/<capability-domain>/<learning-task>/<sample-id>/`。

- `capability-domain`：平台能力域，例如 `simulation`、`data`、`embodied-models`、`ascend`、`robot-integration`。
- `learning-task`：用户要完成的任务，例如 `robot-asset-onboarding` 或 `policy-inference`。
- `sample-id`：可运行的具体示例，例如 `ur5e-gripper-realsense`。

不要在仓库根目录直接新增示例，也不要用 `demo`、`test`、`custom` 等无法表达用户任务的泛化名称。

## 最末级示例结构

```text
<sample-id>/
├── README.md          # 学习入口和可执行说明
├── manifest.yaml      # 机器可读元数据
├── docs/              # 分步骤或背景说明（按需）
├── scripts/           # 该示例专用且可重复执行的脚本（按需）
├── project/           # 可直接导入平台或运行时的完整工程（按需）
├── configs/           # 可单独复用的配置（按需）
├── assets/            # 源模型、缩略图等非工程资产（按需）
└── tests/             # 最小验收或自动校验（按需）
```

`project/` 内必须保留目标运行时所要求的结构；例如 Unity/O3DE/CloudRobo 工程的 `Assets/` 不能被拆散。二进制网格、纹理等大文件应使用 Git LFS，并注明来源和许可证；不提交构建产物、缓存和私密配置。

## 命名与内容

- 路径、示例 ID、脚本名使用英文小写 kebab-case；Python 模块可使用 `snake_case`。
- 一个示例只覆盖一个明确学习目标；复杂端到端流程拆分为多个可串联的示例。
- README 首屏必须说明：解决的问题、预计耗时、适用平台版本、硬件/软件前置条件及最终效果。
- README 必须包含：执行步骤、预期结果、失败排查入口、清理方式和已知限制。
- 每个示例标记成熟度：`runnable`（可直接运行）、`adaptation-required`（需替换硬件/参数）或 `reference-only`（仅供配置参考）。

## manifest.yaml 最小字段

```yaml
id: sample-id
title: Human-readable title
summary: One-sentence summary
maturity: runnable
tags: []
cloudrobo_version: ""
dependencies: []
hardware: []
maintainers: []
```
