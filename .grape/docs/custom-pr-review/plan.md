# 方案/计划 — custom-pr-review v2.0.0（多门禁检视）

> v1.1.0（敏感词校验）已合并 PR #5 并注册为仓库级 skill（skill_id=2972）。
> v2.0.0 扩展需求来源：Issue #3 评论 6183893873（Chat Agent 门禁扩展需求，用户已确认）。
> 扩展范围：7 类门禁（代码质量/编译构建/依赖漏洞/License 合规/密钥泄露/敏感词/文档规范）+ 分级策略（阻断级 ❌ / 警告级 ⚠️）。

## 一、方案选型核实记录（官方技能定制可行性）

本轮按 spec §Risks:59 要求先做「技能资产 + 定制机制」定位核实，证据与结论如下（均为可复现命令）：

| # | 核实内容 | 命令 | 结果 |
|---|---------|------|------|
| 1 | 本仓库是否含平台审查技能资产 | `grape git ls-tree -r --name-only origin/main` | 无 `skills/` 目录（本仓库为 CloudRobo 样例仓库，仅有 `docs/`、`samples/`、`tools/`） |
| 2 | PR 订阅 QA 审查所用技能归属 | `grape skills list --json-out` | 为**官方技能** `qa-cli`（id 2756，`scope=official`，`agent_type=qa`，display「PR 审查与工作流质量审查（CLI 版）」）；另有 `qa`(2590)/`pr`(2589)/`grape-skill-validator`(2789) |
| 3 | 官方技能内容可读性/可改性 | `grape skills detail --skill_id 2756 --json-out` | 内容可读（`content` 返回 SKILL.md），但资产位于平台官方技能库，不在本仓库 |
| 4 | CLI 是否提供技能编辑/更新能力 | `grape commands skills` | 仅 `detail` / `list` / `package sub get` / `sub get` / `sub list` / `upload`——**无技能编辑、覆盖、更新命令** |
| 5 | `skills upload` 能力边界 | `grape skills upload --help` | 仅 `--scope user|repo`（`repo` 需 `--repo`），**无修改 `scope=official` 技能的能力** |
| 6 | 当前仓库级技能现状 | `grape skills list --scope repo --agent_type qa --json-out` | 0 条（本仓库尚未注册仓库级技能） |

**结论**：spec 主路径（在官方 PR 订阅 QA 审查技能 `qa-cli` 内插入敏感词校验环节）在本仓库与当前 CLI 契约（grape 0.3.3）下不可行——官方技能资产不在本仓库、CLI 无技能编辑能力、`tk_` 会话凭据亦无官方技能写入口。故依 spec §Risks:59 预案回退为**仓库内 overlay 方案**：以 `.grape/skills/qa/custom-pr-review/` 交付可注册的仓库级技能资产。

**需需求方确认项（bounded deviation）**：spec §Scope:22「不新建独立技能」与 §Risks:59「回退仓库内 overlay」存在口径张力。本方案按 Risks 预案执行，并保留**整目录可撤销性**（本 PR 仅新增 `.grape/skills/qa/custom-pr-review/` 与 `.grape/docs/custom-pr-review/`，回滚即撤销该目录、不影响仓库既有文件）。该偏离已随本 PR 评论同步需求方，待其在 Issue #3/#5 确认 spec 修订；若需求方坚持改造官方技能，则该项须由平台侧实现（本仓库不可达），本 PR 可整体撤销。

## 二、接线机制（技能如何进入 PR 订阅审查链路）

仓库内技能**不会自动生效**，须经「注册」与「节点加载」两步；本仓库只能覆盖第一步的产物与说明：

1. **资产交付（本 PR）**：`.grape/skills/qa/custom-pr-review/`（SKILL.md + 3 个 references），`agent_type=qa`、`name=custom-pr-review`。
2. **注册为仓库级技能**（流水线 sre 环节，官方技能 `grape-skill-uploader` id 2790，场景 `grape-skill-dev`）：QA 审查通过后预注册；PR 合并后正式上传——
   ```bash
   grape skills upload --file <custom-pr-review.zip> --scope repo --repo "$REPO_FULL_NAME" --agent_type qa
   ```
   （`scope=repo` 才会成为本仓库可见、可被本仓库任务加载的技能；不传 `--repo` 则落为 `scope=user`，对仓库任务不可见。）
3. **注册核验**：
   ```bash
   grape skills list --repo "$REPO_FULL_NAME" --agent_type qa --json-out   # 注册成功后本技能以 scope=repo 出现在列表中
   ```
4. **生效前提（平台侧，本仓库无配置入口）**：PR 订阅审查的 qa 节点需加载/选择本技能。本仓库无场景/订阅配置文件，此项属平台侧配置，**标注为待平台侧确认项，不在本 PR 内断言已接通**。

**能力现状（不夸大）**：在完成第 2、3、4 步前，敏感词校验**不会**随 PR 订阅审查自动执行；本 PR 交付的是技能资产本体 + 注册/核验链路说明。第 4 步的节点加载如不可行，本技能仍可按 `agent_type=qa` 被显式调用的任务加载。

## 三、v2.0.0 实现顺序

1. **SKILL.md** v2.0.0：更新 frontmatter（version/description/tools）；安全硬约束增分级报告+仅评论不阻断；Prerequisites 增可选工具探测；领域规则表扩展为 7 类门禁+级别；操作边界/硬约束/任务完成流程同步更新。≤130 行。
2. **pr-review-runbook.md**：Step 2 扩展为统一扫描脚本（License/密钥/敏感词 python3 内嵌）+ Step 2b 工具依赖门禁（ruff/black/clang-format/py_compile/pip-audit/mdl/yamllint）；Step 3 评论组装按分级（阻断 ❌ / 警告 ⚠️ / 敏感词）。≤250 行。
3. **gate-checks-guide.md**（新增）：7 类门禁检测规则/正则/分级标准/降级策略/评论格式/测试覆盖。≤250 行。
4. **sensitive-scan-guide.md**：沿用 v1（敏感词规则不变）。
5. **grape-cli-reference.md**：沿用 v1（命令集不变）。
6. **docs/**：spec.md/plan.md/task.md 更新为 v2.0.0。

## 四、v2.0.0 依赖关系

- 7 类门禁扫描被检视评论组装调用，不改既有模块间依赖。
- 依赖 grape CLI：`git-repo pulls files`（取 diff）、`issues comments create`（发评论）、`contents --raw`（取词表）、`tasks complete`。
- 运行时依赖 `python3`（扫描实现）；可选工具（ruff/black/clang-format/clang-tidy/pip-audit/mdl/yamllint）缺失时降级为 SKIP+备注。
- 注册链路依赖平台侧 `grape skills upload`（scope=repo）；v2.0.0 需重新上传覆盖 v1.1.0。

## 五、v2.0.0 风险

| 风险 | 缓解 |
|------|------|
| 外部工具未安装 → 门禁跳过 | 降级为 SKIP+备注，不中断检视；建议仓库配 .pre-commit-config.yaml |
| 密钥正则误报 | 报告标注命中类型，审查者判断 |
| License is→isno 检测遗漏 | 正则匹配 is/isno 共现模式；审查者人工复核 |
| ci.yml 占位未替换 | 超出技能目录范围，需单独实施（PR 描述标注） |
| 大 diff 性能 | 仅扫变更文件新增行，逐文件流式处理 |
