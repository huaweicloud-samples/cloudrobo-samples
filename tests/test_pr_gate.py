#!/usr/bin/env python3
"""pr_gate.py 门禁脚本测试（仅标准库 unittest，亦可被 pytest 收集）.

覆盖范围：
- added_line_no：`git diff -U0` hunk 起始行 `n-1` 语义、多 hunk、增/删/改/无变更、无 base 全量口径
- SECRET_PATTERNS：8 类泄露正则逐条正例/反例（含全角冒号、掩码 `*`、长度边界）
- License 分类（经 main() 端到端）：开源放行 / 商业许可一票阻断（优先于开源指纹）/
  非开源阻断 / 篡改特征 / 缺失仅提示 / 根目录判定口径（嵌套 LICENSE 不计入）
- ip_pat（经 main() 端到端）：IPv4/IPv6 命中、URL:端口、误报反例、敏感词优先于 IP

运行（均在仓库根目录执行）：
  python3 -m unittest discover -s tests -v      # 或 python3 -m unittest tests.test_pr_gate -v
  python3 -m pytest tests/test_pr_gate.py       # 如已安装 pytest
  # 在 tests/ 目录内则直接：python3 test_pr_gate.py
"""
import contextlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GATE_PATH = os.path.join(REPO_ROOT, ".github", "scripts", "pr_gate.py")


def _load_gate():
    spec = importlib.util.spec_from_file_location("pr_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

# ---- License 样本（只需包含 pr_gate LICENSE_OPEN_SOURCE_SNIPPETS 的指纹子串）----
MIT_TEXT = """\
MIT License

Copyright (c) 2026 Example Corp.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""
APACHE_TEXT = """\
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/
"""
GPL_TEXT = """\
                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007
"""
MULAN_TEXT = """\
木兰宽松许可证
第 1 条 定义：本许可证中的「许可证」指本文档第 1 至第 x 条。
"""

TEN_LINES = "".join("line%d\n" % i for i in range(1, 11))


# ---------------- 通用脚手架 ----------------
def _git(args, cwd):
    proc = subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True)
    if proc.returncode != 0:
        raise RuntimeError("git %s 失败:\n%s" % (" ".join(args), proc.stdout))
    return proc.stdout.strip()


def _write_files(root, files):
    for rel, content in files.items():
        path = os.path.join(root, rel)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)


def _commit(root, message):
    _git(["add", "-A"], cwd=root)
    _git(["-c", "user.name=gate-test", "-c", "user.email=gate-test@example.com",
          "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", message], cwd=root)
    return _git(["rev-parse", "HEAD"], cwd=root)


@contextlib.contextmanager
def _tmpdir():
    path = tempfile.mkdtemp(prefix="pr_gate_test_")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def _chdir(path):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield path
    finally:
        os.chdir(prev)


@contextlib.contextmanager
def git_repo(base_files):
    """临时 git 仓库（含首个提交）；退出时先恢复 cwd 再清理目录。"""
    with _tmpdir() as path:
        _git(["init"], cwd=path)
        _write_files(path, base_files)
        _commit(path, "base")
        yield path


@contextlib.contextmanager
def two_commit_repo(base_files, head_files):
    """base 提交 base_files，head 提交覆盖 head_files；yield (仓库路径, base sha)。"""
    with git_repo(base_files) as path:
        base_sha = _git(["rev-parse", "HEAD"], cwd=path)
        _write_files(path, head_files)
        _commit(path, "head")
        yield path, base_sha


def run_gate(repo_path, extra=()):
    """在临时仓库内以子进程运行门禁脚本（屏蔽 GITHUB_* 环境变量干扰）。"""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GITHUB_"))
    cmd = [sys.executable, GATE_PATH] + list(extra)
    return subprocess.run(cmd, cwd=repo_path, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)


# ---------------- skip / 豁免口径 ----------------
class SkipPartsTest(unittest.TestCase):
    """tests/ 为门禁自身用例（含伪密钥样本），须整体豁免出扫描范围。"""

    def test_gate_own_tests_dir_is_skipped(self):
        self.assertTrue(gate.skip("tests/test_pr_gate.py"))
        self.assertTrue(gate.skip("tests/helper.py"))

    def test_normal_paths_not_skipped(self):
        self.assertFalse(gate.skip("samples/robot/demo.py"))
        self.assertFalse(gate.skip("README.md"))


# ---------------- added_line_no ----------------
class AddedLineNoTest(unittest.TestCase):
    """基于真实临时 git 仓库验证 `git diff -U0` 行号解析。"""

    def _added(self, base_content, head_content, extra_base=None, extra_head=None):
        base = dict({"app.txt": base_content}, **(extra_base or {}))
        head = dict({"app.txt": head_content}, **(extra_head or {}))
        with two_commit_repo(base, head) as (path, base_sha):
            with _chdir(path):
                return gate.added_line_no(base_sha, "HEAD", "app.txt")

    def test_modified_line_reports_its_new_number(self):
        # hunk 头 `+5` 本身即首个内容行：改为第 5 行 → {5}
        head = TEN_LINES.replace("line5\n", "line5-fixed\n")
        self.assertEqual(self._added(TEN_LINES, head), {5})

    def test_inserted_lines_report_insert_positions(self):
        # 第 3 行后插入 2 行 → 新文件第 4、5 行
        head = TEN_LINES.replace("line4\n", "new-a\nnew-b\nline4\n")
        self.assertEqual(self._added(TEN_LINES, head), {4, 5})

    def test_deleted_line_adds_nothing(self):
        head = TEN_LINES.replace("line5\n", "")
        self.assertEqual(self._added(TEN_LINES, head), set())

    def test_multiple_hunks(self):
        head = TEN_LINES.replace("line2\n", "line2!\n").replace("line9\n", "line9!\n")
        self.assertEqual(self._added(TEN_LINES, head), {2, 9})

    def test_appended_lines_at_eof(self):
        head = TEN_LINES + "line11\nline12\nline13\n"
        self.assertEqual(self._added(TEN_LINES, head), {11, 12, 13})

    def test_unchanged_file_yields_empty_set(self):
        self.assertEqual(
            self._added(TEN_LINES, TEN_LINES,
                        extra_base={"other.txt": "x\n"}, extra_head={"other.txt": "y\n"}),
            set())

    def test_wholly_rewritten_file_counts_all_new_lines(self):
        self.assertEqual(self._added(TEN_LINES, "alpha\nbeta\ngamma\n"), {1, 2, 3})

    def test_no_base_treats_whole_file_as_added(self):
        with _tmpdir() as path, _chdir(path):
            with open("app.txt", "w", encoding="utf-8") as fh:
                fh.write(TEN_LINES)
            self.assertEqual(gate.added_line_no("", "HEAD", "app.txt"),
                             set(range(1, 11)))

    def test_no_base_empty_file_yields_empty_set(self):
        with _tmpdir() as path, _chdir(path):
            with open("app.txt", "w", encoding="utf-8") as fh:
                fh.write("")
            self.assertEqual(gate.added_line_no("", "HEAD", "app.txt"), set())


# ---------------- SECRET_PATTERNS ----------------
class SecretPatternsTest(unittest.TestCase):
    """按正则片段唯一定位每条 SECRET_PATTERNS，逐条验证正例/反例。"""

    POSITIVE = {
        # 明文口令（password/passwd/pwd 赋值即命中，值任意非空白）
        r"password|passwd|pwd": [
            'password = "hunter2"',
            "passwd: root",
            "my pwd = abc123",
            "PWD=letmein",
            "password：abc123",  # 全角冒号同样判定
        ],
        # passcode：值须 ≥4 位非星号
        r"passcode": ["passcode: 987654", "PASSCODE=abcd1234"],
        # 密钥赋值：值须 ≥4 位非星号
        r"secret\s*[:=：]": ["secret: mysecretvalue", "SECRET = supersecret"],
        # AK/SK 或 token：值须 ≥16 位
        "apikey|api_key|access": [
            'token = "abcdefgh12345678"',
            "api_key: ABCDEF1234567890AB",
            "access-key: abcd-1234-abcd-1234",
            'secret_key = "0123456789abcdef"',
        ],
        # 私钥块
        "BEGIN ": [
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "-----BEGIN EC PRIVATE KEY-----",
            "-----BEGIN PGP PRIVATE KEY-----",
        ],
        # 云账号 AK/SK：前缀后须 ≥14 位字母数字
        r"AK(?:LT|SK|IA)": [
            "AKLTW3CNFQZ6EXAMPAA",
            "AKIAIOSFODNN7EXAMPLE",
            "AKSKABCDEF0123456789",
        ],
        # GitHub token：gh[pousr]_ + ≥30 位
        "gh[pousr]_": ["ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abc"],
        # JWT：eyJ 开头三段式
        "eyJ": [
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c",
        ],
    }

    NEGATIVE = {
        r"password|passwd|pwd": [
            "password =",               # 等号后无值
            "password policy updated",  # 无赋值
            "db_passwd: root",          # `\b` 前置边界：`_passwd` 不算词首，不命中
        ],
        r"passcode": ["passcode: ab", "passcode: ****"],  # <4 位 / 星号掩码
        r"secret\s*[:=：]": ["secret: ab", "secret: ****"],
        "apikey|api_key|access": [
            "token: short",             # 值不足 16 位
            "apikey: 123456789012345",  # 15 位，边界外
        ],
        "BEGIN ": ["-----BEGIN PUBLIC KEY-----", "-----BEGIN CERTIFICATE-----"],
        r"AK(?:LT|SK|IA)": ["AKLT123456", "AKIA123"],  # 前缀后不足 14 位
        "gh[pousr]_": ["ghp_short"],
        "eyJ": ["notajwt.header.sig", "eyJonlynodots"],
    }

    def _pattern(self, frag):
        hits = [p for p, _desc in gate.SECRET_PATTERNS if frag in p.pattern]
        self.assertEqual(len(hits), 1, "片段 %r 应唯一定位一条正则" % frag)
        return hits[0]

    def test_table_has_entry_for_every_pattern(self):
        frags = set(self.POSITIVE) | set(self.NEGATIVE)
        self.assertEqual(len(frags), len(gate.SECRET_PATTERNS),
                         "每条 SECRET_PATTERNS 都应有正/反例覆盖")

    def test_patterns_are_compiled_with_description(self):
        self.assertEqual(len(gate.SECRET_PATTERNS), 8)
        for pat, desc in gate.SECRET_PATTERNS:
            self.assertIsInstance(pat, type(re.compile("")))
            self.assertTrue(desc, "每条模式须有中文描述")

    def test_positive_cases_hit(self):
        for frag, lines in sorted(self.POSITIVE.items()):
            pat = self._pattern(frag)
            for line in lines:
                with self.subTest(pattern=frag, line=line):
                    self.assertIsNotNone(pat.search(line), "应命中: %r" % line)

    def test_negative_cases_do_not_hit(self):
        for frag, lines in sorted(self.NEGATIVE.items()):
            pat = self._pattern(frag)
            for line in lines:
                with self.subTest(pattern=frag, line=line):
                    self.assertIsNone(pat.search(line), "不应命中: %r" % line)

    def test_github_token_prefixes(self):
        pat = self._pattern("gh[pousr]_")
        body = "a" * 30
        for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
            with self.subTest(prefix=prefix):
                self.assertIsNotNone(pat.search("token %s%s" % (prefix, body)))


# ---------------- License 分类（端到端） ----------------
class LicenseGateTest(unittest.TestCase):
    """经 main() 子进程验证 License 门禁分级：存在则须开源合规，缺失仅提示。"""

    def _gate_with_license(self, license_text, license_name="LICENSE", extra_files=None):
        files = {"README.md": "# demo\n"}
        if license_text is not None:
            files[license_name] = license_text
        files.update(extra_files or {})
        with git_repo(files) as path:
            return run_gate(path)

    def test_known_open_source_licenses_pass(self):
        for name, text in [("MIT", MIT_TEXT), ("Apache-2.0", APACHE_TEXT),
                           ("GPL-3.0", GPL_TEXT), ("木兰-PSL", MULAN_TEXT)]:
            with self.subTest(license=name):
                proc = self._gate_with_license(text)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertNotIn("::error", proc.stdout)
                self.assertIn("阻断级问题: 0", proc.stdout)

    def test_business_source_license_blocks(self):
        proc = self._gate_with_license("Business Source License 1.1\n" + APACHE_TEXT)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error", proc.stdout)
        self.assertIn("非开源许可证（商业/限制性条款）", proc.stdout)
        self.assertIn("BSL", proc.stdout)

    def test_commons_clause_overrides_open_source_fingerprint(self):
        # 「开源文本 + 限制条款」混合许可：封闭许可指纹优先于开源指纹
        proc = self._gate_with_license(APACHE_TEXT + "\nCommons Clause License text.\n")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Commons Clause", proc.stdout)

    def test_copyright_only_blocks_as_unknown_license(self):
        # 单纯 copyright 声明不算许可
        proc = self._gate_with_license("Copyright 2026 Example Corp. All rights reserved.\n")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("非已知开源许可证", proc.stdout)

    def test_tamper_hints_block(self):
        for hint_line in ("This distribution is licensed under no additional terms.\n",
                          "The isno warranty clause is void.\n"):
            with self.subTest(hint=hint_line.strip()):
                proc = self._gate_with_license(MIT_TEXT + "\n" + hint_line)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("疑似被篡改", proc.stdout)

    def test_missing_license_is_info_only(self):
        proc = self._gate_with_license(None)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("License 缺失（不阻断）", proc.stdout)

    def test_license_md_variant_recognized(self):
        proc = self._gate_with_license(MIT_TEXT, license_name="LICENSE.md")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("License 缺失", proc.stdout)

    def test_nested_license_not_judged(self):
        # 仅嵌套资产 LICENSE 不参与判定，根目录仍视为缺失
        proc = self._gate_with_license(None, extra_files={"samples/robot/LICENSE": MIT_TEXT})
        self.assertEqual(proc.returncode, 0)
        self.assertIn("License 缺失（不阻断）", proc.stdout)


# ---------------- 密钥扫描 × 新增行口径（端到端） ----------------
class SecretScanIntegrationTest(unittest.TestCase):
    TOKEN = "token = zzzz1234567890abcd\n"  # 命中「AK/SK 或 token」（值 ≥16 位）

    def test_secret_in_added_line_blocks(self):
        with git_repo({"config.txt": 'password = "hunter2"\n'}) as path:
            proc = run_gate(path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("密钥泄露（明文口令）", proc.stdout)
        self.assertIn("阻断级问题: 1", proc.stdout)

    def test_multiple_patterns_on_one_line_report_once(self):
        line = 'password = "abc" ghp_' + "a" * 30 + "\n"
        with git_repo({"config.txt": line}) as path:
            proc = run_gate(path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("阻断级问题: 1", proc.stdout)  # 命中即 break，一行只报一条

    def test_secret_on_unchanged_line_not_flagged_with_base(self):
        base = {"a.txt": self.TOKEN, "b.txt": "v1\n"}
        # 场景 1：仅改 b.txt，a.txt 存量密钥不误伤
        with two_commit_repo(base, {"b.txt": "v2\n"}) as (path, base_sha):
            proc = run_gate(path, ["--scan-mode", "changed", "--base", base_sha])
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("阻断级问题: 0", proc.stdout)
        # 场景 2：改写 a.txt 密钥行（新增行）→ 阻断
        with two_commit_repo(base, {"a.txt": "token = aaaa1234567890bcde\n"}) as (path, base_sha):
            proc = run_gate(path, ["--scan-mode", "changed", "--base", base_sha])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("密钥泄露（AK/SK 或 token）", proc.stdout)


# ---------------- ip_pat（端到端，提示级） ----------------
class SensitiveIpTest(unittest.TestCase):
    def _gate_with_content(self, content):
        with git_repo({"deploy.md": content}) as path:
            return run_gate(path)

    def test_ipv4_flagged(self):
        proc = self._gate_with_content("server = 192.168.1.10\n")
        self.assertEqual(proc.returncode, 0)  # 提示级不阻断
        self.assertIn("敏感词命中（IP: 192.168.1.10）", proc.stdout)

    def test_ipv6_flagged(self):
        proc = self._gate_with_content("gateway = fd00::1234\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("敏感词命中（IP: fd00::1234）", proc.stdout)

    def test_ipv4_with_port_and_url(self):
        proc = self._gate_with_content("endpoint: http://10.0.0.1:8080/api\n")
        self.assertIn("敏感词命中（IP: 10.0.0.1）", proc.stdout)

    def test_ip_embedded_in_text_not_flagged(self):
        # 前邻 \w / 后随 . 的点分串不算独立 IP
        proc = self._gate_with_content("revision=v1.2.3.4\nhost 1.2.3.4.example.com\n")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("敏感词命中（IP", proc.stdout)

    def test_sensitive_word_takes_precedence_over_ip(self):
        # 同一行命中敏感词即 break，不再单独报 IP
        proc = self._gate_with_content("contact huawei.com or 10.0.0.1\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("敏感词命中（huawei.com）", proc.stdout)
        self.assertNotIn("IP: 10.0.0.1", proc.stdout)


if __name__ == "__main__":
    unittest.main()
