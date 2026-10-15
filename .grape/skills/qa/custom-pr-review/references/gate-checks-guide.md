# 门禁检查指南（gate-checks-guide）

> 本指南定义 custom-pr-review v2.0.0 的 7 类门禁检查规则：检测内容、分级标准、正则/工具、降级策略。
> 可执行实现见 `pr-review-runbook.md` Step 2/2b；敏感词详细规则见 `sensitive-scan-guide.md`。

---

## 1. 分级策略

| 级别 | 标注 | 含义 | 门禁 |
|------|------|------|------|
| ❌ 阻断 | `BLOCKING` | 须修复才能合入（GitHub Actions required check 失败） | License 合规、密钥泄露、编译构建 |
| ⚠️ 警告 | `WARNING` | 仅提示，不强制修改 | 代码质量、依赖漏洞、敏感词、文档规范 |

- 本技能仅做评论报告，不直接阻断合入；合入阻断由 GitHub Actions required checks 实现。
- 敏感词校验维持 v1 行为：仅提示不阻断。

## 2. License 合规检查（❌ 阻断级）

### 检测规则
| 场景 | 检测方式 | 判定 |
|------|---------|------|
| License 缺失 | 仓库根目录无 `LICENSE` 文件 | ❌ 阻断 |
| License 被改坏 | `LICENSE` 文件内容含 `is` → `isno` 篡改模式 | ❌ 阻断 |
| 不兼容许可 | `LICENSE` 文件含 GPL/AGPL 且仓库无 Copyleft 声明 | ❌ 阻断 |

### 正则
```python
LICENSE_BROKEN = re.compile(r"is\b.*\bisno\b|isno\b.*\bis\b", re.I)
```
- `is→isno` 篡改检测：匹配同一行或相邻行同时出现 `is` 和 `isno` 的模式。

### 文件路径识别
- 文件名含 `license`（大小写不敏感）或路径为 `LICENSE`/`LICENSE.md`/`LICENSE.txt`。

## 3. 密钥泄露检查（❌ 阻断级）

### 检测规则（正则）
| 类型 | 正则 | 说明 |
|------|------|------|
| 密码 | `(?:password\|passwd\|pwd)\s*[=:]\s*\S+` | 赋值语句中的明文密码 |
| 私钥 | `-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----` | PEM 私钥头 |
| AWS AK | `\bAKIA[0-9A-Z]{16}\b` | AWS Access Key ID |
| AK/SK | `\b(?:sk\|ak\|access_key\|secret_key)\s*[=:]\s*[\w/+=]{20,}` | 华为云/通用 AK/SK |
| GitHub Token | `\b(?:ghp\|gho\|ghu\|ghs)_[A-Za-z0-9]{36}\b` | GitHub PAT/App Token |
| JWT | `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*\b` | JWT Token |

### 降级策略
- 无 python3 时用 `grep -E` 逐模式扫描新增行。

## 4. 编译构建检查（❌ 阻断级）

### Python 编译/导入检查
```bash
python3 -m py_compile <changed_files.py>
```
- 编译失败 → ❌ 阻断。
- 仅扫描 `.py` 变更文件。

### C++ ROS2 包构建
- 检测 `package.xml` 存在 → 标记为 ROS2 包。
- 构建工具：`colcon build`（需 ROS2 环境）。
- 工具缺失 → 降级为 SKIP+备注。
- 建议仓库配 `.pre-commit-config.yaml` 使本地与 CI 同口径。

## 5. 代码质量检查（⚠️ 警告级）

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

## 6. 依赖漏洞扫描（⚠️ 警告级）

### pip-audit
```bash
pip-audit --desc
```
- 扫描 `requirements.txt`/`pyproject.toml` 中的依赖 CVE。
- 工具缺失 → SKIP+备注。

## 7. 敏感词校验（⚠️ 警告级，沿用 v1）

> 详细规则见 `sensitive-scan-guide.md`。

| 规则 | 说明 |
|------|------|
| 内置词表 | `huawei.com` `codehub` `myhuaweicloud.com`（子串匹配，大小写不敏感） |
| IP 正则 | IPv4 + IPv6（含公网/内网）；命中标注「疑似 IP」 |
| 词表扩展 | 仓库 `docs/sensitive-words.txt` 存在则合并 |
| 校验范围 | 仅 PR 变更文件新增行 |
| 命中动作 | 附加到检视评论末尾；未命中不附加 |

## 8. 文档规范检查（⚠️ 警告级）

| 工具 | 命令 | 文件类型 |
|------|------|---------|
| mdl | `mdl <files.md>` | Markdown lint |
| yamllint | `yamllint <files.yml>` | YAML 校验 |

- 工具缺失 → SKIP+备注。
- 仅扫描 `.md`/`.yml`/`.yaml` 变更文件。

## 9. 评论输出格式

### 阻断级问题段落
```markdown
---
### ❌ 阻断级问题（须修复）

| 门禁 | 文件 | 行号 | 详情 |
|------|------|------|------|
| License | LICENSE | 1 | License 被改坏（is→isno） |
| 密钥 | src/config.py | 42 | AWS AK |
```

### 警告级问题段落
```markdown
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

## 10. 测试覆盖要求

| 场景 | 输入 | 预期 |
|------|------|------|
| License 缺失 | 仓库无 LICENSE 文件 | ❌ 阻断 |
| License 改坏 | LICENSE 含 is→isno | ❌ 阻断 |
| 密钥泄露 | diff 含 AKIA... | ❌ 阻断 |
| 编译失败 | .py 语法错误 | ❌ 阻断 |
| 代码风格 | ruff 报警 | ⚠️ 警告 |
| 敏感词命中 | diff 含 huawei.com | ⚠️ 警告 |
| 文档 lint | mdl 报警 | ⚠️ 警告 |
| 工具缺失 | ruff 未安装 | SKIP+备注 |
| 全部通过 | 无问题 | 评论仅含审查结论 |
