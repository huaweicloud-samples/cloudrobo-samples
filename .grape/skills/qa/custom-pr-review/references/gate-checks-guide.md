# 门禁检查指南（gate-checks-guide）

> 本指南定义 custom-pr-review v2.1.0 的 4 类警告级门禁检查规则：检测内容、分级标准、正则/工具、降级策略。
> 可执行实现见 `pr-review-runbook.md` Step 2/2b；敏感词详细规则见 `sensitive-scan-guide.md`。

---

## 0. 职责边界（2026-10-23 起）

- **阻断级门禁（编译构建/密钥泄露/License 合规）已移出本技能**：由仓库 CI required check `ci`（`.github/scripts/pr_gate.py`）独占承担，本技能不检视、不判定、不报告 ❌。
- 本技能仅产出**警告级**结论与**敏感词提示**；工具缺失时降级为 SKIP+备注，不中断检视。

## 1. 分级策略

| 级别 | 标注 | 含义 | 门禁 |
|------|------|------|------|
| ⚠️ 警告 | `WARNING` | 仅提示，不强制修改 | 代码质量、依赖漏洞、敏感词、文档规范 |

- 本技能仅做评论报告，不直接阻断合入；合入阻断由 GitHub Actions required checks（CI 门禁）实现。
- 敏感词校验维持 v1 行为：仅提示不阻断。

## 2. 代码质量检查（⚠️ 警告级）

### Python
| 工具 | 命令 | 说明 |
|------|------|------|
| ruff | `ruff check <files>` | lint + import 排序 |
| black | `black --check <files>` | 格式化检查 |

### C++
| 工具 | 命令 | 说明 |
|------|------|------|
| clang-format | `clang-format --dry-run <files>` | 格式化检查 |
| clang-tidy | `clang-tidy <files>` | 静态分析 |

### 降级策略
- 工具缺失 → SKIP+备注，不中断检视。
- 仅扫描对应语言变更文件。

## 3. 依赖漏洞扫描（⚠️ 警告级）

### pip-audit
```bash
pip-audit --desc
```
- 扫描 `requirements.txt`/`pyproject.toml` 中的依赖 CVE。
- 工具缺失 → SKIP+备注。

## 4. 敏感词校验（⚠️ 警告级，沿用 v1）

> 详细规则见 `sensitive-scan-guide.md`。

| 规则 | 说明 |
|------|------|
| 内置词表 | `huawei.com` `codehub` `myhuaweicloud.com`（子串匹配，大小写不敏感） |
| IP 正则 | IPv4 + IPv6（含公网/内网）；命中标注「疑似 IP」 |
| 词表扩展 | 仓库 `docs/sensitive-words.txt` 存在则合并 |
| 校验范围 | 仅 PR 变更文件新增行 |
| 命中动作 | 附加到检视评论末尾；未命中不附加 |

## 5. 文档规范检查（⚠️ 警告级）

| 工具 | 命令 | 文件类型 |
|------|------|---------|
| mdl | `mdl <files.md>` | Markdown lint |
| yamllint | `yamllint <files.yml>` | YAML 校验 |

- 工具缺失 → SKIP+备注。
- 仅扫描 `.md`/`.yml`/`.yaml` 变更文件。

## 6. 评论输出格式

### 警告级问题段落
```markdown
---
### ⚠️ 警告级问题（仅提示）

| 门禁 | 文件 | 行号 | 详情 |
|------|------|------|------|
| 代码质量 | src/main.py | 10 | ruff: unused import |
| 文档规范 | README.md | 5 | mdl: line too long |
```

### 敏感词段落（命中>0 时附加）
```markdown
### 敏感词校验结果

| 文件 | 行号 | 命中词 |
|------|------|--------|
| src/config.yaml | 42 | huawei.com |

> ⚠️ 仅提示不阻断合入。
```

## 7. 测试覆盖要求

| 场景 | 输入 | 预期 |
|------|------|------|
| 代码风格 | ruff 报警 | ⚠️ 警告 |
| 依赖漏洞 | pip-audit 报 CVE | ⚠️ 警告 |
| 敏感词命中 | diff 含 huawei.com | ⚠️ 警告 |
| 文档 lint | mdl 报警 | ⚠️ 警告 |
| 工具缺失 | ruff 未安装 | SKIP+备注 |
| 全部通过 | 无问题 | 评论仅含审查结论 |
| 阻断级场景 | 编译失败/密钥泄露/License | 不属于本技能：由 CI 门禁 `.github/scripts/pr_gate.py` 覆盖 |
