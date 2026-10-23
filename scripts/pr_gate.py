#!/usr/bin/env python3
"""PR 门禁扫描器（Issue #3 定制PR检视 · 真实阻断能力）.

分级策略（与 custom-pr-review v2.0.0 保持一致）：
- 阻断级 BLOCKING（任一命中 → 退出码 1，产生 failed check）：
  1) 编译失败：Python 源文件语法错误（py_compile 全量）
  2) 密钥/凭证泄露：新增行中的明文口令/私钥/AK/SK/token
  3) License 缺失或被改坏：LICENSE 文件缺失 / 声明被篡改
- 警告级 WARNING（仅 GitHub annotation / PR 评论提示，不失败）：
  ruff/black（Python 风格）、clang-format/clang-tidy（C++ 风格，工具缺失自动 SKIP）、
  pip-audit（依赖漏洞）、mdl/yamllint（文档规范）、敏感词（内部域名/IP，沿用 v1 仅报告）

用法：
  python3 scripts/pr_gate.py [--base <sha>] [--head <sha>] [--scan-mode all|changed]
  --scan-mode all     扫描 head 全量文件（默认；License 检查要求全量口径）
  --scan-mode changed 只扫描 base..head 变更文件
退出码：0 = 门禁通过；1 = 存在阻断级问题；2 = 脚本自身异常
输出：GitHub Actions workflow command（::error/::warning/::notice，自动渲染 annotation）
"""
import argparse
import os
import py_compile
import re
import shutil
import subprocess
import sys

BLOCK_LEVEL, WARN_LEVEL, INFO_LEVEL = "阻断级", "警告级", "提示"

SENSITIVE_WORDS = ["huawei.com", "codehub", "myhuaweicloud.com"]
SENSITIVE_WORDS_FILE = "docs/sensitive-words.txt"  # 存在则合并追加

# 全量脚本扫描时跳过的目录/文件（三方资产/文档/门禁自身）
SKIP_PARTS = (
    ".git/", ".github/", ".grape/", "Assets/UrdfImporter/", "scripts/pr_gate.py",
)

SECRET_PATTERNS = [
    # 与 custom-pr-review v2.0.0 gate-checks-guide.md「密钥泄露」正则同源：password|passwd|pwd 赋值即命中
    (re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=：]\s*\S+"), "明文口令"),
    (re.compile(r"(?i)\bpasscode\s*[:=：]\s*[^\s*]{4,}"), "明文口令"),
    (re.compile(r"(?i)\bsecret\s*[:=：]\s*[^\s*]{4,}"), "密钥赋值"),
    (re.compile(r"(?i)\b(token|apikey|api_key|access[_-]?key|secret[_-]?key)\s*[:=：]\s*['\"]?[A-Za-z0-9_\-]{16,}"), "AK/SK 或 token"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "私钥"),
    (re.compile(r"\bAK(?:LT|SK|IA)[A-Za-z0-9]{14,}\b"), "云账号 AK/SK"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub token"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*\b"), "JWT Token"),
]

# License 指纹片段（大小写不敏感子串）
LICENSE_OK_SNIPPETS = ["apache license", "copyright", "bsd", "mit license", "gnu general public license", "mozilla public license"]
LICENSE_TAMPER_HINTS = [r"\bisno\b", r"\bwa\b(?!rning)", r"licensed under no"]

TEXT_SUFFIX = (".py", ".md", ".txt", ".yml", ".yaml", ".json", ".xml", ".cfg", ".ini", ".toml", ".rst", "", ".sh", ".cmake", ".xacro", ".urdf", ".dae")


def sh(args, cwd=None):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=300)
    except Exception as exc:  # noqa: BLE001
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def git_lines(*args):
    r = sh(["git", *args])
    return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()] if r.returncode == 0 else []


def is_binary(path):
    try:
        with open(path, "rb") as fh:
            return b"\0" in fh.read(8192)
    except OSError:
        return True


def load_sensitive_words():
    words = list(SENSITIVE_WORDS)
    if os.path.isfile(SENSITIVE_WORDS_FILE):
        with open(SENSITIVE_WORDS_FILE, encoding="utf-8", errors="ignore") as fh:
            words += [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    return words


def skip(path):
    return any(part in path for part in SKIP_PARTS)


def changed_files(base, head):
    if base:
        return git_lines("diff", "--name-only", "--diff-filter=ACMR", base, head)
    return git_lines("ls-tree", "-r", "--name-only", head)


def added_line_no(base, head, path):
    """变更文件中「新增行」的行号集合（无 base 时全量视为新增）。

    ⚠️ `git diff -U0` 的 hunk 头 `+n,m` 是 hunk 在新文件中的**起始行号**，
    该行本身已计入 hunk；逐行推进时起始行应按 `n-1` 起算（首个内容行即第 n 行）。
    """
    if base:
        r = sh(["git", "diff", base, head, "--", path, "-U0"])
        if r.returncode != 0:
            return set()
        nums = set()
        newline = None
        for ln in (r.stdout or "").splitlines():
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", ln)
            if m:
                newline = int(m.group(1)) - 1
                continue
            if newline is None or ln.startswith(("diff ", "index ", "--- ", "+++ ")):
                if ln.startswith(("diff ", "index ", "--- ", "+++ ")):
                    newline = None
                continue
            if ln.startswith("+"):
                newline += 1
                nums.add(newline)
            elif not ln.startswith("-"):
                newline += 1
        return nums
    with open(path, encoding="utf-8", errors="ignore") as fh:
        return set(range(1, sum(1 for _ in fh) + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("GITHUB_BASE_REF_SHA") or "")
    ap.add_argument("--head", default=os.environ.get("GITHUB_HEAD_REF_SHA") or "HEAD")
    ap.add_argument("--scan-mode", choices=("all", "changed"), default="all")
    args = ap.parse_args()

    blocking, warnings, infos, skips = [], [], [], []
    head = args.head or "HEAD"
    base = args.base or ""

    if args.scan_mode == "changed" and not base:
        print("::warning::--scan-mode changed 需要有效 --base，回退为全量扫描")

    # ---------- ① 编译门禁（阻断级）：全量 .py 语法编译 ----------
    files = changed_files("" if args.scan_mode == "all" else base, head)
    py_files = [f for f in files if f.endswith(".py") and not skip(f) and os.path.isfile(f)]
    for path in py_files:
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as exc:
            first = str(exc).strip().splitlines()
            m = re.search(r"line (\d+)", str(exc))
            line = int(m.group(1)) if m else 1
            blocking.append((path, line, "编译失败（语法错误）", first[-1][:200] if first else str(exc)[:200]))

    # ---------- ② 密钥泄露门禁（阻断级）：新增行扫描 ----------
    text_files = [f for f in files if not skip(f) and os.path.isfile(f) and not is_binary(f)]
    for path in text_files:
        added = added_line_no(base, head, path)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, 1):
            if base and idx not in added:
                continue  # 仅新增行判定，避免存量问题误伤
            for pat, desc in SECRET_PATTERNS:
                if pat.search(line):
                    blocking.append((path, idx, f"密钥泄露（{desc}）", line.strip()[:160]))
                    break

    # ---------- ③ License 门禁（阻断级）：缺失 / 被改坏 ----------
    licenses = [f for f in files if os.path.basename(f).upper().startswith("LICENSE")]
    if not licenses:
        blocking.append(("(repository)", 0, "License 缺失", "仓库/变更范围内未发现 LICENSE 文件"))
    for path in licenses:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            continue
        if not any(s in content.lower() for s in LICENSE_OK_SNIPPETS):
            blocking.append((path, 1, "License 内容被改坏或为空", "未识别到有效许可声明指纹"))
        for hint in LICENSE_TAMPER_HINTS:
            if re.search(hint, content, re.IGNORECASE):
                blocking.append((path, 1, "License 疑似被篡改（isno/wa）", f"命中篡改特征 {hint}"))
                break

    # ---------- ④ 警告级：工具化检查（缺失自动 SKIP） ----------
    def tool_check(name, cmd, cwd=None, parse=None):
        if not shutil.which(cmd[0]):
            skips.append(f"{name}: 工具未安装，SKIP")
            return
        r = sh(cmd, cwd=cwd)
        if r.returncode == 0:
            return
        out = (r.stdout + r.stderr).strip().splitlines()
        for ln in out[:50]:
            m = parse(ln) if parse else None
            if m:
                path_, line, msg = m
                warnings.append((path_, line, f"{name} {msg}", ln[:160]))
            else:
                warnings.append(("<repo>", 0, name, ln[:160]))

    if py_files:
        tool_check("ruff", ["ruff", "check", "--quiet", *py_files], parse=lambda ln: _parse_generic(ln, r"^([^\s:]+):(\d+):\d+: (.*)$"))
        tool_check("black", ["black", "--check", "--quiet", *py_files], parse=lambda ln: _parse_generic(ln, r"^would reformat ([^\s]+).*$", 1))
    cpp_files = [f for f in files if f.endswith((".cpp", ".cc", ".cxx", ".h", ".hpp")) and os.path.isfile(f)]
    if cpp_files:
        tool_check("clang-format", ["clang-format", "--dry-run", "--Werror", *cpp_files], parse=lambda ln: _parse_generic(ln, r"^([^\s:]+):(\d+):\d+: (.*)$"))
    if cpp_files:
        skips.append("clang-tidy: 需编译数据库，SKIP（警告级不阻断）")
    reqs = [f for f in files if os.path.basename(f) in ("requirements.txt", "pyproject.toml") and os.path.isfile(f)]
    if reqs:
        tool_check("pip-audit", ["pip-audit", "--no-deps", "-r", reqs[0]], parse=lambda ln: _parse_generic(ln, r"^([^\s:]+):(\d+): (.*)$", 1))
    md_files = [f for f in files if f.endswith(".md") and not skip(f) and os.path.isfile(f)]
    if md_files and shutil.which("mdl"):
        tool_check("mdl", ["mdl", *md_files], parse=lambda ln: _parse_generic(ln, r"^([^\s:]+):(\d+): (.*)$"))
    else:
        skips.append("mdl: 工具未安装，SKIP")
    yml_files = [f for f in files if f.endswith((".yml", ".yaml")) and not skip(f) and os.path.isfile(f)]
    if yml_files and shutil.which("yamllint"):
        tool_check("yamllint", ["yamllint", "-f", "parsable", *yml_files], parse=lambda ln: _parse_generic(ln, r"^([^\s:]+):(\d+): (.*)$"))
    else:
        skips.append("yamllint: 工具未安装，SKIP")

    # ---------- ⑤ 敏感词（提示级，沿用 v1 仅报告不阻断） ----------
    words = load_sensitive_words()
    ip_pat = re.compile(
        r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?![\w.])|(?<![\w:])([0-9a-fA-F]{0,4}:[0-9a-fA-F:]{2,39})(?![\w:])"
    )
    for path in text_files:
        added = added_line_no(base, head, path)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, 1):
            if base and idx not in added:
                continue
            low = line.lower()
            for w in words:
                if w in low:
                    infos.append((path, idx, f"敏感词命中（{w}）", line.strip()[:160]))
                    break
            else:
                m = ip_pat.search(line)
                if m:
                    infos.append((path, idx, f"敏感词命中（IP: {m.group(1) or m.group(2)}）", line.strip()[:160]))

    # ---------- 输出 ----------
    def emit(level, items, cmd):
        for path, line, title, msg in items:
            print(f"{cmd} file={path},line={max(line, 1)},title={level} · {title}::{level} {title}：{msg}")

    emit(BLOCK_LEVEL, blocking, "::error")
    emit(WARN_LEVEL, warnings, "::warning")
    emit(INFO_LEVEL, infos, "::notice")
    for s in skips:
        print(f"::notice::门禁降级 {s}")

    print(f"\n==== PR Gate 汇总 ====")
    print(f"阻断级问题: {len(blocking)}")
    print(f"警告级问题: {len(warnings)}")
    print(f"敏感词提示: {len(infos)}")
    print(f"工具降级:   {len(skips)}")

    if blocking:
        print("\n❌ 门禁未通过：存在阻断级问题（License 缺失/改坏、密钥泄露、编译失败），须修复后才能合入。")
        return 1
    print("\n✅ 门禁通过：无阻断级问题；警告/提示见 annotation 与汇总。")
    return 0


def _parse_generic(ln, pattern, msg_group=3):
    m = re.match(pattern, ln)
    if m:
        try:
            return m.group(1), int(m.group(2)), m.group(msg_group)
        except (ValueError, IndexError):
            return None
    return None


if __name__ == "__main__":
    sys.exit(main())
