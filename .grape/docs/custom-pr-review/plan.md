# 方案/计划 — custom-pr-review（敏感词校验）

## 实现方案

由于本仓库为样例仓库（无 skills/ 目录），grape 平台官方 QA 技能不在本仓库代码内，采用**仓库内包装方案**：在 `.grape/skills/qa/custom-pr-review/` 下创建一个新的 QA skill，作为 PR 检视敏感词校验的技能资产。

该 skill 被 qa agent 在 PR 审查环节调用，执行敏感词校验并附加到检视评论。

## 实现顺序

1. **SKILL.md**：定义技能定位、安全约束、处理流程引用、领域规则（敏感词校验规则表）、操作边界、任务完成流程。
2. **references/pr-review-runbook.md**：执行走查表（Step 0-6），每步给出确切 grape CLI 命令 + bash 扫描逻辑。
3. **references/sensitive-scan-guide.md**：敏感词校验指南（内置词表、IP 正则、词表扩展、diff 行号定位、评论格式、测试覆盖要求）。
4. **references/grape-cli-reference.md**：CLI 命令参考（qa 相关命令子集）。
5. **docs/ 开发过程文档**：spec.md（来源 issue agent）、plan.md（本文档）、task.md（任务拆解）。

## 依赖关系

- 敏感词校验模块被检视评论组装调用（新增调用），不改既有模块间依赖。
- 依赖 grape CLI `git-repo pulls files`（获取 PR diff）+ `git-repo pulls comments create`（发布评论）+ `git-repo contents`（读取词表文件）。

## 风险

| 风险 | 缓解 |
|------|------|
| IP 正则误报版本号 | 报告标注「疑似 IP」，审查者自行判断 |
| 大 diff 性能 | 仅扫变更文件新增行，逐文件流式处理 |
| 仓库无自定义词表 | 仅用内置词表，不报错 |
