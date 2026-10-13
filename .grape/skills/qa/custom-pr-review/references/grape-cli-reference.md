# grape CLI 命令参考（custom-pr-review 专用）

> 来源：cli-code-analysis-spec.md（CLI 契约事实基线）+ 本轮契约核对（grape 0.3.3，`grape commands` 实列）
> 用途：本 skill 引用 grape 命令的唯一事实来源，不编造

---

## 1. 安装与就绪检查

- grape CLI 通常预装；未装/版本过低可自行安装：`pip install git+https://gitcode.com/grape-dev/grape-cli.git`
- 版本校验：`grape --version`（必须 ≥ 0.3.2）
- 契约刷新：`grape refresh`
- 扫描实现依赖解释器：`python3 -V`（grape CLI 为 Python 实现，通常已随环境提供；缺失时按 runbook 错误排查降级为 POSIX awk 参考实现）

## 2. 认证与配置

- `$GRAPE_SESSION_TOKEN` 自动注入，`grape` 自动读取，不需手动 export。
- Token 优先级：`--token` > `GRAPE_SESSION_TOKEN`(tk_) > `GRAPE_API_TOKEN`(grape_) > config.json。

## 3. 常用命令（本 skill 实际用到的）

### 任务上下文（共性）
| 命令 | 用途 | 变更性 |
|------|------|--------|
| `tasks current --json-out` | 当前任务 | 只读 |
| `issues detail --owner --repo --number --json-out` | Issue 全貌 | 只读 |
| `events detail --seq --json-out` | 前序 A2A payload | 只读 |

### PR 检视（qa 核心）
| 命令 | 用途 | 变更性 |
|------|------|--------|
| `git-repo pulls get --owner --repo --number --json-out` | PR 元信息 | 只读 |
| `git-repo pulls files --owner --repo --number --json-out` | PR 变更文件清单（含 patch/diff） | 只读 |
| `git-repo pulls comments list --owner --repo --number --json-out` | 已有评论 | 只读 |
| `git-repo issues comments create --owner --repo --number --json '{"body":"..."}'` | 发布 PR 级检视评论（GitHub 上 PR 与 Issue 同号；含敏感词报告） | **变更** |
| `git-repo pulls comments create --owner --repo --number --json '{"body":"..."}'` | 行级评审评论端点（GitHub `POST /pulls/{n}/comments`），仅传 body 返回 422（需 `commit_id`/`path`/`position`）→ **本技能不使用** | **变更** |
| `git-repo contents --owner --repo --path --raw` | 读取文件内容/词表文件 | 只读 |
| `git-repo tree --owner --repo --depth single --json-out` | 仓库目录结构 | 只读 |

### 任务完成与交接
| 命令 | 用途 | 变更性 |
|------|------|--------|
| `tasks complete --json '{"result_text":"...","next_step":"done","actions":[],"conclusion":"","artifacts":[],"issue_summary":""}'` | 自报完成（本技能默认收尾，`next_step=done`） | **变更** |
| `a2a handoff --json '{"agent":"sre","payload":{...}}'` | A2A 交棒 sre（**仅 `pull_request_merged` 合并验证通过后条件触发**，检视流程内不使用） | **变更** |

### 内置命令
| 命令 | 用途 |
|------|------|
| `grape refresh` | 刷新契约缓存 |
| `grape commands [pattern]` | 列出可调用命令 |
| `grape git <args>` | 本地 git 透传 + 凭据注入 |

## 4. 命令限制与不存在的能力

- **命令遮蔽**：`tasks status get` 不可执行，用 `tasks wait --task-id`。
- **无 grape api 逃生舱**：v0.2.2 移除，契约未覆盖端点无法 CLI 调用。
- **PR 评论无更新能力**：`grape git-repo pulls comments` 仅 `create`/`list`（PATCH/DELETE 不存在）→ 评论内容必须一次组装完整，不做原地追加。
- **不存在能力**：仓库创建/删除 · 任务删除 · 知识上传 · Issue/PR 删除 · PR 评论更新/删除 · 分支创建/删除 · Token 管理 · `tasks create`（已移除）· 流式输出 · 交互式提示。

## 5. 退出码

`0` 成功 · `1` 通用/配置错误 · `2` click 用法错误 · `≥400` HTTP 透传 · `130` Ctrl+C · git 退出码透传。

## 6. 安全与边界

- token 使用范围：只用 `$GRAPE_SESSION_TOKEN`（`tk_`）；`user_only` 端点（auth/skills）对 `tk_` 返回 403 需 `grape_`/JWT。
- 禁止访问 `data/`、`configs/`、`sessions.db` 等 Grape 内部数据。
- 403 → 停止并回复「需管理员/维护者权限」；401 → 说明凭据已失效，不换 token。
- token 不写文件/日志/评论。
