# 示例能力域

`samples/` 下的一级目录是稳定的平台能力域，不按语言、团队或交付时间划分。

| 能力域 | 用途 |
| --- | --- |
| `simulation/` | 场景构建、传感器仿真和 sim-to-real |
| `data/` | 数据采集、转换、标注和质量校验 |
| `embodied-models/` | 具身策略训练、推理和评估 |
| `ascend/` | 昇腾环境、模型转换与推理部署 |
| `robot-integration/` | 机器人资产、ROS 2、末端执行器和传感器接入 |

新增示例必须进入相应能力域下的任务目录；具体规则见 [`docs/conventions/sample-authoring.md`](../docs/conventions/sample-authoring.md)。
