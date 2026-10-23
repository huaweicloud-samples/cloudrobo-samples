# 需求说明 — grapedev-bot 门禁阻断能力（检视结论上报 commit status）

> 状态：待平台侧评估 | 2026-10-22 定稿，2026-10-23 修订（见文末 §九）| 提出方：本仓库维护者（zhuzhe）
> 背景：仓库级技能 `custom-pr-review`（`.grape/skills/qa/custom-pr-review/`，现 v2.0.1）+ PR 订阅 QA 审查已在实际 PR 上运行，但检出的阻断级问题无法阻断合入（详见 §一 证据）。
> 关联：本需求**显式取代** `spec.md`（Issue #3）Out of Scope 中「不阻断 PR 合入、不作为门禁」的决策（§Risks 有变更管理说明）。

## Objective

- **要什么**：让 grapedev-bot 能把 PR 检视结论上报为 GitHub **commit status**，使分支保护（Require status checks）能够据此阻止合入。
- **为什么**：当前机器人只有评论通道，而分支保护只读取「失败信号」（失败 status / REQUEST_CHANGES review），不读取评论内容——阻断级问题检出再多，合入按钮也不受影响。机器人的 AI 语义级检视（全角冒号密码、CVE 关联等 CI 难以复算的发现）是核心价值，其结论应能直接生效为门禁。
- **用户**：本仓库 PR 作者与审查者；所有为仓库配置了机器人代码审核 + 分支保护的 CloudRobo 用户。
- **成功标准**：机器人检出阻断级问题的 PR，`mergeable_state=blocked`、合入按钮禁用；修复并推送后恢复可合入；检视评论照常发布，详情不丢失。

## 一、问题证据链（可复现）

以 PR `huaweicloud-samples/cloudrobo-samples#12`（base 分支 `zhuzhe1020`）为实测样本，2026-10-22：

| # | 实测项 | 结果 | 获取方式 |
|---|--------|------|---------|
| 1 | 机器人检视结论 | 「pass with blockers，须修复 2 项阻断级问题」，并自述「仅评论报告不直接阻断合入」 | PR 评论（grapedev-bot，`issues comments create` 发布） |
| 2 | 目标分支保护 | `protected: true` | `GET /branches/zhuzhe1020` |
| 3 | PR 合入状态 | `mergeable: true`，`mergeable_state: clean`（无任何阻断） | `GET /pulls/12` |
| 4 | PR review | 0 条（无 REQUEST_CHANGES） | `GET /pulls/12/reviews` |
| 5 | commit status | 无 | `GET /commits/{sha}/status` |
| 6 | check runs | 仅 `ci = success`（仓库 placeholder CI，恒绿） | `GET /commits/{sha}/check-runs` |

**根因**（三层，均在平台/CLI 能力边界内，仓库侧无法配置解决）：

1. **通道缺失**：grape CLI 契约（本仓库 `references/grape-cli-reference.md` §4 事实基线）无 status / check-run / review-event 上报端点，且「无 grape api 逃生舱」。
2. **凭证缺失**：机器人运行时仅注入 grape 会话凭证（`GRAPE_SESSION_TOKEN`，`tk_`），无 GitHub 凭证；技能安全硬约束「CLI 优先、禁止绕过直连 API」为平台刻意设计，不应突破。
3. **技能约束**：现行 SKILL.md 硬约束「仅评论不阻断 / 阻断 PR 合入 ❌ 禁止」是 Issue #3 时代的显式决策，能力就位后需同步修订（§五）。

## 二、Scope

### In Scope（平台侧，P0）

- grape CLI 新增命令：`git-repo statuses create`（§三 契约）
- 平台服务端放行对应 GitHub API，并为机器人凭证授予该仓库 status 写权限
- CLI 契约文档/`grape refresh` 缓存同步更新，`grape commands` 实列可见新命令

### In Scope（仓库侧，依赖平台交付后实施，本需求仅列出）

- 本仓库技能资产修订（§五）：`grape-cli-reference.md`、`SKILL.md`、`pr-review-runbook.md`
- 分支保护规则勾选 required check（§六，管理员 UI 操作）

### Out of Scope

- **不改**检视/阻断级的判定逻辑本身（什么算「阻断级」仍由技能规则定义，本需求只负责把结论送达门禁）〔2026-10-23 修订：阻断级判定已移出技能、由仓库 CI 门禁定义，见 §九 #4；本条「语义级发现由 AI 检出」部分不变〕
- **不选**仓库侧 CI 复算门禁方案：语义级问题 CI 无法复算，且双份规则必然漂移（见 §三 决策表）〔2026-10-23 修订：规则化子集的 CI 复算已作为过渡门禁落地，见 §九 #1；语义级不可复算的结论仍然成立〕
- **不改**会话/token 注入机制（沿用现有 `GRAPE_SESSION_TOKEN`）
- **不新增**其他 Issue/PR 写端点

## 三、技术方案

### 关键设计决策

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 上报载体 | commit status / check-run / REQUEST_CHANGES review | **commit status（P0）** | 与分支保护 Require status checks 天然集成；按 commit 幂等；无人值守语义（REQUEST_CHANGES 依赖 Require approvals 且涉及人工 dismiss，降为 P1 可选） |
| 仓库侧兜底 | 平台能力 / 仓库 CI 复算 | 平台能力（语义级）+ CI 复算（规则化子集，2026-10-23 起，见 §九 #1） | 语义级发现是机器人核心价值，CI 无法复算；规则化子集由 CI 兜底，与技能规则文档同源维护 |
| context 命名 | 自由文本 / 平台统一规范 | 平台统一前缀 `grape-qa/{技能名}` | context 是 required check 的勾选标识，必须稳定且可枚举（§七 风险） |

### CLI 命令契约（新增，命名沿用 `git-repo` 风格）

```bash
grape git-repo statuses create \
  --owner "$REPO_OWNER" --repo "$REPO_NAME" \
  --sha "<PR head sha（pulls get 已返回，无需新增只读命令）>" \
  --state failure          # success | failure | error | pending
  --context "grape-qa/custom-pr-review" \
  --description "阻断级问题 2 项，须修复后合入" \
  --target-url "<检视评论/任务详情 URL>" \
  --json-out
```

- 映射 GitHub `POST /repos/{owner}/{repo}/statuses/{sha}`（body: `state`/`context`/`description`/`target_url`）
- `--state` 由技能按检视结论映射：**存在阻断级问题 → `failure`；无阻断级 → `success`**；检视执行异常 → `error`（不误拦）
- `--target-url` 指向本次检视评论，保证「红灯 → 详情」可跳转
- 退出码语义沿用现有约定（`≥400` HTTP 透传；403 → 需管理员权限）

### 平台侧权限

- 机器人凭证需对目标仓库具备 commit status 写权限（GitHub 侧等价 `repo`/`statuses:write`）
- 服务端代理对 `tk_` 会话放行该端点；401/403 行为与现有 git-repo 写端点一致

## 四、验收标准（以 PR #12 场景回归）

- [ ] 含阻断级问题的 PR：检视完成后 head commit 出现 `context=grape-qa/custom-pr-review, state=failure`；`mergeable_state=blocked`，合入按钮禁用
- [ ] 修复阻断项并推送：新 head commit 上报 `success`，恢复可合入
- [ ] 无阻断级问题的 PR：上报 `success`，不误拦
- [ ] 检视评论照常发布（问题明细不丢失），status `target_url` 可跳转到评论
- [ ] 上报失败（网络/权限）：重试 ≤3 次后降级为仅评论，并在评论中如实说明「门禁状态未上报，本次不阻断」
- [ ] 回归：既有检视流程（拉 diff → 扫描 → 评论 → `tasks complete`）行为不变
- [ ] `grape commands git-repo statuses` 可见新命令；`grape refresh` 后契约缓存含该端点

## 五、本仓库配套变更（平台交付后由本仓库 PR 实施，此处为预告清单）

| 文件 | 变更 |
|------|------|
| `references/grape-cli-reference.md` | §3 登记 `git-repo statuses create`；§4「不存在能力」相应条目移除 |
| `SKILL.md` | 硬约束「仅评论不阻断 / 阻断 ❌ 禁止」→「阻断级命中 → statuses create(failure)；无阻断级 → success；上报失败降级为仅评论并说明」；版本 2.1.0 → 2.2.0 |
| `references/pr-review-runbook.md` | 新增 Step 4.5「上报门禁状态」（Step 4 发布评论之后、Step 6 收尾之前）；错误排查表增补上报失败条目；评论模板「仅评论报告不直接阻断合入」→「已上报门禁状态：failure/success（context=…）」 |
| `spec.md`（Issue #3） | Out of Scope「不阻断」条目标注「已由 blocking-gate-spec.md 取代」，不删除原文（保留决策轨迹） |
| 分支保护 | 见 §六 |

## 六、仓库侧分支保护配置（管理员，平台交付并验证后）

1. Settings → Branches → 编辑 `main` / `zhuzhe1020` 保护规则
2. 勾选 **Require status checks to pass before merging** → 选中 `grape-qa/custom-pr-review`
   - 注意：context 至少真实上报过一次后才会出现在可选列表，需先在测试 PR 上触发一次
3. （可选）Require conversation resolution：要求机器人评论中「须修复」会话处理完毕

## 七、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 平台排期不可控 | Med | 未交付前由规则化子集 CI 复算兜底（`.github/scripts/pr_gate.py` + required check `ci`，2026-10-23 起，见 §九 #1）；语义级发现维持评论不阻断 |
| 误报导致正常合入被卡 | Med | 阻断级判定收敛并与技能规则同源；应急放行走管理员在保护规则中临时取消 required（平台需提供操作手册） |
| context 命名变更致 required check 失联 | Med | context 作为稳定契约固化于本文档，变更须同步修订本文档 + 各仓库分支保护 |
| 检视任务中断未上报（如 agent 崩溃） | Low | status 停留在「无上报」态而非 success，不会误放行；可后续考虑 pending 兜底 |
| REQUEST_CHANGES 路线的人工 dismiss 语义与无人值守冲突 | Low | 仅列为 P1 可选（`pulls reviews create --event REQUEST_CHANGES`），不作为主路径 |

## 八、里程碑建议

- **P0**：`git-repo statuses create` + 权限放行 + 契约文档（§四 验收）
- **P1**（可选）：`pulls reviews create` 支持 `--event REQUEST_CHANGES`，供依赖 Require approvals 的仓库选用
- **交付后**：本仓库按 §五 出技能修订 PR，按 §六 配置分支保护，在 PR #12 类场景回归验收

## 九、修订记录

- **2026-10-23**（随 feat/issue-3 收录进 git 时修订）：
  1. **推翻 Out of Scope「本仓库不引入 CI 复算」**：规则化阻断子集的 CI 复算门禁已落地——`.github/scripts/pr_gate.py` 接入 `.github/workflows/ci.yml`（job 名 `ci`，分支 feat/issue-3），覆盖编译失败/密钥正则/License 合规三类机器可判定阻断，作为本 spec P0（statuses create）交付前的过渡兜底。**「语义级发现 CI 无法复算」的结论不变**，仍依赖平台交付；`pr_gate.py` 规则与 `gate-checks-guide.md` 同源维护（技能 v2.0.1），漂移风险以同步修订约束。
  2. **License 门禁规则修订**（维护者定义）：仓库根 LICENSE 缺失 → 仅提示不阻断；存在则须合规（改坏/篡改特征 → 阻断）。技能文档已同步升 v2.0.1，平台侧需重新 `grape skills upload --scope repo` 覆盖注册。
  3. 版本引用更新：背景中技能 v1.1.0 → 现 v2.0.1；§五 SKILL.md 目标版本由 1.1.0→1.2.0 改为 2.0.1→2.1.0。
  4. **职责拆分**（同日第二批变更）：编译构建/密钥泄露/License 合规三类阻断级检查移出技能（技能收敛为 4 类警告级门禁，v2.0.1 → v2.1.0），由仓库 CI required check `ci` 独占承担。#1 所述「与 gate-checks-guide.md 同源维护」的漂移风险随之消解：阻断级规则单一来源 = CI 门禁脚本。技能不再产出阻断级结论，AI 语义级检视结论仍随评论报告（平台 statuses 能力交付后按 §五 上报）。
