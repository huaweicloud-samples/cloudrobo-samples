# CloudRobo Samples

[![Status](https://img.shields.io/badge/Status-Incubating-blue)]()
[![Huawei Cloud](https://img.shields.io/badge/Huawei%20Cloud-Samples-red)]()
[![Scenario](https://img.shields.io/badge/Scenario-application%20intelligence-success)](https://3ms.huawei.com/docs/docinfo/1300901382590492672?bookstackId=866760814559879168&gid=3591759&l=zh-cn&documentkind=&attachmentIdx=5)

CloudRobo Embodied AI Platform 的示例代码仓库。示例按平台能力域组织，帮助用户从仿真、数据处理和模型使用逐步完成机器人接入与部署。

## 示例导航

| 能力域 | 目录 | 说明 |
| --- | --- | --- |
| 仿真 | `samples/simulation/` | 场景、传感器与 sim-to-real 示例 |
| 数据 | `samples/data/` | 采集、转换与校验示例 |
| 具身模型 | `samples/embodied-models/` | 训练、推理与评估示例 |
| 昇腾 | `samples/ascend/` | 模型转换、适配与部署示例 |
| 机器人接入 | `samples/robot-integration/` | 机器人资产、ROS 2 与末端执行器接入示例 |

当前已提供的示例：

- [`custom-robot`](samples/robot-integration/robot-asset-onboarding/custom-robot/)：机器人资产接入示例，保留原始工程目录与代码结构。

目录与示例撰写要求见 [示例规范](docs/conventions/sample-authoring.md)。

## 快速开始

1. 在上表中选择与目标相符的能力域。
2. 打开最末级示例目录中的 `README.md`，核对版本、硬件和前置条件。
3. 按文档步骤运行，并以文档列出的验收结果为准。

## 贡献

请在提交前阅读 [贡献指南](CONTRIBUTING.md)，并遵守示例目录和元数据规范。

## License

This project is licensed under the MIT-0 license.

## Maintainers

CODEOWNERS: @rebornczs

## Feedback

Please use GitHub Issues: https://github.com/huaweicloud-samples/cloudrobo-samples/issues
