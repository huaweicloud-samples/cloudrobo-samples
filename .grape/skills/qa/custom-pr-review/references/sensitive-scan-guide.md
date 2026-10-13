# 敏感词校验指南（sensitive-scan-guide）

> 本指南定义 custom-pr-review 技能的敏感词校验规则：内置词表、IP 正则、词表扩展、diff 行号定位、评论格式。

---

## 1. 内置词表

| 词 | 匹配方式 | 说明 |
|----|---------|------|
| `huawei.com` | 子串匹配，大小写不敏感 | 华为内部域名 |
| `codehub` | 子串匹配，大小写不敏感 | CodeHub 内部平台 |
| `myhuaweicloud.com` | 子串匹配，大小写不敏感 | 华为云内部域名 |

- 匹配方式：对每行文本做 `grep -i` 式子串查找（不要求全词匹配）。
- 命中记录：`文件路径:行号:命中词`。

## 2. IP 正则

### IPv4
```
\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b
```
- 匹配 `0.0.0.0` ~ `255.255.255.255` 格式（不做范围校验，由审查者判断）。
- 含公网/内网地址（`10.x` `172.16-31.x` `192.168.x` `127.x` 等）。
- 版本号如 `1.2.3.4` 也会命中——报告标注「疑似 IP」，审查者自行判断。

### IPv6（简化全格式）
```
\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b
```
- 匹配完整格式的 IPv6 地址（如 `2001:0db8:85a3:0000:0000:8a2e:0370:7334`）。
- 缩写格式（如 `::1`）不匹配——简化实现，减少误报。

### Bash 实现示例
```bash
# IPv4 扫描
echo "$LINE" | grep -oE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b'

# IPv6 扫描
echo "$LINE" | grep -oE '\b([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b'

# 内置词表扫描
for word in huawei.com codehub myhuaweicloud.com; do
  echo "$LINE" | grep -qi "$word" && echo "$word"
done
```

## 3. 词表扩展

### 仓库词表文件
- 路径：`docs/sensitive-words.txt`
- 格式：每行一个敏感词，`#` 开头为注释，空行跳过。
- 合并方式：读取文件内容，逐行追加到内置词表（去重）。
- 文件不存在时：仅用内置词表（不报错）。

### 读取命令
```bash
grape git-repo contents --owner "$REPO_OWNER" --repo "$REPO_NAME" --path "docs/sensitive-words.txt" --raw 2>/dev/null || true
```

### 词表文件示例
```
# 自定义敏感词表
internal.example.com
secret.local
10.20.30.40
```

## 4. diff 行号定位

### unified diff 格式解析
```
@@ -old_start,old_lines +new_start,new_lines @@ context
+新增行内容
 上下文行
-删除行内容
```

- **新增行**：以 `+` 开头（非 `+++` 文件头），行号 = `new_start` + 后续偏移。
- **上下文行**：以空格开头，行号递增。
- **删除行**：以 `-` 开头（非 `---` 文件头），不影响新行号。

### 行号计算逻辑
1. 从 `@@ +new_start,new_lines @@` 提取 `new_start`。
2. 遍历 patch 行：
   - `+` 开头（非 `+++`）→ 当前行号 = `new_start`，命中则记录；`new_start` 不递增（新增行不占原文件行号，但占新文件行号 → 实际 `new_start` 应递增）。
   - 空格开头 → `new_start++`。
   - `-` 开头（非 `---`）→ 不递增 `new_start`。

### Bash 实现示例
```bash
echo "$PATCH" | awk '
/^@@ / {
  match($0, /\+([0-9]+)/, arr)
  lineno = arr[1] + 0
  next
}
/^\+[^+]/ {
  line = substr($0, 2)
  # 在此扫描 line 的敏感词，lineno 为新文件行号
  printf "%d:%s\n", lineno, line
  lineno++
}
/^ / {
  lineno++
}
'
```

## 5. 评论格式

### 命中时（命中数 > 0）
在检视评论末尾附加：
```markdown
---
### 敏感词校验结果

| 文件 | 行号 | 命中词 |
|------|------|--------|
| src/config.yaml | 42 | huawei.com |
| src/config.yaml | 87 | 10.0.0.1 |
| docs/guide.md | 15 | myhuaweicloud.com |

> ⚠️ 以上为疑似敏感信息命中，仅提示不阻断合入。请审查者确认是否需要脱敏处理。
```

### 未命中时（命中数 = 0）
- **不附加**敏感词校验段落。
- 原审查结论与评论格式完全不变。

## 6. 测试覆盖要求

| 场景 | 输入 | 预期 |
|------|------|------|
| 命中内部域名 | diff 含 `huawei.com` | 评论列出文件+行号 |
| 命中 IP | diff 含 `10.0.0.1` | 评论列出文件+行号 |
| 未命中 | diff 无敏感词 | 评论不含敏感词段落 |
| 词表扩展 | `docs/sensitive-words.txt` 含自定义词 | 自定义词被扫描命中 |
| 多文件多命中 | 多文件多行命中 | 全部列出 |
