# custom-pr-review 执行走查表（pr-review-runbook）

> 目的：把 PR 检视敏感词校验的每一步固化下来，Agent 照着执行。
> 环境变量（A 类，检视流程必需）：`$REPO_OWNER`（owner）、`$REPO_NAME`（repo 名）、`$PR_NUMBER`（PR 编号）、`$GRAPE_SESSION_TOKEN`（grape 自动读取）；B 类（`$ISSUE_NUMBER`/`$ACCOUNT`/`$GRAPE_SCENE_ID`）仅在特定事件/场景注入，缺失即跳过（见 SKILL.md 环境变量）。
> ⚠️ **评论前缀硬规**：所有 PR 评论 body 一律以 `[QA Agent]` 开头。

---

## Step 0 · 前置 + 读任务上下文（必须最先）
```bash
grape --version           # ≥ 0.3.2
grape refresh
echo "token=${GRAPE_SESSION_TOKEN:+ok} owner=$REPO_OWNER repo=$REPO_NAME pr=${PR_NUMBER:-MISSING} issue=${ISSUE_NUMBER:-absent}"
python3 -V                # 扫描实现依赖

grape tasks current --json-out
[ -n "${ISSUE_NUMBER:-}" ] && grape issues detail --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$ISSUE_NUMBER" --json-out || echo "ISSUE_NUMBER 未注入/非本仓库真实 Issue → 跳过 issues detail（不阻断检视）"
```
- `PR_NUMBER` 缺失 → 先查 `grape tasks current` / `grape events detail --seq {seq} --json-out` 的 payload（`{seq}` = `tasks current` 返回的当前事件 seq）→ PR_NUMBER；仍缺失 → 评论说明并收尾（`next_step=reply_comment`）。
- ⚠️ `ISSUE_NUMBER` 在 PR 订阅审查运行时可能是内部链路 ID（`issues detail` 返回 422）：**不得**把它原样写入 A2A payload；仅作上下文读取，读不到就跳过。

### Step 1 · 拉 PR 变更文件（必做）
```bash
# PR 元信息（确认状态/分支；head.ref 用于追溯，本技能不推送代码）
grape git-repo pulls get --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" --json-out

# PR 变更文件清单（文件名 + patch/diff 内容）
grape git-repo pulls files --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" --json-out
```
- `pulls files` 返回数组，每项含 `filename` + `patch`（unified diff，无文件头）。
- 行号口径：`@@ -a,b +c,d @@` 的 `c` 为新文件起始行号；`+` 开头且非 `+++` 为新增行（**纯 `+` 空行也算新增行**）；空格开头为上下文行；`-` 开头不改变新行号。详见 `references/sensitive-scan-guide.md` §4。

### Step 2 · 扫描敏感词（必做）

> 规则详见 `references/sensitive-scan-guide.md`；下面给出**可直接执行**的实现（python3，无 jq 依赖，变量经 argv/管道传入而非单引号内插值）。

```bash
# ① 取 PR 变更文件 JSON（变量：json 交给下面的脚本管道读取）
PR_FILES_JSON=$(grape git-repo pulls files --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" --json-out)

# ② 取仓库自定义词表（统一走 grape git-repo contents --raw；404 时为空，仅用内置词表）
CUSTOM_WORDS_RAW=$(grape git-repo contents --owner "$REPO_OWNER" --repo "$REPO_NAME" --path "docs/sensitive-words.txt" --raw 2>/dev/null || true)

# ③ 扫描：stdout=每行 `文件:行号:命中词`；stderr=HITS=<命中总数>
SCAN_OUT=$(printf '%s' "$PR_FILES_JSON" | python3 -c '
import json, re, sys

BUILTIN = ["huawei.com", "codehub", "myhuaweicloud.com"]
IPV4 = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b")
EXTRA = sys.argv[1] if len(sys.argv) > 1 else ""

def load_words(extra):
    words = list(BUILTIN)
    for line in (extra or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    seen, uniq = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower()); uniq.append(w)
    return uniq

payload = json.load(sys.stdin)
files = payload if isinstance(payload, list) else (payload.get("files") or [])
words = load_words(EXTRA)
rows, seen = [], set()
for f in files:
    patch = f.get("patch") or ""
    if not patch:
        continue
    path = f.get("filename") or f.get("new_path") or "?"
    lineno = None
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+++") or raw.startswith("---"):
            continue
        elif raw.startswith("+"):
            text = raw[1:]
            low = text.lower()
            hits = [w for w in words if w.lower() in low]
            hits += [m.group(0) for m in list(IPV4.finditer(text)) + list(IPV6.finditer(text))]
            for w in hits:
                if (path, lineno, w) not in seen:
                    seen.add((path, lineno, w)); rows.append((path, lineno, w))
            lineno += 1
        elif raw.startswith(" "):
            lineno += 1
for path, lineno, word in rows:
    print("%s:%s:%s" % (path, lineno, word))
print("HITS=%d" % len(rows), file=sys.stderr)
' "$CUSTOM_WORDS_RAW")

echo "$SCAN_OUT"                       # 命中明细：文件:行号:命中词
HIT_COUNT=$(printf '%s\n' "$SCAN_OUT" | grep -c ':' || true)   # 存入变量：Step 5/6 引用；|| true 防零命中时 grep 退出码 1 中断（set -e）
echo "HIT_COUNT=$HIT_COUNT"           # 与脚本 stderr 的 HITS= 对账（两者必须相等，不等则以 HITS= 复核）
```
- ⚠️ **禁止写成 `python3 - <<PY ... PY` 并同时用管道喂 JSON**：heredoc 会覆盖 stdin，脚本读到空输入（历史缺陷）；本步固定用 `python3 -c '...'` + 管道。
- 命中数按 `(文件, 行号, 命中词)` 去重；词表子串匹配大小写不敏感；词表项与 IP 均可能重复命中同一行，去重后即为报告行。
- 无 python3 环境的降级：按 `sensitive-scan-guide.md` §4 的 POSIX awk 参考实现逐 patch 编号，再用 `grep -inF` 逐词命中；两实现行号必须一致，不一致时以 guide §4 口径复核。

### Step 3 · 组装评论（必做）

占位符定义（**不留未定义占位符**）：

| 占位符 | 来源（取值方式） | 缺失/例外 |
|--------|-----------------|----------|
| `{review_conclusion}` | 本技能本次对 PR 的审查结论段落（Agent 依变更文件审查结果生成：配置检查 / 变更摘要 / 审查结论三小节）；**同一条评论内先结论、后敏感词段落** | 无法生成时只发敏感词段落并注明「审查结论未生成」 |
| `{hit_count}` | Step 2 赋值的 `$HIT_COUNT`（`SCAN_OUT` 行数，与脚本 stderr `HITS=` 对账后使用） | 命中=0 → 不输出敏感词段落 |
| `{hit_rows}` | Step 2 每行 `文件:行号:命中词` 转 Markdown 表格行（`| {file} | {line} | {word} |`） | 命中=0 → 不输出 |
| `{file}` / `{line}` / `{word}` | 上一条表格行内的字段槽位，逐条替换为 Step 2 输出行的文件名 / 行号 / 命中词 | — |

组装规则：
- **命中数 = 0** → 评论 body = `[QA Agent]` + `{review_conclusion}`，**不含**敏感词段落。
- **命中数 > 0** → 评论 body = `[QA Agent]` + `{review_conclusion}` + 下方敏感词段落（附在结论末尾）。
- ⚠️ `grape git-repo pulls comments` 契约只有 `create`/`list`（**无更新能力**）→ 不做「原地追加到既有评论」；结论与敏感词段落**在同一条新评论内**一次性发布，语义与 spec「命中>0 时附加到检视评论末尾」一致。

命中>0 时附加的段落（Markdown 模板）：
```markdown
---
### 敏感词校验结果

| 文件 | 行号 | 命中词 |
|------|------|--------|
{hit_rows}

> ⚠️ 以上为疑似敏感信息命中（含规则示例自引用），仅提示不阻断合入；命中 {hit_count} 处。
```

### Step 4 · 发布检视评论（必做）
```bash
# 发布 PR 级检视评论：用 issues comments 端点（GitHub 上 PR 与 Issue 同号）
# body 组装示例（命中>0；命中=0 时去掉敏感词段落；换行用 \n 转义）
grape git-repo issues comments create --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" \
  --json '{"body":"[QA Agent] <review_conclusion 正文>\n\n---\n### 敏感词校验结果\n\n| 文件 | 行号 | 命中词 |\n|------|------|--------|\n| <file> | <line> | <word> |\n\n> ⚠️ 以上为疑似敏感信息命中（含规则示例自引用），仅提示不阻断合入；命中 <hit_count> 处。"}'
```
- ⚠️ `git-repo pulls comments create` 映射的是**行级评审评论**端点（GitHub `POST /pulls/{n}/comments`），仅传 body 实测返回 `422`（缺 `commit_id`/`path`/`position`）→ 本技能发布 PR 级总结评论一律用 `issues comments create`。
- 发布后核对返回的评论 id/URL，确认评论已落地（失败重试 ≤3 次，仍失败 → 评论/收尾如实说明）。

### Step 5 · 交棒规则（默认不交棒；sre 仅条件触发）
> ⚠️ 本技能输出为提示性校验（不阻断、无「通过/不通过」语义），**检视流程内不交棒 sre**：sre 交办仅应在 PR 合并验证（`pull_request_merged`）通过后触发。下方 `${HIT_COUNT}` 为 Step 2 赋值的命中总数。

```bash
# 条件判定：仅合并事件且合并验证通过才交棒，其余一律不交棒
echo "event=${DEVOPS_EVENT_TYPE:-unknown} scene_id=${GRAPE_SCENE_ID:+set} pr_state=$(grape git-repo pulls get --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" --json-out | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("merged") or d.get("state") or "unknown")')"
```
- 非 `pull_request_merged`（PR 未合并/审查未通过/仅命中敏感词）→ **不交棒**，直接进 Step 6（`next_step=done`）。
- 仅当事件为 `pull_request_merged` 且 PR 状态为合并通过时，才按下方执行：
```bash
SCENE_NODES="$(grape scenes detail --scene_id "$GRAPE_SCENE_ID" --json-out 2>/dev/null)"
echo "scene_agents: $(echo "$SCENE_NODES" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(",".join(sorted({(n.get("tag") or n.get("skill") or "") for n in d.get("nodes") or []})))' 2>/dev/null)"
grape a2a handoff --json '{
  "agent": "sre",
  "payload": {
    "repository": "'"$REPO_FULL_NAME"'",
    "issue_number": '"${ISSUE_NUMBER:?合并事件下 ISSUE_NUMBER 必填，缺失则不交棒}"',
    "pr_number": '"$PR_NUMBER"',
    "context": "PR #'"$PR_NUMBER"' 已合并，检视敏感词校验结果已报告（命中 '"${HIT_COUNT:-0}"' 处），请执行发布/部署。"
  }
}' --json-out
```
- 场景未注入 / `scenes detail` 无 `sre` 节点 → 不交棒，评论说明并 `next_step=done`（禁止用「平台返回 skipped」等疑似 bug 措辞）。
- A2A 失败重试 ≤3 次；仍失败 → 评论说明需人工介入。

### Step 6 · 任务完成流程（必做）
> `${HIT_COUNT}` 来自 Step 2 的赋值（`grep -c ':' || true`），发布评论前必须已完成 Step 2；`:-0` 兜底仅防变量意外为空，正常执行下不会触发。

```bash
grape tasks complete --json '{
  "result_text": "结论：PR #'"$PR_NUMBER"' 敏感词校验完成，命中 '"${HIT_COUNT:-0}"' 处，已随检视评论报告（未阻断）。",
  "next_step": "done",
  "actions": ["拉取 PR diff 变更文件", "按内置词表+IP 正则扫描新增行", "组装并发布检视评论"],
  "conclusion": "PR #'"$PR_NUMBER"' 敏感词校验完成，命中 '"${HIT_COUNT:-0}"' 处，未阻断合入",
  "artifacts": ["PR #'"$PR_NUMBER"' 检视评论"],
  "issue_summary": "敏感词校验完成 | PR #'"$PR_NUMBER"' | 命中 '"${HIT_COUNT:-0}"' 处 | 未阻断"
}'
```
> `already:true` 忽略。然后结束任务，不要继续等待输入。

---

## 错误排查速查

| 现象 | 处理 |
|------|------|
| `PR_NUMBER` 未注入 | 从 `tasks current` / `events detail` payload 解析；仍缺失 → 评论说明 + `next_step=reply_comment` |
| `ISSUE_NUMBER` 未注入或 `issues detail` 422 | 正常（PR 订阅运行时可能非真实 Issue 编号）→ 跳过上下文读取，不写入 A2A payload |
| `pulls files` 返回空数组 | PR 无变更文件 → 命中=0，评论不含敏感词段落 |
| `pulls files` 5xx | 直接重试 `pulls files` ≤3 次（间隔递增）；仍失败用 `grape git ls-remote` 核对 |
| `contents` 404（词表不存在） | 正常 → 仅用内置词表继续扫描 |
| 脚本报 `JSON 解析失败`/`Expecting value` | `PR_FILES_JSON` 为空或被日志污染 → 重拉 `pulls files --json-out`；确认是 `--json-out` 而非文本输出 |
| `pulls comments create` 返回 422 | 该命令为行级评审评论端点，缺 `commit_id`/`path`/`position` → 改用 `issues comments create --number "$PR_NUMBER"` 发布 PR 级评论 |
| 管道喂入但脚本读到空 stdin | 误用了 `python3 - <<PY` heredoc 形式（heredoc 覆盖管道）→ 改用本 Step 2 的 `python3 -c` 形式 |
| `python3: command not found` | 按 `sensitive-scan-guide.md` §4 的 POSIX awk 参考实现降级扫描；两实现行号不一致时以 guide §4 口径复核 |
| IP 误报版本号 | 报告标注「疑似 IP」；版本号模式（如 `1.2.3.4`）仍报告，由审查者判断 |

## 明确禁止

- ❌ 阻断 PR 合入 / 作为门禁。
- ❌ 校验 PR 标题/描述。
- ❌ 在 PR 未合并、审查未通过时交棒 sre（交棒仅 `pull_request_merged` 合并验证通过后触发）。
- ❌ 把 `ISSUE_NUMBER`（内部链路 ID）原样写入 A2A payload。
- ❌ 编造命令/参数（命令只引事实基线）。
- ❌ 裸 `git`（一律走 `grape git`）。
- ❌ 跳过最终 Step（`tasks complete`）。
