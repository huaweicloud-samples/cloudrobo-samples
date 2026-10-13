# 方案/计划 — custom-pr-review（敏感词校验）

> 需求锚点：`spec.md`（Issue #3 交付）—— In Scope「在现有 PR 订阅 QA 审查中新增敏感词校验环节」（§Scope:14）、Out of Scope「不新建独立技能（用户明确拒绝）」（§Scope:22）、Risks 预案「官方技能不可 fork/定制入口未开放 → coding 阶段先定位技能资产与定制机制，不可行时回退为仓库内 overlay/包装方案」（§Risks:59）。

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

## 三、实现顺序

1. **SKILL.md**：技能定位（含「检视流程内不交棒 sre」的任务边界与技能接线说明）、安全约束、环境变量（A/B 两类）、处理流程引用、领域规则（敏感词校验规则表）、操作边界、硬约束、任务完成流程。
2. **references/pr-review-runbook.md**：执行走查表（Step 0-6），每步给出确切 grape CLI 命令；Step 2 为可直接执行的 python3 扫描实现。
3. **references/sensitive-scan-guide.md**：敏感词校验指南（内置词表、IP 正则、词表扩展、diff 行号定位与 POSIX awk 参考实现、评论格式、测试覆盖要求）。
4. **references/grape-cli-reference.md**：CLI 命令参考（qa 检视相关命令子集 + 不存在能力声明）。
5. **docs/ 开发过程文档**：spec.md（来源 issue agent）、plan.md（本文档）、task.md（任务拆解）。

## 四、依赖关系

- 敏感词校验被检视评论组装调用（新增调用），不改既有模块间依赖。
- 依赖 grape CLI：`git-repo pulls files`（取 diff）、`git-repo pulls comments create`（发评论）、`git-repo contents --raw`（取词表）、`tasks complete`（收尾）。
- 运行时依赖 `python3`（扫描实现）；缺失时按 runbook 错误排查降级为 guide §4 的 POSIX awk 参考实现。
- 注册链路依赖平台侧 `grape skills upload`（scope=repo）与 `grape-skill-uploader` 技能（见 §二）。

## 五、风险

| 风险 | 缓解 |
|------|------|
| 官方技能定制入口不可用（已发生） | 按 spec §Risks:59 回退仓库内 overlay，并保留整目录可撤销性；偏离项随 PR 评论同步需求方 |
| 仓库级技能注册/节点加载未完成 → 校验不自动执行 | §二 明确「注册 + 加载」两步与核验命令，并标注平台侧待确认项；不承诺自动生效 |
| IP 正则误报版本号 | 报告标注「疑似 IP」，审查者自行判断 |
| 技能自身示例造成自引用命中 | guide §1 声明为预期噪音，按规则原样报告，不阻断 |
| 大 diff 性能 | 仅扫变更文件新增行，逐文件流式处理 |
| 仓库无自定义词表 | 仅用内置词表，不报错 |
