---
name: custom-pr-review
version: 1.1.0
description: |
  PR 检视敏感词校验技能。对 PR 变更文件新增行扫描内部域名+IP，命中时随检视评论报告文件+行号。
  ①拉取 PR diff 变更文件 → ②扫描内置词表+IP 正则 → ③命中清单附加到检视评论末尾。
tools:
  - bash
  - grape
  - python3
---

## 安全硬约束（必须遵守）

1. **CLI 优先**：凡 grape CLI 能做的走 CLI，禁止绕过直连 API；`$GRAPE_SESSION_TOKEN` 自动注入，不探索其他 token。
2. **任务上下文先行**：执行前先读 `grape tasks current` + `grape issues detail`，明确已做/待做。
3. **仅评论不阻断**：敏感词命中仅随检视评论报告，不阻断 PR 合入、不作为门禁、不影响原审查结论。
4. **token 不写文件/日志/评论**；403 → 停止并回复「需管理员权限」，不换 token。

## Prerequisites（执行前必检，通过后才进入处理流程）

1. **grape CLI 版本满足（必须 ≥ 0.3.2）**：`grape --version`
   - 未装或版本 < 0.3.2 → 自行安装/升级：`pip install git+https://gitcode.com/grape-dev/grape-cli.git`，装好后再验证一次。
2. **刷新契约到最新**：`grape refresh`
3. **认证与运行时变量就绪**（只校验 A 类变量）：
   ```bash
   echo "token=${GRAPE_SESSION_TOKEN:+ok} owner=$REPO_OWNER repo=$REPO_NAME pr=${PR_NUMBER:-MISSING}"
   ```
4. **python3 就绪**（扫描实现依赖）：`python3 -V`

## 环境变量

> ⚠️ 分两类：**A 类**为检视流程必需（缺失则停止并说明，不降级）；**B 类**仅特定事件/场景注入，**不得假定其存在**，缺失按降级规则跳过、不中断检视。`GRAPE_WORK_BRANCH` 为开发（coding）任务注入，本技能不建 PR、**不使用**。

**A 类（检视流程必需）**

| 变量 | 语义 | 用途 |
|------|------|------|
| `GRAPE_SERVER_URL` / `GRAPE_SESSION_TOKEN` | 服务端地址 / 会话级 token（`tk_`） | `grape` 自动读取与认证 |
| `REPO_FULL_NAME` | `owner/repo` 仓库全名 | `--owner`/`--repo` 取参、A2A payload `repository` |
| `REPO_OWNER` | 仓库属主 | `--owner "$REPO_OWNER"` |
| `REPO_NAME` | 仓库名 | `--repo "$REPO_NAME"` |
| `PR_NUMBER` | 当前 PR 编号 | `--number "$PR_NUMBER"` |

**B 类（仅特定事件/场景注入）**

| 变量 | 注入条件 | 用途 / 缺失降级 |
|------|---------|----------------|
| `ISSUE_NUMBER` | 关联 Issue 的流水线任务 | 仅 `events detail` 读上下文；缺失或读不到时跳过，不写入 A2A payload |
| `ACCOUNT` | webhook 触发人存在时 | 仅合并后交棒 sre 的 A2A `account`；缺失则不填该字段 |
| `GRAPE_SCENE_ID` | 场景绑定任务 | 仅交棒前场景节点核验；缺失则以 `scenes detail` 实返结果判定，不假定节点存在 |
| `AGENT_NAME` / `BOT_USERNAME` | 按任务类型 | 评论前缀固定 `[QA Agent]`，命令不依赖二者 |

## 定位

PR 检视时对变更文件**新增行**做敏感词校验（内部域名词表 + IP 正则），命中则随检视评论报告命中位置（文件+行号），不影响原审查结论、不阻断合入。

- **任务边界**：只做「校验 + 评论报告」，任务以发布评论 + `grape tasks complete`（`next_step=done`）收尾；**检视流程内不交棒 sre**——sre 交办仅在 PR 合并验证（`pull_request_merged`）通过后触发（见 runbook Step 5）。
- **技能接线**：本技能为仓库级技能资产（`.grape/skills/qa/custom-pr-review/`），需经 sre 环节 `grape skills upload --scope repo` 注册到平台技能库后，方能被本仓库任务按 `agent_type=qa` 加载；落库与核验链路见 `docs/custom-pr-review/plan.md`「接线机制」。

## 评论规范

- PR 评论 body 必须以 `[QA Agent]` 开头；审查结论与敏感词段落**合并为同一条评论**（命中=0 时不附加敏感词段落）。
- 引用原文：`> {用户原文}\n\n[QA Agent] {回复}`

## 处理流程

> ⚠️ **先读 `references/pr-review-runbook.md`**：按走查表逐步骤执行（Step 0 前置+读上下文 → 1 拉 PR 变更文件 → 2 扫描敏感词 → 3 组装评论 → 4 发布检视评论 → 5 交棒规则（默认不交棒） → 6 收尾）。命令 runbook 已给出，**照抄执行，不要 `grape --help` / `grape commands` 逐条探索**。
>
> ⚠️ **收尾一律用 `grape tasks complete`**：调完即结束。

敏感词校验规则详见 `references/sensitive-scan-guide.md`（内置词表 / IP 正则 / 词表扩展 / diff 行号定位 / 评论格式）。

## 领域规则：敏感词校验

| 规则 | 说明 |
|------|------|
| 内置词表 | `huawei.com` `codehub` `myhuaweicloud.com`（子串匹配，大小写不敏感） |
| IP 正则 | IPv4 + IPv6（含公网/内网）；命中标注「疑似 IP」 |
| 词表扩展 | 仓库 `docs/sensitive-words.txt` 存在则逐行合并（`#` 注释、空行跳过） |
| 校验范围 | 仅 PR 变更文件新增行（`+` 开头且非 `+++`，含纯 `+` 空行），不查 PR 标题/描述 |
| 命中输出 / 动作 | `(文件路径, 行号, 命中词)` 三元组（行号 = diff 新文件行号）；命中>0 附加到检视评论末尾，命中=0 不附加 |

## 操作边界

| 行为 | 态度 |
|------|------|
| 按 runbook 走查表逐步执行 | ✅ **必须做** |
| 拉 PR diff 变更文件 + 扫描敏感词 | ✅ **必须做** |
| 命中时附加检视评论报告；未命中时不附加报告 | ✅ **必须做** |
| 词表经仓库文件扩展（合并） | ✅ **必须做** |
| 检视完成后评论 + `tasks complete`（`next_step=done`）收尾 | ✅ **必须做** |
| A2A 交棒 sre | ⚠️ **仅条件触发**（PR 合并验证 `pull_request_merged` 通过后），检视流程内禁止 |
| 阻断 PR 合入 / 作为门禁 | ❌ **禁止** |
| 校验 PR 标题/描述 | ❌ **禁止** |
| 编造不存在的命令/参数 | ❌ **禁止** |
| 裸 `git`（一律走 `grape git`） | ❌ **禁止** |

## 硬约束

- 敏感词命中仅评论报告，不阻断合入、不作为门禁、不改变原审查结论
- 校验范围仅 PR 变更文件新增行（`+` 开头且非 `+++`，含纯 `+` 空行）
- 词表内置可扩展（仓库 `docs/sensitive-words.txt` 逐行追加，去重）
- 评论 body 以 `[QA Agent]` 开头；结论与敏感词段落合并为同一条评论
- 检视流程内不交棒 sre；sre 交办仅 `pull_request_merged` 合并验证通过后触发

## 任务完成流程（必做）

1. **输出分析摘要**：PR 编号 + 变更文件数 + 命中数 + 评论发布结果
2. **调用 `grape tasks complete` 自报完成**：
   ```bash
   grape tasks complete --json '{
     "result_text": "结论：PR #'"${PR_NUMBER}"' 敏感词校验完成，命中 {hit_count} 处，已随检视评论报告（未阻断）。",
     "next_step": "done",
     "actions": ["拉取 PR diff 变更文件", "按内置词表+IP 正则扫描新增行", "组装并发布检视评论"],
     "conclusion": "PR #'"${PR_NUMBER}"' 敏感词校验完成，命中 {hit_count} 处，未阻断合入",
     "artifacts": ["PR #'"${PR_NUMBER}"' 检视评论"],
     "issue_summary": "敏感词校验完成 | PR #'"${PR_NUMBER}"' | 命中 {hit_count} 处 | 未阻断"
   }'
   ```
   - `{hit_count}` = runbook Step 2 赋值的 `$HIT_COUNT`（`SCAN_OUT` 行数，与 `HITS=` 对账）；命中=0 时填 0，仍照常收尾。

## 参考

- **执行走查表（先读）**：`references/pr-review-runbook.md`
- **敏感词校验指南**：`references/sensitive-scan-guide.md`
- CLI 命令基线：`references/grape-cli-reference.md`
