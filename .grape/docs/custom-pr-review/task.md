# 任务拆解清单 — custom-pr-review v2.0.0（多门禁检视）

## v1.1.0 已完成任务（PR #5 已合并）

| # | 任务 | 状态 |
|---|------|------|
| 1-15 | v1 敏感词校验技能生成/自检/PR/QA 审查/修复/上传 | ✅ |

## v2.0.0 扩展任务（本轮）

| # | 任务 | 验收标准 | 状态 |
|---|------|---------|------|
| 16 | SKILL.md v2.0.0 | ≤130 行；7 类门禁+分级策略+工具探测+环境变量+操作边界+硬约束+任务完成 | ✅（130 行） |
| 17 | pr-review-runbook.md v2 | ≤250 行；Step 0-6 + Step 2 统一扫描脚本 + Step 2b 工具门禁 + 分级评论组装 | ✅（193 行） |
| 18 | gate-checks-guide.md（新增） | ≤250 行；7 类门禁检测规则/正则/分级/降级/评论格式/测试覆盖 | ✅（161 行） |
| 19 | sensitive-scan-guide.md | 沿用 v1，不变 | ✅ |
| 20 | grape-cli-reference.md | 沿用 v1，不变 | ✅ |
| 21 | docs 更新 | spec.md/plan.md/task.md 更新为 v2.0.0 | ✅ |
| 22 | 质量自检 | 行数限制/无残留旧命令/无 stale 字段 | ✅ |
| 23 | 建 PR | 关联 Issue #3；base=main | ⏳ |
| 24 | A2A 交棒 QA | 场景校验 qa 节点 → A2A | ⏳ |
| 25 | tasks complete | 结构化字段真实填充 | ⏳ |

## v2.0.0 验收映射

- **7 类门禁全部执行** → runbook Step 2（内置扫描）+ Step 2b（工具门禁）；gate-checks-guide.md 检测规则
- **阻断级（License/密钥/编译）标注 ❌** → SKILL.md 领域规则表 + runbook Step 3 评论组装
- **警告级（代码风格/文档/依赖/敏感词）标注 ⚠️** → SKILL.md 领域规则表 + runbook Step 3 评论组装
- **工具缺失降级** → SKILL.md Prerequisites + runbook Step 2b SKIP+备注
- **敏感词沿用 v1** → sensitive-scan-guide.md 不变
- **ci.yml 替换** → 超出技能目录范围，PR 描述标注需单独实施
