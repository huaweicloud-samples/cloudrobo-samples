# 贡献指南

每个提交的示例应只解决一个可验证的学习目标，并保持可独立阅读和运行。

## 新增示例

1. 在 `samples/<capability-domain>/<learning-task>/<sample-id>/` 下创建示例。
2. 提供 `README.md` 和 `manifest.yaml`；涉及多个操作步骤时，增加 `docs/`。
3. 在 README 中写明前置条件、步骤、预期结果、清理方式和已知限制。
4. 将可复用工具放入 `shared/`，维护/校验工具放入 `tools/`，不要复制到多个示例中。
5. 提交前运行该示例的最小验收流程，并在 Pull Request 中说明运行环境与结果。

完整约定见 [示例撰写规范](docs/conventions/sample-authoring.md)。
