# custom-pr-review 执行走查表（pr-review-runbook）

> 目的：把 PR 检视敏感词校验的每一步固化下来，Agent 照着执行。
> 环境变量：`$REPO_OWNER`（owner）、`$REPO_NAME`（repo 名）、`$PR_NUMBER`（PR 编号）、`$ISSUE_NUMBER`、`$GRAPE_WORK_BRANCH`、`$BOT_USERNAME`、`$GRAPE_SESSION_TOKEN`（grape 自动读取）。
> ⚠️ **评论前缀硬规**：所有 PR 评论 body 一律以 `[QA Agent]` 开头。

---

## Step 0 · 前置 + 读任务上下文（必须最先）
```bash
grape --version           # ≥ 0.3.2
grape refresh
echo "token=${GRAPE_SESSION_TOKEN:+ok} owner=$REPO_OWNER repo=$REPO_NAME pr=${PR_NUMBER:-MISSING}"

grape tasks current --json-out
grape issues detail --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$ISSUE_NUMBER" --json-out
grape events detail --seq {seq} --json-out   # 读前序 A2A payload
```
- 从 payload 确认 `PR_NUMBER`；缺失 → 评论说明并收尾（next_step=reply_comment）。

### Step 1 · 拉 PR 变更文件（必做）
```bash
# PR 元信息（确认状态/分支）
grape git-repo pulls get --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" --json-out

# PR 变更文件清单（文件名 + patch/diff 内容）
grape git-repo pulls files --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" --json-out
```
- `pulls files` 返回每个文件的 `filename` + `patch`（unified diff 格式）。
- 提取 `patch` 中以 `+` 开头的行（新增行），记录其在文件中的行号（patch 中 `@@ -a,b +c,d @@` 的 `c` 起始行号 + 行内偏移）。

### Step 2 · 扫描敏感词（必做）

> 详细规则见 `references/sensitive-scan-guide.md`，此处给执行命令。

```bash
# 检查仓库是否有自定义词表文件
grape git-repo contents --owner "$REPO_OWNER" --repo "$REPO_NAME" --path "docs/sensitive-words.txt" --raw 2>/dev/null || true
```

扫描逻辑（bash 实现，逐文件逐行）：
1. **合并词表**：内置词表（`huawei.com` `codehub` `myhuaweicloud.com`）+ 仓库 `docs/sensitive-words.txt`（存在则逐行追加，`#` 开头跳过）。
2. **IP 正则**：IPv4 `\b(?:\d{1,3}\.){3}\d{1,3}\b` + IPv6 `\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b`（简化全格式）。
3. **逐文件扫描**：对每个变更文件的 patch 新增行（`+` 开头），按行扫描词表（子串匹配，大小写不敏感）+ IP 正则。
4. **记录命中**：`(filename, line_number, matched_word)` 三元组。

```bash
# 扫描脚本（可直接执行）
SCAN_RESULTS=$(bash -c '
BUILTIN_WORDS="huawei.com codehub myhuaweicloud.com"
# 尝试读取仓库自定义词表（如果已经拉取到本地）
CUSTOM_WORDS=""
if [ -f "docs/sensitive-words.txt" ]; then
  CUSTOM_WORDS=$(grep -v "^#" docs/sensitive-words.txt | grep -v "^$" | tr "\n" " ")
fi
ALL_WORDS="$BUILTIN_WORDS $CUSTOM_WORDS"

# IP 正则
IPV4_RE="\b([0-9]{1,3}\.){3}[0-9]{1,3}\b"
IPV6_RE="\b([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"

# 逐文件扫描 PR diff（从 pulls files 的 JSON 输出解析）
# 命中输出格式: filename:lineno:matched_word
echo "$PR_DIFF_JSON" | jq -r ".[] | .filename, .patch" | \
  awk -v words="$ALL_WORDS" -v ipv4="$IPV4_RE" -v ipv6="$IPV6_RE" "
  # 解析 patch 行号 + 扫描（具体实现见 sensitive-scan-guide.md）
  "
')
```

### Step 3 · 组装评论（必做）
- 命中数 = 0 → 不附加敏感词报告（原审查结论不变）。
- 命中数 > 0 → 在检视评论末尾附加：
  ```
  ---
  ### 敏感词校验结果

  | 文件 | 行号 | 命中词 |
  |------|------|--------|
  | path/to/file | 42 | huawei.com |
  | path/to/file | 87 | 10.0.0.1 |

  > ⚠️ 以上为疑似敏感信息命中，仅提示不阻断合入。
  ```
- 评论 body 以 `[QA Agent]` 开头。

### Step 4 · 发布检视评论（必做）
```bash
grape git-repo pulls comments create --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" \
  --json '{"body":"[QA Agent] PR 检视完成。\n\n{原审查结论}\n\n---\n### 敏感词校验结果\n\n| 文件 | 行号 | 命中词 |\n|------|------|--------|\n| {file} | {line} | {word} |\n\n> ⚠️ 以上为疑似敏感信息命中，仅提示不阻断合入。"}'
```
- 未命中时评论不含敏感词段落（原审查结论不变）。

### Step 5 · A2A 交棒 sre agent（必做）
```bash
# 场景核验
echo "scene_id=${GRAPE_SCENE_ID:+set}"
SCENE_NODES="$(grape scenes detail --scene_id "$GRAPE_SCENE_ID" --json-out 2>/dev/null)"
echo "scene_agents: $(echo "$SCENE_NODES" | jq -r '[.nodes[]?.tag // .nodes[]?.skill] | unique | join(",")' 2>/dev/null)"

# 校验 sre 节点命中后发起 A2A
grape a2a handoff --json '{
  "agent": "sre",
  "payload": {
    "repository": "'"$REPO_FULL_NAME"'",
    "account": "'"$ACCOUNT"'",
    "issue_number": '"$ISSUE_NUMBER"',
    "pr_number": '"$PR_NUMBER"',
    "context": "PR #'"$PR_NUMBER"' 检视完成，敏感词校验已执行（命中 {n} 处），可进入发布/部署。"
  }
}' --json-out
```
- 未命中 sre 节点 → 评论说明 + next_step=done。

### Step 6 · 任务完成流程（必做）
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
> `already:true` 忽略。然后结束任务。

---

## 错误排查速查

| 现象 | 处理 |
|------|------|
| `PR_NUMBER` 未注入 | 从 `events detail` payload 解析；仍缺失 → 评论说明 + next_step=reply_comment |
| `pulls files` 返回空 | PR 无变更文件 → 评论说明 + 收尾（命中=0） |
| `pulls files` 5xx | `grape tasks wait` 重试；仍失败用 `grape git ls-remote` 核对 |
| `contents` 404（词表不存在） | 正常——仅用内置词表，继续扫描 |
| IP 正则误报版本号 | 报告标注「疑似 IP」；版本号模式（如 `1.2.3.4`）仍报告，由审查者判断 |

## 明确禁止

- ❌ 阻断 PR 合入 / 作为门禁。
- ❌ 校验 PR 标题/描述。
- ❌ 编造命令/参数（命令只引事实基线）。
- ❌ 裸 `git`（一律走 `grape git`）。
- ❌ A2A 交办技能名——交办 **agent**（`sre`）。
- ❌ 跳过最终 Step（tasks complete）。
