---
name: custom-pr-review
version: 2.0.1
description: |
  PR 多门禁检视技能。对 PR 变更文件执行 7 类门禁检查（代码质量/编译构建/依赖漏洞/License 合规/密钥泄露/敏感词/文档规范），
  阻断级问题标注 ❌、警告级标注 ⚠️，全部结果随检视评论报告。
  ①拉取 PR diff → ②逐项门禁扫描 → ③按分级组装评论 → ④发布检视评论。
tools:
  - bash
  - grape
  - python3
---

## 安全硬约束（必须遵守）

1. **CLI 优先**：凡 grape CLI 能做的走 CLI，禁止绕过直连 API；`$GRAPE_SESSION_TOKEN` 自动注入，不探索其他 token。
2. **任务上下文先行**：执行前先读 `grape tasks current` + `grape issues detail`，明确已做/待做。
3. **分级报告**：阻断级（License 改坏、密钥泄露、编译失败）标注 ❌ 须修复；License 缺失仅提示不阻断；警告级（代码风格、文档规范）标注 ⚠️ 仅提示；敏感词维持仅提示不阻断。
4. **仅评论报告**：门禁结果随检视评论报告，不直接阻断合入（合入阻断由 GitHub Actions required checks 实现，非本技能职责）。
5. **token 不写文件/日志/评论**；403 → 停止并回复「需管理员权限」，不换 token。

## Prerequisites（执行前必检，通过后才进入处理流程）

1. **grape CLI 版本满足（必须 ≥ 0.3.2）**：`grape --version`
   - 未装或版本 < 0.3.2 → 自行安装/升级：`pip install git+https://gitcode.com/grape-dev/grape-cli.git`
2. **刷新契约到最新**：`grape refresh`
3. **认证与运行时变量就绪**（只校验 A 类变量）：
   ```bash
   echo "token=${GRAPE_SESSION_TOKEN:+ok} owner=$REPO_OWNER repo=$REPO_NAME pr=${PR_NUMBER:-MISSING}"
   ```
4. **python3 就绪**（扫描实现依赖）：`python3 -V`
5. **可选工具探测**（缺失时对应门禁降级为跳过+备注，不中断检视）：
   ```bash
   for t in ruff black clang-format clang-tidy pip-audit mdl yamllint; do command -v "$t" >/dev/null 2>&1 && echo "$t=ok" || echo "$t=MISSING"; done
   ```

## 环境变量

> ⚠️ **A 类**为检视必需（缺失停止）；**B 类**仅特定事件注入，缺失按降级跳过。

**A 类（检视流程必需）**

| 变量 | 语义 | 用途 |
|------|------|------|
| `GRAPE_SERVER_URL` / `GRAPE_SESSION_TOKEN` | 服务端地址 / 会话级 token（`tk_`） | `grape` 自动读取与认证 |
| `REPO_FULL_NAME` | `owner/repo` 仓库全名 | `--owner`/`--repo` 取参、A2A payload |
| `REPO_OWNER` | 仓库属主 | `--owner "$REPO_OWNER"` |
| `REPO_NAME` | 仓库名 | `--repo "$REPO_NAME"` |
| `PR_NUMBER` | 当前 PR 编号 | `--number "$PR_NUMBER"` |

**B 类**（`ISSUE_NUMBER`/`ACCOUNT`/`GRAPE_SCENE_ID`/`AGENT_NAME`）：仅特定事件注入，缺失按降级跳过（详见 runbook Step 0）。

## 定位

PR 检视时对变更文件执行多门禁检查（代码质量/编译构建/依赖漏洞/License 合规/密钥泄露/敏感词/文档规范），按阻断级/警告级分级报告，全部结果随检视评论发布。

- **任务边界**：只做「检查 + 评论报告」，以 `grape tasks complete`（`next_step=done`）收尾；检视流程内不交棒 sre；仓库级技能资产，需 sre 环节 `grape skills upload --scope repo` 注册后方能加载。

## 评论规范

- PR 评论 body 必须以 `[QA Agent]` 开头；所有门禁结果**合并为同一条评论**。
- 评论结构：审查结论 → 阻断级问题（❌）→ 警告级问题（⚠️）→ 敏感词校验（命中>0 时附加）。
- 引用原文：`> {用户原文}\n\n[QA Agent] {回复}`

## 处理流程

> ⚠️ **先读 `references/pr-review-runbook.md`**：按走查表逐步执行（Step 0 前置 → 1 拉 PR diff → 2 执行 7 类门禁扫描 → 3 按分级组装评论 → 4 发布检视评论 → 5 交棒规则 → 6 收尾）。
>
> ⚠️ **收尾一律用 `grape tasks complete`**。

门禁检查规则详见 `references/gate-checks-guide.md`（各门禁检测规则/分级标准/降级策略）。
敏感词校验规则详见 `references/sensitive-scan-guide.md`（内置词表 / IP 正则 / diff 行号定位）。

## 领域规则：门禁检查

| 门禁 | 检测内容 | 级别 |
|------|---------|------|
| 代码质量 | Python: ruff+black；C++: clang-format+clang-tidy | ⚠️ 警告 |
| 编译构建 | C++ ROS2 包构建；Python 编译/导入检查 | ❌ 阻断 |
| 依赖漏洞 | pip-audit（Python 依赖 CVE） | ⚠️ 警告 |
| License 合规 | 存在时被改坏（如 is→isno）/不兼容许可；缺失仅提示 | ❌ 阻断 |
| 密钥泄露 | 密码/私钥/AK-SK/token 正则检测 | ❌ 阻断 |
| 敏感词 | 内部域名+IP（沿用现有规则） | ⚠️ 警告 |
| 文档规范 | Markdown lint + YAML 校验 | ⚠️ 警告 |

- 工具缺失时对应门禁降级为「跳过+备注」，不中断检视。
- 阻断级问题标注 ❌ 须修复；警告级标注 ⚠️ 仅提示。

## 操作边界

| 行为 | 态度 |
|------|------|
| 按 runbook 逐项执行 7 类门禁检查 | ✅ **必须做** |
| 按分级组装评论（阻断 ❌ / 警告 ⚠️ / 敏感词） | ✅ **必须做** |
| 工具缺失时降级跳过并备注 | ✅ **必须做** |
| 检视完成后评论 + `tasks complete`（`next_step=done`）收尾 | ✅ **必须做** |
| A2A 交棒 sre | ⚠️ **仅条件触发**（PR 合并后） |
| 直接阻断 PR 合入（由 GitHub Actions 实现） | ❌ **禁止** |
| 校验 PR 标题/描述 | ❌ **禁止** |
| 编造命令/参数；裸 `git` | ❌ **禁止** |

## 硬约束

- 7 类门禁全部执行（工具缺失时降级跳过+备注，不省略不隐瞒）
- 阻断级（License/密钥/编译）标注 ❌；警告级（代码风格/文档/依赖/敏感词）标注 ⚠️
- 敏感词校验维持仅提示不阻断（沿用 v1 规则）
- 评论 body 以 `[QA Agent]` 开头；所有结果合并为同一条评论
- 检视流程内不交棒 sre

## 任务完成流程（必做）

1. **输出分析摘要**：PR 编号 + 各门禁结果 + 阻断/警告计数
2. **调用 `grape tasks complete` 自报完成**：
   ```bash
   grape tasks complete --json '{
     "result_text": "结论：PR #'"$PR_NUMBER"' 门禁检视完成，阻断 '"${BLOCK_COUNT:-0}"' 项，警告 '"${WARN_COUNT:-0}"' 项，敏感词命中 '"${HIT_COUNT:-0}"' 处。",
     "next_step": "done",
     "actions": ["拉取 PR diff", "执行 7 类门禁扫描", "按分级组装并发布检视评论"],
     "conclusion": "PR #'"$PR_NUMBER"' 门禁检视完成，阻断 '"${BLOCK_COUNT:-0}"' 项，警告 '"${WARN_COUNT:-0}"' 项",
     "artifacts": ["PR #'"$PR_NUMBER"' 检视评论"],
     "issue_summary": "门禁检视完成 | PR #'"$PR_NUMBER"' | 阻断 '"${BLOCK_COUNT:-0}"' | 警告 '"${WARN_COUNT:-0}"' | 敏感词 '"${HIT_COUNT:-0}"'"
   }'
   ```

## 参考

- **执行走查表（先读）**：`references/pr-review-runbook.md`
- **门禁检查指南**：`references/gate-checks-guide.md`
- **敏感词校验指南**：`references/sensitive-scan-guide.md`
- CLI 命令基线：`references/grape-cli-reference.md`
