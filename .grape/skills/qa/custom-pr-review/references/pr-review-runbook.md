# custom-pr-review 执行走查表（pr-review-runbook）

> 目的：把 PR 多门禁检视的每一步固化下来，Agent 照着执行。
> A 类变量：`$REPO_OWNER` `$REPO_NAME` `$PR_NUMBER` `$GRAPE_SESSION_TOKEN`；B 类（`$ISSUE_NUMBER`/`$ACCOUNT`/`$GRAPE_SCENE_ID`）缺失即跳过。
> ⚠️ **评论前缀硬规**：所有 PR 评论 body 一律以 `[QA Agent]` 开头。

---

## Step 0 · 前置 + 读任务上下文（必须最先）
```bash
grape --version; grape refresh
echo "token=${GRAPE_SESSION_TOKEN:+ok} owner=$REPO_OWNER repo=$REPO_NAME pr=${PR_NUMBER:-MISSING} issue=${ISSUE_NUMBER:-absent}"
python3 -V
for t in ruff black clang-format clang-tidy pip-audit mdl yamllint; do command -v "$t" >/dev/null 2>&1 && echo "$t=ok" || echo "$t=MISSING"; done
grape tasks current --json-out
[ -n "${ISSUE_NUMBER:-}" ] && grape issues detail --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$ISSUE_NUMBER" --json-out || echo "ISSUE_NUMBER 未注入 → 跳过"
```
- `PR_NUMBER` 缺失 → 从 `tasks current`/`events detail` payload 解析；仍缺失 → 评论说明 + `next_step=reply_comment`。
- ⚠️ `ISSUE_NUMBER` 在 PR 订阅运行时可能是内部链路 ID（`issues detail` 返回 422）→ 不写入 A2A payload。

### Step 1 · 拉 PR 变更文件（必做）
```bash
grape git-repo pulls get --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" --json-out
PR_FILES_JSON=$(grape git-repo pulls files --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" --json-out)
```
- `pulls files` 返回数组，每项含 `filename` + `patch`（unified diff）。

### Step 2 · 执行 7 类门禁扫描（必做）

> 各门禁检测规则/正则/分级标准详见 `references/gate-checks-guide.md`。下方给出**可直接执行**的统一扫描脚本（python3，输出 JSON）。

```bash
CUSTOM_WORDS_RAW=$(grape git-repo contents --owner "$REPO_OWNER" --repo "$REPO_NAME" --path "docs/sensitive-words.txt" --raw 2>/dev/null || true)

GATE_OUT=$(printf '%s' "$PR_FILES_JSON" | python3 -c '
import json, re, sys

BUILTIN_WORDS = ["huawei.com", "codehub", "myhuaweicloud.com"]
IPV4 = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b")
SECRET_PATTERNS = [
    (re.compile(r"(?:password|passwd|pwd)\s*[=:]\s*\S+", re.I), "密码"),
    (re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----"), "私钥"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS AK"),
    (re.compile(r"\b(?:sk|ak|access_key|secret_key)\s*[=:]\s*[\w/+=]{20,}", re.I), "AK/SK"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{36}\b"), "GitHub Token"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*\b"), "JWT Token"),
]
LICENSE_OK = re.compile(r"^\s*Copyright\s|^\s*Licensed\s|^\s*\*\s*Copyright|SPDX-License-Identifier", re.I)
LICENSE_BROKEN = re.compile(r"is\b.*\bisno\b|isno\b.*\bis\b", re.I)

EXTRA = sys.argv[1] if len(sys.argv) > 1 else ""
def load_words(extra):
    words = list(BUILTIN_WORDS)
    for line in (extra or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"): words.append(line)
    seen, uniq = set(), []
    for w in words:
        if w.lower() not in seen: seen.add(w.lower()); uniq.append(w)
    return uniq

payload = json.load(sys.stdin)
files = payload if isinstance(payload, list) else (payload.get("files") or [])
words = load_words(EXTRA)
results = {"blocking": [], "warning": [], "sensitive": [], "skipped": []}

for f in files:
    patch = f.get("patch") or ""
    if not patch: continue
    path = f.get("filename") or "?"
    lineno = None
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw); lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+++") or raw.startswith("---"): continue
        elif raw.startswith("+"):
            text = raw[1:]; low = text.lower()
            # 1. License 合规（阻断级）
            if path.endswith("LICENSE") or "license" in path.lower():
                if LICENSE_BROKEN.search(text):
                    results["blocking"].append({"gate":"license","file":path,"line":lineno,"detail":"License 被改坏（is→isno）"})
            # 2. 密钥泄露（阻断级）
            for pat, label in SECRET_PATTERNS:
                if pat.search(text):
                    results["blocking"].append({"gate":"secret","file":path,"line":lineno,"detail":label})
            # 3. 敏感词（警告级，沿用 v1）
            hits = [w for w in words if w.lower() in low]
            hits += [m.group(0) for m in list(IPV4.finditer(text)) + list(IPV6.finditer(text))]
            for w in hits:
                results["sensitive"].append({"file":path,"line":lineno,"word":w})
            lineno += 1
        elif raw.startswith(" "): lineno += 1

# 4. License 缺失检查（阻断级）：仓库根无 LICENSE 文件
# 5-7. 代码质量/编译/依赖/文档门禁：需外部工具，在 Step 2b 执行
print(json.dumps(results))
' "$CUSTOM_WORDS_RAW")

echo "$GATE_OUT"
```

### Step 2b · 工具依赖门禁（可选工具可用时执行，缺失降级跳过）
```bash
# 提取变更文件列表
CHANGED_PY=$(printf '%s' "$PR_FILES_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); files=d if isinstance(d,list) else d.get("files",[]); print(" ".join(f["filename"] for f in files if f.get("filename","").endswith(".py")))' 2>/dev/null)
CHANGED_CPP=$(printf '%s' "$PR_FILES_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); files=d if isinstance(d,list) else d.get("files",[]); print(" ".join(f["filename"] for f in files if any(f.get("filename","").endswith(e) for e in (".cpp",".cc",".h",".hpp"))))' 2>/dev/null)
CHANGED_MD=$(printf '%s' "$PR_FILES_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); files=d if isinstance(d,list) else d.get("files",[]); print(" ".join(f["filename"] for f in files if f.get("filename","").endswith(".md")))' 2>/dev/null)
CHANGED_YAML=$(printf '%s' "$PR_FILES_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); files=d if isinstance(d,list) else d.get("files",[]); print(" ".join(f["filename"] for f in files if any(f.get("filename","").endswith(e) for e in (".yml",".yaml"))))' 2>/dev/null)

# 代码质量（警告级）：ruff+black / clang-format+clang-tidy
command -v ruff >/dev/null 2>&1 && [ -n "$CHANGED_PY" ] && ruff check $CHANGED_PY 2>&1 || echo "ruff=SKIP"
command -v black >/dev/null 2>&1 && [ -n "$CHANGED_PY" ] && black --check $CHANGED_PY 2>&1 || echo "black=SKIP"
command -v clang-format >/dev/null 2>&1 && [ -n "$CHANGED_CPP" ] && clang-format --dry-run $CHANGED_CPP 2>&1 || echo "clang-format=SKIP"

# 编译构建（阻断级）：Python 编译检查
[ -n "$CHANGED_PY" ] && python3 -m py_compile $CHANGED_PY 2>&1 && echo "py_compile=OK" || echo "py_compile=FAIL/SKIP"

# 依赖漏洞（警告级）：pip-audit
command -v pip-audit >/dev/null 2>&1 && pip-audit --desc 2>&1 || echo "pip-audit=SKIP"

# 文档规范（警告级）：mdl / yamllint
command -v mdl >/dev/null 2>&1 && [ -n "$CHANGED_MD" ] && mdl $CHANGED_MD 2>&1 || echo "mdl=SKIP"
command -v yamllint >/dev/null 2>&1 && [ -n "$CHANGED_YAML" ] && yamllint $CHANGED_YAML 2>&1 || echo "yamllint=SKIP"
```
- 工具缺失 → 对应门禁标注「SKIP（工具未安装）」，不中断检视。

### Step 3 · 组装评论（必做）

评论结构（同一条件内一次性发布）：
```
[QA Agent] {审查结论}

---
### ❌ 阻断级问题（须修复）
{blocking_rows or "无"}

### ⚠️ 警告级问题（仅提示）
{warning_rows or "无"}

### 敏感词校验（仅提示）
{sensitive_table or "未命中，不附加"}
```
- 阻断/警告/敏感词段落的「无」或「未命中」时不输出对应段落。
- `BLOCK_COUNT`/`WARN_COUNT`/`HIT_COUNT` 从 `GATE_OUT` JSON + Step 2b 结果统计。

### Step 4 · 发布检视评论（必做）
```bash
grape git-repo issues comments create --owner "$REPO_OWNER" --repo "$REPO_NAME" --number "$PR_NUMBER" \
  --json '{"body":"[QA Agent] <审查结论>\n\n---\n### ❌ 阻断级问题\n<blocking_rows>\n\n### ⚠️ 警告级问题\n<warning_rows>\n\n### 敏感词校验结果\n<sensitive_table>"}'
```
- 用 `issues comments create`（GitHub PR 与 Issue 同号）；`pulls comments create` 仅行级评审返回 422。
- 发布后核对评论 id/URL；失败重试 ≤3 次。

### Step 5 · 交棒规则（默认不交棒；sre 仅条件触发）
```bash
echo "event=${DEVOPS_EVENT_TYPE:-unknown} scene_id=${GRAPE_SCENE_ID:+set}"
```
- 非 `pull_request_merged` → 不交棒，进 Step 6。
- 仅合并事件时按 v1 逻辑 A2A 交棒 sre（场景校验同 v1）。

### Step 6 · 任务完成流程（必做）
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
> `already:true` 忽略。然后结束任务。

---

## 错误排查速查

| 现象 | 处理 |
|------|------|
| `PR_NUMBER` 未注入 | 从 `tasks current`/`events detail` 解析；仍缺失 → `next_step=reply_comment` |
| `pulls files` 5xx | 重试 ≤3 次；仍失败用 `grape git ls-remote` 核对 |
| `pulls comments create` 422 | 改用 `issues comments create` |
| 管道喂入脚本读到空 stdin | 禁用 heredoc，固定用 `python3 -c` + 管道 |
| 外部工具缺失 | 降级为 SKIP+备注，不中断检视 |
| `python3: command not found` | 敏感词降级为 POSIX awk（见 sensitive-scan-guide.md §4） |

## 明确禁止

- ❌ 直接阻断 PR 合入（由 GitHub Actions required checks 实现）。
- ❌ 校验 PR 标题/描述。
- ❌ 跳过任何门禁（工具缺失须降级+备注，不省略）。
- ❌ 编造命令/参数；裸 `git`；跳过 `tasks complete`。
