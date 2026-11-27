# bridge_seed_eval 使用说明

本目录用于在客户的 RoboTwin 环境中，通过 `robotwin_bridge` 桥接层调用远端模型，执行**固定种子 / 固定 prompt** 的可复现评测。

- 任务数：50 个任务 × 2 个档位 × 100 条 = 10000 条
- 档位：`clean_seen`（demo_clean / seen）、`random_unseen`（demo_randomized / unseen）
- 每条 case 的 seed、prompt、now_ep_num 都来自缓存，模型之间输入逐字节一致
- 不训练、不生成新种子、不修改基础包

## 1. 目录与前置条件

本目录必须位于桥接层根目录下，且同级要有 `robotwin_cache/`、`start.sh` 等文件：

```text
<ROBOTWIN_ROOT>/
├── robotwin_bridge/                 # 桥接层交付包
│   ├── bridge_seed_eval/            # 本目录
│   │   ├── fixed_bridge_driver.py   # 单任务驱动
│   │   └── run_fixed.sh             # 批次编排
│   ├── robotwin_cache/              # 固定输入缓存（本包已附带）
│   │   ├── CHECKSUMS.json
│   │   ├── evaluation_10000.json
│   │   └── inputs/{clean_seen,random_unseen}.jsonl
│   ├── main.py
│   ├── start.sh
│   └── models/
├── XPolicyLab/policy/CloudRobo_WM/evaluation/   # 基础包（只读）
│   ├── eval_policy_xpolicylab.py
│   ├── fixed_runtime.py
│   ├── fixed_rollout.py
│   └── SDK_BINDING.json
├── scripts/eval_policy_xpolicylab.py            # RoboTwin 官方 client
└── envs/_base_task.py                           # RoboTwin 官方任务基类
```

路径约定（脚本内固定推导，不做目录探测）：

| 变量 | 值 |
|---|---|
| `ROBOTWIN_ROOT` | 必填，来自 `--root`、环境变量或 `fixed_eval.env` |
| `FIXED_EVAL_DIR` | `$ROBOTWIN_ROOT/XPolicyLab/policy/CloudRobo_WM/evaluation` |
| `CACHE_DIR` | `<桥接层根目录>/robotwin_cache` |
| `OUT_ROOT` | `--out-root`，默认 `$ROBOTWIN_ROOT/fixed_out` |

运行依赖：`bash`、`python3`（或 `python`）、`flock`、`fuser`、`timeout`、`sha256sum`（没有时回退 `shasum` / Python）。基础包文件不得修改。

## 2. 快速开始

### 2.1 准备运行环境

设置 RoboTwin 根目录并进入桥接层：

```bash
export ROBOTWIN_ROOT=/path/to/RoboTwin
cd "$ROBOTWIN_ROOT/robotwin_bridge"
```

确认这些已经在机器上（随交付包或客户环境提供，不需要你创建）：

```text
$ROBOTWIN_ROOT/robotwin_bridge/robotwin_cache/              固定输入缓存
$ROBOTWIN_ROOT/XPolicyLab/policy/CloudRobo_WM/evaluation/   基础包
$ROBOTWIN_ROOT/scripts/eval_policy_xpolicylab.py            RoboTwin 官方 client
$ROBOTWIN_ROOT/envs/_base_task.py                           RoboTwin 官方任务基类
```

再准备两个输入文件（只需要一次）：

```bash
# 1) 模型地址文件：一行一个，卡数几张就至少几行
printf '%s\n' 'wss://host-a/' 'wss://host-b/' > /tmp/ws.txt

# 2) 鉴权 token 文件（模型不需要鉴权可跳过）
printf '%s\n' '<your-token>' > /path/to/model.key
chmod 600 /path/to/model.key
```

### 2.2 检查环境

`check` 只做静态检查，不连模型，不需要 `ws.txt` 和 token：

```bash
bash bridge_seed_eval/run_fixed.sh check --root "$ROBOTWIN_ROOT"
```

### 2.3 先跑单档 clean_seen

单档是 50 个任务 × 100 条 = 5000 条。先 dry-run 看计划（不启动进程），再正式跑：

```bash
bash bridge_seed_eval/run_fixed.sh start --root "$ROBOTWIN_ROOT" \
  --profile clean_seen --gpus 0 --dry-run

bash bridge_seed_eval/run_fixed.sh start --root "$ROBOTWIN_ROOT" \
  --profile clean_seen \
  --gpus 0 --port-base 5695 \
  --ws-file /tmp/ws.txt \
  --api-key-file /path/to/model.key \
  --model cloudrobo-wm \
  --model-name cloudrobo-wm-v1 --ckpt /path/to/ckpt --note "baseline"
```

- `--gpus 0`：用 0 号卡；多卡写 `0,1`
- 几张卡就起几个桥接层，端口从 `--port-base` 连续分配
- 模型不需要鉴权时去掉 `--api-key-file`
- `--model` 是真正给桥接层的适配器（影响运行）；`--model-name` / `--ckpt` / `--note` 只是写进 `run.json` 的标签/备注，不影响评测

### 2.4 需要两档一起跑

`--profile clean_seen,random_unseen` 会按顺序跑两档，100 个任务 × 100 条 = 10000 条。追加 `--new-run` 新建批次：

```bash
bash bridge_seed_eval/run_fixed.sh start --root "$ROBOTWIN_ROOT" \
  --profile clean_seen,random_unseen --new-run \
  --gpus 0 --ws-file /tmp/ws.txt \
  --api-key-file /path/to/model.key --model cloudrobo-wm
```

### 2.5 查看进度、续跑、停止

```bash
bash bridge_seed_eval/run_fixed.sh status
bash bridge_seed_eval/run_fixed.sh summary --run-id <run_id>
bash bridge_seed_eval/run_fixed.sh stop --run-id <run_id>
```

续跑要重新提供模型地址和 key（`run.json` 只保存地址的哈希，不保存地址本身）：

```bash
bash bridge_seed_eval/run_fixed.sh resume --run-id <run_id> \
  --root "$ROBOTWIN_ROOT" \
  --gpus 0 \
  --ws-file /tmp/ws.txt \
  --api-key-file /path/to/model.key \
  --model cloudrobo-wm
```

`--gpus` 数量要和原批次一致；`rerun-failed` 同理。

输出目录、`run.json` 字段和自动生成的文件见第 5 节。

### 2.6 单 case 排障

先手动启动桥接层，再用 driver 只跑一条：

```bash
bash start.sh --robotwin-root "$ROBOTWIN_ROOT" \
  --ws-url 'wss://host-a/' --port 5695 \
  --api-key-file /path/to/model.key --model cloudrobo-wm

python3 bridge_seed_eval/fixed_bridge_driver.py \
  --root "$ROBOTWIN_ROOT" --profile clean_seen --task adjust_bottle \
  --port 5695 --output "$ROBOTWIN_ROOT/fixed_out/debug" --single-case 0
```

## 3. 子命令

| 命令 | 说明 |
|---|---|
| `check [--fix]` | 环境、基础包、官方 client/base task、桥接层缓存、端口、模型地址数量检查；`--fix` 只做 BOM/CRLF 无损修复 |
| `start` | 新建或继续一个批次；默认沿用最近一次 run_id |
| `resume --run-id X` | 续跑指定批次（不传则用最近一次）；需重新提供模型地址/key，卡数与原批次一致 |
| `status [--run-id X]` | 显示批次状态与完成数；不传则列出全部批次 |
| `summary [--run-id X]` | 重新生成并打印 `FIXED_SUMMARY_*.json` / `fixed_seed_results_*.csv` |
| `stop [--run-id X]` | 杀掉主进程、worker、桥接层并释放端口 |
| `list` | 列出全部批次 ID |
| `rerun-failed [--run-id X]` | 重跑最近一次执行中记录到 `failed.txt` 的任务；同样需重新提供模型地址/key |

`start` 相关参数：

| 参数 | 说明 |
|---|---|
| `--run-id X` | 指定批次 ID；已存在时按续跑处理（输入指纹需一致） |
| `--new-run` | 新建批次；`--run-id` 已存在会报错 |
| `--force` | 覆盖已有 `trials==100` 的任务 |
| `--dry-run` | 只打印命令，不执行 |
| `--profile a,b` | 档位，逗号分隔，按顺序执行；默认 `clean_seen`（单档 50 任务 / 5000 条，双档 100 任务 / 10000 条） |
| `--gpus 0,1,2` | 指定卡号；默认 `0` |
| `--num-gpus N` | 使用 `0..N-1` |
| `--port-base N` | 起始端口，默认 `5695`；N 卡占用连续 N 个端口 |
| `--task-timeout SEC` | 单任务超时，`0` 表示不限；超时会记入失败清单 |
| `--root PATH` | RoboTwin 根目录 |
| `--out-root PATH` | 输出根目录，默认 `$ROBOTWIN_ROOT/fixed_out` |
| `--ws-file PATH` | 模型地址文件，一行一个 `wss://...`；优先级最高；`check` 可不填 |
| `--api-key-file PATH` | 模型鉴权文件（推荐） |
| `--api-key KEY` | 模型鉴权 token（会出现在命令行，慎用） |
| `--model NAME` | 桥接层模型适配器，**影响运行**，例如 `cloudrobo-wm`；对应桥接层的 `--model` |
| `--log-level LEVEL` | 桥接层日志级别，默认 `INFO` |
| `--insecure` | 关闭桥接层 TLS 校验 |
| `--model-name NAME` | 模型名称标签，**只写入 `run.json`**，不影响运行、不参与续跑指纹；用于日后辨认这是哪个模型/版本 |
| `--ckpt PATH` | 权重路径标签，只写入 `run.json`，不影响运行 |
| `--note TEXT` | 自由备注，只写入 `run.json`，不影响运行 |

注意区分：

- `--model`：传给桥接层，决定用哪个模型适配器，**影响运行**。
- `--model-name`：只是写进 `run.json` 的名字，**不影响运行**，用于日后追溯“这是哪个模型/版本跑的”。

## 4. 配置：环境变量与 fixed_eval.env

除 `--run-id`、`--new-run`、`--force`、`--dry-run`、`--fix` 外，其余参数都可用环境变量提供，优先级为：**命令行 > 环境变量 > `fixed_eval.env`**。

`fixed_eval.env` 放在本目录（`bridge_seed_eval/fixed_eval.env`），使用 bash 语法：

```bash
ROBOTWIN_ROOT=/opt/RoboTwin
OUT_ROOT=/opt/RoboTwin/fixed_out
PORT_BASE=5695
TASK_TIMEOUT=0
PROFILE=clean_seen,random_unseen
GPUS=(0 1)
NUM_GPUS=2
API_KEY_FILE=/secure/model.key
BRIDGE_MODEL=cloudrobo-wm
BRIDGE_LOG_LEVEL=INFO
MODEL_NAME=cloudrobo-wm-v1
CKPT=/models/ckpt.pt
NOTE=baseline

# 模型地址二选一：
# 1) 数组形式（本文件内推荐）
MODEL_WS=("wss://host-a/" "wss://host-b/")
# 2) 普通字符串：只按空格拆分，不认逗号
# MODEL_WS="wss://host-a/ wss://host-b/"
```

模型地址优先级：`--ws-file`（也可用环境变量或 `fixed_eval.env` 的 `WS_FILE`）> `MODEL_WS`（环境变量 > `fixed_eval.env`）。`--ws-file` 每行一个地址，空行忽略，不支持注释。正式 `start`/`resume` 时地址数量必须不少于卡数；`check` 不连接模型，未提供时只提示，`--ws-file` 指向不存在的文件才会报错。

`resume` / `rerun-failed` 不会从 `run.json` 读取真实地址（只存哈希），所以建议把 `MODEL_WS`、`API_KEY_FILE`、`BRIDGE_MODEL` 写进 `fixed_eval.env`，续跑时自动读取，不用每次手敲。

鉴权优先级：`API_KEY_FILE` > `API_KEY` > `MODEL_API_KEY`。仓库内不要写明文密钥。

环境变量与参数对应关系：

| 环境变量 | 对应参数 |
|---|---|
| `ROBOTWIN_ROOT` | `--root` |
| `OUT_ROOT` | `--out-root` |
| `PROFILE` | `--profile` |
| `GPUS` / `NUM_GPUS` | `--gpus` / `--num-gpus` |
| `PORT_BASE` | `--port-base` |
| `TASK_TIMEOUT` | `--task-timeout` |
| `WS_FILE` | `--ws-file` |
| `MODEL_WS` | 无（模型地址数组/空格分隔字符串） |
| `API_KEY_FILE` / `API_KEY` | `--api-key-file` / `--api-key` |
| `MODEL_API_KEY` | 桥接层读取的 token（优先级最低） |
| `BRIDGE_MODEL` | `--model` |
| `BRIDGE_LOG_LEVEL` | `--log-level` |
| `BRIDGE_INSECURE` | `--insecure`（`1` 开启） |
| `MODEL_NAME` / `CKPT` / `NOTE` | `--model-name` / `--ckpt` / `--note` |

其他可调环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `BRIDGE_WAIT` | `120` | 等待桥接层输出 `policy server ready` 的秒数 |

## 5. 批次目录与结果

```text
$OUT_ROOT/runs/<run_id>/
├── run.json
├── .run/<时间戳>/
│   ├── queue.txt
│   ├── failed.txt
│   ├── current
│   └── logs/
│       ├── bridge_<i>.log
│       └── worker_<i>.log
├── clean_seen/<task>/
│   ├── fixed_cases.jsonl
│   └── FIXED_RESULT.json
└── random_unseen/<task>/...
```

`summary` 会在 `runs/<run_id>/` 下生成：

- `FIXED_SUMMARY_<profile>.json`
- `fixed_seed_results_<profile>.csv`

`run.json` 主要字段：`run_id / status / profiles / gpus / model_ws_sha256 / cache_manifest_sha256 / sdk_binding_sha256 / driver_sha256 / model_name / ckpt / note / pid / port_base / started_at / updated_at`。其中 `model_name`、`ckpt`、`note` 是你传入的标签/备注，仅用于记录，不影响运行。

状态：`运行中`、`已中断`、`已完成`、`部分完成`。

## 6. 续跑规则

- 已有 `FIXED_RESULT.json` 且 `trials==100` 的任务默认跳过，`--force` 才覆盖。
- 未完成的任务读取 `fixed_cases.jsonl`（包括改名后的 `<task>.old*`）统计已完成条数与成功数，自动传 `--resume-from` / `--prev-success` 续跑。
- 续跑会先把旧任务目录整体改名为 `<task>.old`（再次续跑为 `<task>.old.N`），不删除旧结果；非续跑模式会清空该任务目录。
- 输入指纹不一致时拒绝续跑：`profiles`、卡数、`cache_manifest_sha256`、`sdk_binding_sha256`。模型地址只记录、不参与指纹；改动档位/卡数/缓存/基础包后请加 `--new-run`。
- `resume` / `rerun-failed` 需要重新提供模型地址、key 和 `--model`，并保持卡数一致；真实地址不会写入 `run.json`。

## 7. 环境检查（check）

`check` 会验证：

- `ROBOTWIN_ROOT`、Python、`flock/fuser/timeout`
- 基础包四个文件存在（`eval_policy_xpolicylab.py`、`fixed_runtime.py`、`fixed_rollout.py`、`SDK_BINDING.json`）
- 官方 `scripts/eval_policy_xpolicylab.py`、`envs/_base_task.py` 的 sha256、BOM，与 `SDK_BINDING.json` 一致
- 缓存 `robotwin_cache/`：`CHECKSUMS.json` 中每个文件的 sha256，包括 `evaluation_10000.json`
- `MODEL_WS` 数量 >= 卡数；未提供时只提示、不判失败（`check` 不连接模型）
- 端口是否被占用

缓存以桥接层自带的 `robotwin_cache/CHECKSUMS.json` 为唯一基准，**不要求**与 XPolicyLab 下的 `default_cache_manifest_sha256` 一致（官方侧缓存不在本检查范围）。

缓存 sha256 判定为三态：

- 原始 sha256 匹配：通过
- 仅 CRLF 归一化后匹配：缓存文件不允许修改，需重新拷贝正确文件
- 都不匹配：版本不符

`check --fix` 只对官方 client / base task 做 CRLF→LF、去 BOM，修完立即复检；缓存与基础包不做修改。

## 8. 驱动 fixed_bridge_driver.py

一般通过 `run_fixed.sh` 调用；排障时可单独使用。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--profile` | `clean_seen` | `clean_seen` / `random_unseen` |
| `--task` | `all` | 任务名、逗号分隔列表或 `all` |
| `--host` | `127.0.0.1` | 桥接层地址 |
| `--port` | `5695` | 桥接层端口 |
| `--root` | 环境变量 / env 文件 | `ROBOTWIN_ROOT` |
| `--cache` | `<桥接层根目录>/robotwin_cache` | 固定输入缓存 |
| `--eval-dir` | `$ROBOTWIN_ROOT/XPolicyLab/policy/CloudRobo_WM/evaluation` | 基础包目录 |
| `--output` | `$ROBOTWIN_ROOT/fixed_out` | 输出根目录 |
| `--single-case N` | 无 | 只跑第 N 条（0..99），用于排障 |
| `--cases 8,12,17` | 无 | 只跑指定序号列表 |
| `--resume-from N` | `0` | 跳过前 N 条 |
| `--prev-success S` | `0` | 前段已成功数，并入最终统计 |
| `--parallel N` | `1` | 多进程并行跑多个任务 |

驱动会按绝对路径加载 `eval_policy_xpolicylab.py`，设置 `sys.argv`、注入 `test_render` stub，并通过 `CLOUDROBO_*` 环境变量把路径传给 wrapper。执行任务前会切到 `--root`（RoboTwin 使用相对 `assets/...` 路径），并把 `--eval-dir` 临时加入 `sys.path`；任务结束后恢复 `cwd`、`sys.path`、`os.environ` 与 `sys.argv`。单 case / 批量 case 模式会拒绝覆盖已存在的 `trials==100` 结果，请使用独立 `--output`。

## 9. 不依赖 RoboTwin 的自检

```bash
cd <ROBOTWIN_ROOT>/robotwin_bridge

bash -n bridge_seed_eval/run_fixed.sh
python3 -m py_compile bridge_seed_eval/fixed_bridge_driver.py

python3 bridge_seed_eval/fixed_bridge_driver.py --help
bash bridge_seed_eval/run_fixed.sh --help
bash bridge_seed_eval/run_fixed.sh list

# 缓存完整性（以桥接层 robotwin_cache/CHECKSUMS.json 为准）
python3 - <<'PY'
import hashlib, json
m = json.load(open("robotwin_cache/evaluation_10000.json", encoding="utf-8"))
assert m["total_episodes"] == 10000
assert len(m["task_names"]) == 50 == len(set(m["task_names"]))
assert {p["id"] for p in m["profiles"]} == {"clean_seen", "random_unseen"}
print("manifest OK", hashlib.sha256(open("robotwin_cache/evaluation_10000.json", "rb").read()).hexdigest())
PY
```

## 10. 常见问题

| 现象 | 处理 |
|---|---|
| `缺少缓存清单` | 确认 `robotwin_cache/evaluation_10000.json` 存在；缓存应包含 `CHECKSUMS.json` 与 `inputs/` |
| `端口被占用` | 换 `--port-base`，或先 `stop` 旧批次 / 释放端口 |
| `桥接层端口 ... 未就绪` | 查看 `.run/<stamp>/logs/bridge_<i>.log`；常见是模型地址不可达、TLS 校验失败（可试 `--insecure`） |
| `MODEL_WS 数量少于卡数` | 减少卡数或补足模型地址 |
| `输入指纹变化` | 档位/卡数/缓存/基础包变了，改用 `--new-run` 新建批次 |
| 任务中断后想继续 | `resume --run-id X`，并重新带 `--ws-file` / `--api-key-file` / `--model`；不要手动删除 `fixed_cases.jsonl` 与 `<task>.old*` |
| `resume` 提示缺少模型地址 | 地址不落盘，重新带 `--ws-file` 或 `MODEL_WS`；也可写进 `fixed_eval.env` |
| `fixed_cases.jsonl` 已存在 | 非续跑模式会清空任务目录，续跑模式会自动改名备份 |
| 只跑一个 case 却覆盖了正式结果 | 单 case/批量 case 请用独立 `--output` |
| 模型地址含逗号 | `MODEL_WS` 环境变量只按空格拆分；用 `--ws-file` 或 env 文件数组 |
