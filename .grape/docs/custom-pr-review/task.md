# 任务拆解清单 — custom-pr-review（敏感词校验）

## 任务清单

| # | 任务 | 验收标准 | 状态 |
|---|------|---------|------|
| 1 | 生成 SKILL.md | ≤130 行；含安全硬约束/Prerequisites/环境变量表/定位/评论规范/处理流程/领域规则/操作边界/硬约束/任务完成流程/参考 | ✅（127 行） |
| 2 | 生成 pr-review-runbook.md | ≤250 行；Step 0-6 编号步骤 + 每步命令 + 必做标注 + 错误排查 + 明确禁止 | ✅（205 行） |
| 3 | 生成 sensitive-scan-guide.md | ≤250 行；内置词表/IP 正则/词表扩展/diff 行号定位/评论格式/测试覆盖要求 | ✅（152 行） |
| 4 | 生成 grape-cli-reference.md | ≤250 行；安装检查/认证配置/常用命令/限制声明/退出码/安全边界 | ✅（69 行） |
| 5 | 沉淀 spec.md | 来源 issue agent 交付的 Spec，原样落盘（未被本次修改触碰） | ✅ |
| 6 | 沉淀 plan.md | 实现顺序/依赖/风险 + 方案核实记录 + 接线机制 | ✅ |
| 7 | 沉淀 task.md | 逐项验收/验证清单 | ✅ |
| 8 | 质量自检 | 按 QA 复审清单逐项复核 | ✅ |
| 9 | 建 PR | 关联 Issue #3；PR #5（head `feat/issue-3`，base `main` 仓库默认分支） | ✅ |
| 10 | 交棒（条件触发） | 检视流程内不交棒；仅 PR 合并验证（`pull_request_merged`）通过后交棒 sre | ✅（规则已落地，当前非合并事件故不交棒） |
| 11 | tasks complete | 结构化字段真实填充（`next_step=done`） | ✅ |
| 12 | 按 QA 复审意见修复（1 Critical + 7 Required + 1 Nit） | 逐项修复并在 PR 评论给出可核验证据 | ✅ |
| 13 | 扫描实现可执行性实测 | runbook Step 2 逐字复制执行，对 PR #5 输出命中明细、与独立实现计数一致 | ✅（head `9d6b1fd` 实测 32 处，与 QA 三次独立实测一致；更早提交 `03a852a` 时为 35 处，差异源于提交间文档行增减） |
| 14 | 行号口径实测（空新增行） | 修正实现 2/3/4 行号正确；旧 `/^\+[^+]/` 复现偏小 1 的缺陷 | ✅ |
| 15 | HIT_COUNT 变量赋值（QA 复审 6143937627 Required） | Step 2 赋值 `HIT_COUNT=$(printf '%s\n' "$SCAN_OUT" \| grep -c ':' \|\| true)`，Step 5/6 引用改为该赋值来源 | ✅（实测 HIT_COUNT=32，与 HITS= 对账一致；零命中样例返回 0 不中断） |

## 本轮修复对照（QA 复审 6141480826）

| 审查项 | 级别 | 修复 |
|--------|------|------|
| plan.md 方案与 spec 冲突 + 接线机制缺失 | 🔴 Critical | plan.md §一 补方案核实记录（6 条可复现证据，结论：官方技能不可定制）；§二 补接线机制（`skills upload --scope repo` 注册 + 核验命令 + 平台侧待确认项）；§一 末标注 bounded deviation 与可撤销性 |
| 扫描脚本不可执行（单引号不展开 + jq 依赖 + awk 空占位） | 🔴 Required | runbook Step 2 改为可直接执行的 python3 实现（管道 stdin + argv 传词表，无 jq 依赖），逐字复制实测通过 |
| awk 不可移植 + 漏空新增行 | 🔴 Required | guide §4 行号口径统一；参考实现改用 POSIX `match(2 参数)` + `RSTART/RLENGTH`；排除文件头用独立 `/^\+\+\+/` 规则，`/^\+/` 覆盖纯 `+` 空行；附 2/3/4 校验样例 |
| 无条件 A2A 交棒 sre | 🔴 Required | SKILL.md 定位/操作边界/硬约束/完成流程与 runbook Step 5 统一为「检视流程内不交棒，仅 `pull_request_merged` 合并验证通过后条件触发」，`next_step=done` |
| env 表与运行时不符 + 无关硬约束 | 🔴 Required | SKILL.md env 表拆 A/B 两类，B 类逐项标注注入条件与缺失降级；删除「建 PR base 用 $GRAPE_WORK_BRANCH」等与检视职责无关的硬约束 |
| `{原审查结论}` 占位符未定义 | 🔴 Required | runbook Step 3 补占位符定义表（`{review_conclusion}`/`{hit_count}`/`{hit_rows}` 的来源与缺失处理）与组装规则；说明 CLI 无评论更新能力，结论与敏感词段落在同一条评论内发布 |
| task.md 交棒对象 qa/sre 矛盾 | 🔴 Required | task.md #10 与 SKILL.md/runbook 统一（条件触发 sre） |
| PR body `Closes` 语法非法 | 🔴 Required | PR #5 body 更新为 `Closes #3` |
| task.md 自检项状态过时 | ⚪ Nit | task.md 全表状态更新为实际值并补本轮记录 |
| tools 缺 grape | 🟢 Optional | tools 补 `grape` 与 `python3`（扫描实现依赖） |

## 本轮修复对照（QA 复审 6143937627，head `9d6b1fd`）

| 审查项 | 级别 | 修复 |
|--------|------|------|
| runbook `HIT_COUNT` 变量从未赋值，Step 5/6 恒报命中 0 | 🔴 Required | Step 2 增加赋值行 `HIT_COUNT=$(printf '%s\n' "$SCAN_OUT" \| grep -c ':' \|\| true)`；Step 5 头注、Step 6 头注与 Step 3 占位符表、SKILL.md `{hit_count}` 定义统一注明来源为该赋值 |
| 「实测 35 处命中」声明在当前 head 不可复现（实测 32 处） | ⚪ Nit | task.md #13 更新为「head `9d6b1fd` 实测 32 处」，并标注 35 处系更早提交 `03a852a` 时的实测值、差异源于提交间文档行增减；PR body 同步修正 |
| `grep -c ':'` 零命中退出码 1 可能在 `set -e` 环境中断 | ⚪ Nit | 赋值行内联 `\|\| true`（随 Required 一并修复）；实测零命中样例返回 0 且不中断 |

## 验收映射

- **变更文件出现内部域名或 IP 时，检视评论准确列出所有命中位置（文件+行号）** → runbook Step 1-2（python3 扫描实现）+ Step 3 评论组装；行号口径见 guide §4 校验样例
- **未命中不附加报告，原审查结论与格式不变** → Step 3 组装规则（命中=0 不输出敏感词段落）
- **仅评论报告不阻断** → 安全硬约束第 3 条 + 操作边界表
- **词表可经仓库文件扩展** → guide §3 词表扩展
- **现有测试通过 + 新增测试覆盖** → guide §6 测试覆盖要求（含空新增行、多 hunk 场景）
- **技能可被本仓库任务加载** → plan.md §二 接线机制（`skills upload --scope repo` 注册 + 核验命令）
