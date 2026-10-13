---
name: custom-pr-review
version: 1.0.0
description: |
  PR 检视敏感词校验技能。对 PR 变更文件内容扫描内部域名+IP，命中时随检视评论报告文件+行号。
  ①拉取 PR diff 变更文件 → ②扫描内置词表+IP 正则 → ③命中清单附加到检视评论末尾。
tools:
  - bash
---

## 安全硬约束（必须遵守）

1. **CLI 优先**：凡 grape CLI 能做的走 CLI，禁止绕过直连 API；`$GRAPE_SESSION_TOKEN` 自动注入，不探索其他 token。
2. **任务上下文先行**：执行前先读 `grape tasks current` + `grape issues detail`，明确已做/待做。
3. **仅评论不阻断**：敏感词命中仅随检视评论报告，不阻断 PR 合入、不作为门禁、不影响原审查结论。
4. **token 不写文件/日志/评论**；403 → 停止并回复「需管理员权限」，不换 token。

## Prerequisites（执行前必检，通过后才进入处理流程）

1. **grape CLI 版本满足（必须 ≥ 0.3.2）**：
   ```bash
   grape --version
   ```
2. **刷新契约到最新**：
   ```bash
   grape refresh
   ```
3. **认证与环境变量就绪**：
   ```bash
   echo "token=${GRAPE_SESSION_TOKEN:+ok} owner=$REPO_OWNER repo=$REPO_NAME pr=${PR_NUMBER:-MISSING} work=${GRAPE_WORK_BRANCH:-main}"
   ```

## 环境变量（服务端已注入，直接使用）

| 变量 | 语义 | 用途 |
|------|------|------|
| `GRAPE_SERVER_URL` | 服务端地址 | `grape` 自动读取 |
| `GRAPE_SESSION_TOKEN` | 会话级临时 token（`tk_`） | `grape` 自动认证 |
| `REPO_FULL_NAME` | `owner/repo` 仓库全名 | A2A payload |
| `REPO_OWNER` | 仓库属主 | `--owner "$REPO_OWNER"` |
| `REPO_NAME` | 仓库名 | `--repo "$REPO_NAME"` |
| `ISSUE_NUMBER` | Issue 编号 | A2A payload |
| `PR_NUMBER` | 当前 PR 编号 | `--number "$PR_NUMBER"` |
| `GRAPE_SCENE_ID` | 场景 ID | A2A 前校验节点 |
| `BOT_USERNAME` | Bot 用户名 | 评论身份 |
| `AGENT_NAME` | Agent 显示名 | 评论前缀 `[QA Agent]` |

## 定位

PR 检视时对变更文件内容做敏感词校验（内部域名词表 + IP 正则），命中则随检视评论报告命中位置（文件+行号），不影响原审查结论、不阻断合入。审查完成后 A2A 交棒 **sre** agent（发布/部署）。

## 评论规范

- PR 评论 body 必须以 `[QA Agent]` 开头。
- 敏感词校验结果附加到检视评论末尾「敏感词校验结果」段落；未命中不附加。
- 引用原文：`> {用户原文}\n\n[QA Agent] {回复}`

## 处理流程

> ⚠️ **先读 `references/pr-review-runbook.md`**：按走查表逐步骤执行（Step 0 前置+读上下文 → 1 拉 PR 变更文件 → 2 扫描敏感词 → 3 组装评论 → 4 发布评论 → 5 A2A 交棒 sre → 6 收尾）。每个步骤的命令 runbook 已给出，**照抄执行，不要 `grape --help` / `grape commands` 逐条探索**。
>
> ⚠️ **本技能收尾一律用 `grape tasks complete`（情形一）**：最后一步必须调用 `grape tasks complete` 自报完成，调完即结束。

敏感词校验规则详见 `references/sensitive-scan-guide.md`（内置词表 / IP 正则 / 词表扩展 / diff 行号定位 / 评论格式）。

## 领域规则：敏感词校验

| 规则 | 说明 |
|------|------|
| 内置词表 | `huawei.com` `codehub` `myhuaweicloud.com`（子串匹配，大小写不敏感） |
| IP 正则 | IPv4 + IPv6，含公网/内网；加边界约束减少版本号误报 |
| 词表扩展 | 仓库 `docs/sensitive-words.txt` 存在则逐行读取合并（追加到内置词表） |
| 校验范围 | 仅 PR 变更文件内容（新增行），不查 PR 标题/描述 |
| 命中输出 | `(文件路径, 行号, 命中词)` 三元组列表 |
| 命中动作 | 附加到检视评论末尾；未命中不附加 |
| 行号定位 | diff 新行号（与 PR 变更视图一致） |

## 操作边界

| 行为 | 态度 |
|------|------|
| 按 runbook 走查表逐步执行 | ✅ **必须做** |
| 拉 PR diff 变更文件 + 扫描敏感词 | ✅ **必须做** |
| 命中时附加检视评论报告 | ✅ **必须做** |
| 未命中时不附加报告 | ✅ **必须做** |
| 词表经仓库文件扩展（合并） | ✅ **必须做** |
| A2A 交棒 sre agent | ✅ **必须做** |
| 阻断 PR 合入 / 作为门禁 | ❌ **禁止** |
| 校验 PR 标题/描述 | ❌ **禁止** |
| 编造不存在的命令/参数 | ❌ **禁止** |
| 裸 `git`（一律走 `grape git`） | ❌ **禁止** |

## 硬约束

- 敏感词命中仅评论报告，不阻断合入、不作为门禁
- 校验范围仅 PR 变更文件内容（不查标题/描述）
- 词表内置可扩展（仓库 `docs/sensitive-words.txt` 追加/覆盖）
- 评论 body 以 `[QA Agent]` 开头
- 建 PR 的 `base` 用 `$GRAPE_WORK_BRANCH`（不硬编码 main）

## 任务完成流程（必做）

完成全部业务动作后，依次执行：

1. **输出分析摘要**：PR 编号 + 变更文件数 + 命中数 + 评论发布结果
2. **调用 `grape tasks complete` 自报完成（必做）**：
   ```bash
   grape tasks complete --json '{
     "result_text": "结论：PR #'"${PR_NUMBER}"' 敏感词校验完成，命中 {n} 处，已随检视评论报告。",
     "next_step": "a2a_sre",
     "actions": ["拉取 PR diff", "扫描敏感词", "组装评论", "发布检视评论", "A2A 交棒 sre"],
     "conclusion": "PR #'"${PR_NUMBER}"' 敏感词校验完成，命中 {n} 处",
     "artifacts": ["PR #'"${PR_NUMBER}"'", "检视评论"],
     "issue_summary": "敏感词校验完成 | PR #'"${PR_NUMBER}"' | 命中 {n} 处 | A2A sre"
   }'
   ```

## 参考

- **执行走查表（先读）**：`references/pr-review-runbook.md`
- **敏感词校验指南**：`references/sensitive-scan-guide.md`
- CLI 命令基线：`references/grape-cli-reference.md`
