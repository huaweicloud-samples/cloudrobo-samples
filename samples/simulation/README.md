# 仿真能力域

本目录收录场景构建、传感器仿真、sim-to-real 与策略评测联调示例。示例按 `samples/simulation/<learning-task>/<sample-id>/` 组织（历史示例暂保留现有层级），具体规范见 [`docs/conventions/sample-authoring.md`](../../docs/conventions/sample-authoring.md)。

## 示例导航

| 学习任务 | 示例 | 成熟度 | 说明 |
| --- | --- | --- | --- |
| 策略评测联调 | [`robotwin/robotwin_bridge`](robotwin/robotwin_bridge/) | `adaptation-required` | 打通 RoboTwin 2.0 官方评测脚本与 CloudRobo 远端世界模型（ws ↔ wss、格式翻译、token 认证） |

## 学习路径

1. 先按 [`robotwin/robotwin_bridge/README.md`](robotwin/robotwin_bridge/README.md) 的“快速概览”核对硬件、软件与平台版本。
2. 完成单任务联调，确认 `[CONNECTED]` 与成功率输出。
3. 再按官方 `env_cfg/eval/all_tasks.yml` 扩展为全部任务评测。

返回 [仓库示例总览](../../README.md)。