# robotwin_bridge 使用说明

> RoboTwin 2.0 评测桥接层：在 RoboTwin 2.0 官方环境（XPolicyLab WS 协议）中评测时，将官方评测脚本与华为云远端世界模型（cr-wam 协议，`wss://`）打通。桥接层面向官方脚本暴露本地 `ws://` 端口，对远端模型走 `wss://` 加密通道，并完成 obs/state 与 14 维动作的格式翻译。评测方无需改动任何官方代码。

**成熟度：`adaptation-required`**——需要替换 `<MODEL_HOST>` 与模型 token，并自备 RoboTwin 2.0 环境后才能运行。

## 0. 快速概览

| 项目 | 说明 |
|---|---|
| 解决的问题 | 让 RoboTwin 2.0 官方评测脚本无需改动即可调用华为云远端世界模型，完成仿真评测并产出成功率与视频 |
| 预计耗时 | 约 30–60 分钟（不含 RoboTwin 官方环境安装与资产下载）；全任务评测耗时取决于 GPU 数量与任务集，通常数小时起 |
| 适用平台版本 | CloudRobo Embodied AI Platform（仓库 `integration/26.10.0` 基线）；RoboTwin 2.0 官方环境 |
| 硬件前置 | 至少 1 张 NVIDIA GPU（按 RoboTwin 官方要求配置 CUDA/驱动）；≥ 32 GB 内存；≥ 100 GB 可用磁盘（资产、日志与评测视频） |
| 软件前置 | Ubuntu 20.04/22.04、Python 3.10、RoboTwin 2.0（含 XPolicyLab submodule、sapien、curobo）；桥接层仅需 `websockets`/`msgpack`/`numpy` |
| 最终效果 | 官方脚本输出 `[CONNECTED]` → 逐 episode 打印 `Success!`，并汇总 `Success rate: <suc>/<test_num> => …%`、收尾 `Final success rate: x/y = z%`，同时生成 `eval_result/**/episode*.mp4` 视频产物 |

## 1. 桥接层功能

| 功能 | 说明 | 实现 |
|---|---|---|
| WS ↔ WSS | 对官方脚本暴露 `ws://127.0.0.1:<port>`，对模型走 `wss://`（TLS1.2 指定 cipher） | `ws_client.py` |
| 格式转换 | XPolicyLab obs/state → cr-wam（相机键映射、uint8 无 batch、14 维 joint） | `models/crwam.py` |
| Token 认证 | 连接模型时注入 `Authorization: Bearer <token>` | `ws_client.py` + `main.py` |

## 2. 前置要求（评测方自备）

- Python 3.10 + RoboTwin 2.0 官方环境（含 XPolicyLab submodule、sapien、curobo 等，按官方 INSTALLATION 安装）：
  ```bash
  git clone --recurse-submodules https://github.com/RoboTwin-Platform/RoboTwin.git
  ```
- 评测资产已解压到 `$ROBOTWIN_ROOT/assets/`（background_texture/embodiments/objects）
- 模型服务端地址（`wss://`）及 token（模型侧鉴权必填）

## 3. 交付物

除本使用说明外，桥接层程序共 8 个文件，另附 1 个示例元数据文件与 2 个种子文件用于重现榜单成绩：

```text
robotwin_bridge/
├── main.py                 入口 (解析参数、生命周期; SIGTERM 优雅退出)
├── manifest.yaml           示例元数据 (maturity: adaptation-required)
├── server.py               XPolicyLab PolicyServer 桥接 (后台线程, 按模型 protocol 自动路由)
├── models/                 模型适配层 (可插拔)
│   ├── __init__.py         注册表: get_model/按模型 metadata.protocol 自动路由
│   └── crwam.py            格式翻译 (cr-wam 协议, 相机映射/uint8/14维joint)
├── policy_client.py        cr-wam 推理客户端 (action queue)
├── ws_client.py            wss(TLS1.2指定cipher)+Bearer token+msgpack; TLS 三态(见 §5)
├── requirements.txt        桥接层自身依赖 (仅 websockets/msgpack/numpy)
├── start.sh                启动脚本 (自动组装 PYTHONPATH, 参数缺失校验)
└── robotwin_cache/         种子文件
    ├── clean_seen.jsonl    clean 类型种子文件
    └── random_unseen.jsonl random 类型种子文件
```


## 4. 部署步骤

```bash
# 4.1 放置交付包 (任意可写目录, 建议独立)
mkdir -p ~/robotwin_bridge
# 将 robotwin_bridge/ 下文件放入 ~/robotwin_bridge/, 然后:
pip install -r ~/robotwin_bridge/requirements.txt
chmod +x ~/robotwin_bridge/start.sh
# 注: scp/上传/zip 解压会丢失可执行位, 故显式授权; tar 分发(保留权限位)则无需此步

# 4.2 代理豁免: 评测与本机/模型链路均为内网直连, 默认无需操作
# (仅当评测机配置了 HTTP 代理且连接报 504 / InvalidProxy 时, 才补下述豁免:
export no_proxy="localhost,127.0.0.1,<MODEL_HOST>"
export NO_PROXY="$no_proxy"

# 4.3 启动桥接层 (监听 ws://0.0.0.0:5695, 等待官方脚本连接)
cd ~/robotwin_bridge && ./start.sh \
  --ws-url wss://<MODEL_HOST>:8000/ \
  --port 5695 \
  --robotwin-root $ROBOTWIN_ROOT \
  --api-key-file ~/.config/robotwin_bridge/api_key
# 期望: "[BridgeServer] policy server ready at ws://127.0.0.1:5695"
```

Token 三选一（生产推荐 ①，实际优先级 ① > ② > ③，与 `main.py` 实现一致）：

| 方式 | 用法 | 安全性 |
|---|---|---|
| ① 文件（推荐） | `--api-key-file /path/keyfile`（文件 `chmod 600`） | 只在进程内存, 不落 ps/env, 受 OS 权限保护 |
| ② 命令行 | `--api-key <token>` | `ps aux` 对**所有用户**可见（不建议生产） |
| ③ 环境变量 | `export MODEL_API_KEY=<token>` | 同级任意进程可经 `/proc/<pid>/environ` 读取 |

> key 文件内容会自动 `strip()`（容忍末尾换行）；若文件对非 owner 可读（非 0600），启动时打 Warning 提示收紧。

```bash
# 推荐做法
mkdir -p ~/.config/robotwin_bridge && umask 077
printf '%s\n' 'YOUR_API_KEY' > ~/.config/robotwin_bridge/api_key && chmod 0600 ~/.config/robotwin_bridge/api_key
```

## 5. TLS 三态（wss 证书校验模式）

桥接层对模型走 `wss://`，默认**严格校验**模型证书；三种模式仅影响证书身份验证，均为 TLS 加密通道（AES-GCM/ChaCha20 对称加密 + ECDHE 密钥协商）：

| 场景 | 参数 | 说明 |
|---|---|---|
| 模型证书由公开/系统可信 CA 签发 | （什么都不加） | 严格模式，校验系统可信 CA 与 hostname |
| 模型证书由私有 CA 签发 | `--cacert /path/ca.pem` | 加载私有 CA 后严格校验 |
| 受控内网、证书无法校验的联调环境 | `--insecure` | 跳过身份验证（仍是加密，但有中间人风险）；生产/公网切勿使用 |

> 判断指引：`--insecure` 只应在「受控内网 + 证书/域名与 CA 均不可对齐」的联调期使用；其余一律优先 `--cacert` 或默认严格模式。

## 6. （每台机器首次必做）生成 curobo 配置

> RoboTwin 的 curobo 配置含本机绝对路径，每台评测机首次运行前需生成一次；否则评测会报 `'Robot' object has no attribute 'left_planner'`。

```bash
conda activate <robotwin_env>  # RoboTwin 官方环境名 (按实际安装命名, 下同)
python $ROBOTWIN_ROOT/scripts/update_embodiment_config_path.py
```

## 7. 运行评测（不改任何官方代码）

**单任务验证**（先确认链路通）：

```bash
# 另开终端: 使用 RoboTwin 的 conda 环境
conda activate <robotwin_env>
cd $ROBOTWIN_ROOT
python scripts/eval_policy_xpolicylab.py \
  --protocol ws --host 127.0.0.1 --port 5695 \
  --task_name adjust_bottle --task_config demo_clean \
  --test_num 5 --policy_name bridge --root_dir $ROBOTWIN_ROOT
```

**全部任务（官方 multitask 入口，无需写循环）**：RoboTwin 官方提供 `scripts/eval_policy.sh` 统一评测入口（2026-08 起），`multitask` 子命令读取 `env_cfg/eval/all_tasks.yml`（官方维护的全部任务与 GPU 设置），一次跑完并多任务/多 GPU 调度；桥接层本身就是预启动的 XPolicyLab policy server，配合官方 remote 模式直连即可：

```bash
conda activate <robotwin_env>
cd $ROBOTWIN_ROOT
bash scripts/eval_policy.sh multitask \
  --config env_cfg/eval/all_tasks.yml \
  --task-config demo_clean \
  --env-cfg-type arx_x5 \
  --eval-env-conda-env <robotwin_env> \
  --action-type joint \
  --policy-name bridge \
  --enable-remote \
  --policy-server-ip 127.0.0.1 --policy-server-port 5695
```

**任务与配置说明**

- 任务全集由 `env_cfg/eval/all_tasks.yml` 的 `tasks` 字段决定（以该文件为准，不在文档中写死数量）；只评单个任务时把该文件 `tasks` 裁成一项即可
- **任务配置有两类**：`demo_clean`（默认，干净场景）与 `demo_randomized`（随机化场景，物体/纹理/光照更强泛化挑战）；改 `--task-config demo_randomized` 再执行一次即可跑另一类，或在 yml 的 `tasks` 条目上按任务单独覆写
- `--enable-remote` 表示直连**已预启动**的远端 policy server（即桥接层），本地只跑 SAPIEN 仿真客户端，无需 `--ckpt-name` / `--policy-conda-env` 本地适配器；`--policy-name` 仍为必填参数（上游 `scripts/eval_policy_multitask.py` 中 `required=True`，与是否 `--enable-remote` 无关，用作结果标签），示例统一取 `bridge`

**并发路数怎么算（关键）**

实际并发路数 = `min(GPU 预算, 端点数)`：

- **GPU 预算** = `len(gpu_ids) × jobs_per_gpu`。官方 yml 默认 `gpu_ids: "0-7"`（8 卡）`jobs_per_gpu: 1` → 预算 8；按客户实际 GPU 数在 yml 中调整（如 4 卡改 `gpu_ids: "0-3"` → 预算 4）
- **端点数** = 命令行 `--policy-server-ip/--policy-server-port` 传了几对（`--policy-server-ip` 是 append 参数，可重复）。**CLI 传了端点会整组替换 yml 里的 `policy_server_ip/policy_server_port`，不叠加**；不传则回落到 yml 配置（官方默认 4 个端口 `[18080..18083]`）

| 传法 | 端点数 | GPU 预算 | 实际并发 | 全部任务走向 |
|---|---|---|---|---|
| 传 1 对（如上例 5695） | 1 | 8 | min(8,1)=**1** | 全部串行走 5695 |
| 传 4 对（5695~5698） | 4 | 8 | min(8,4)=**4** | 4 路并发轮询 |
| 传 8 对（5695~5702） | 8 | 8 | min(8,8)=**8** | 跑满 8 路 |
| 传 10 对 | 10 | 8 | min(8,10)=**8** | 被 GPU 预算卡住，需调大 gpu_ids |

**多实例并行写法**（8 路为例）：先各起 1 个桥接层实例监听 5695~5702（每个连一个模型服务），再传 8 对端点组成 server 池：

```bash
bash scripts/eval_policy.sh multitask \
  --config env_cfg/eval/all_tasks.yml --task-config demo_clean \
  --env-cfg-type arx_x5 --eval-env-conda-env <robotwin_env> --action-type joint \
  --policy-name bridge \
  --enable-remote \
  --policy-server-ip 127.0.0.1 --policy-server-port 5695 \
  --policy-server-ip 127.0.0.1 --policy-server-port 5696 \
  --policy-server-ip 127.0.0.1 --policy-server-port 5697 \
  --policy-server-ip 127.0.0.1 --policy-server-port 5698 \
  --policy-server-ip 127.0.0.1 --policy-server-port 5699 \
  --policy-server-ip 127.0.0.1 --policy-server-port 5700 \
  --policy-server-ip 127.0.0.1 --policy-server-port 5701 \
  --policy-server-ip 127.0.0.1 --policy-server-port 5702
```

> 注意：每个端口必须有桥接层实例在监听才有效；只写端口不启实例，对应任务会连接失败。端点重复会报 `duplicate endpoint`。

## 8. 验收标准

| 检查点 | 判定 |
|---|---|
| 桥接层日志 | `Connected to wss://<MODEL_HOST>` + `policy server ready` |
| 官方脚本 | `[CONNECTED]` → 逐 episode 打印 `Success!` |
| 最终结果 | `Final success rate: x/y = z%` |
| 视频产物 | `$ROBOTWIN_ROOT/eval_result/<task>/bridge/<cfg>/<ckpt>/<time>/episode*.mp4` |

## 9. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 桥接层连接模型 504 / `proxy rejected connection` | 走代理访问内网模型地址 | `export no_proxy` 含内网地址后重启 |
| 官方脚本连本地 5695 报 `InvalidProxy` | 代理变量残留 | 同样设置 `no_proxy` 含 127.0.0.1 |
| 端口占用 | 上次进程未退出 | 按监听端口反查后 `kill <PID>`（如 `ss -lptn 'sport = :5695'`；避免 `pkill -f "main.py"` 这类全命令行匹配误杀同名进程），或换 `--port` |
| `'Robot' object has no attribute 'left_planner'` | curobo 配置路径未生成 | `python scripts/update_embodiment_config_path.py` 后重跑 |
| `module not found: sapien/torch` | 未激活 RoboTwin 环境 | `conda activate RoboTwin` |
| wss 握手失败 `CERTIFICATE_VERIFY_FAILED` | 证书非系统可信 CA / hostname 不符 | 私有 CA 用 `--cacert`；受控内网联调用 `--insecure`（生产勿用） |
| wss 握手失败 `TLSV1_ALERT_PROTOCOL_VERSION` | 服务端要求更高 TLS 版本 | 桥接层固定 TLS1.2+ 指定 cipher，需模型侧支持 TLS1.2 |
| 模型返回 401 | token 未配置 | 必填其一: `--api-key-file` / `--api-key` / `MODEL_API_KEY` |

## 10. 桥接层参数一览（main.py）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--ws-url` | 必填 | 远端模型地址（`wss://...`） |
| `--port` | 5695 | 桥接层监听端口（官方脚本连接） |
| `--host` | 0.0.0.0 | 监听地址 |
| `--api-key-file KEY_FILE` | 无 | **推荐**：从受保护文件（0600）读 token，优先级最高 |
| `--api-key` | 空 | 模型鉴权 token（命令行传入，`ps aux` 对所有用户可见，不建议生产） |
| `MODEL_API_KEY`（环境变量） | 空 | 模型鉴权 token 的环境变量形式（同级进程可读 `/proc/<pid>/environ`） |
| `--camera-map XPL=REMOTE` | 默认映射 | 相机键映射，可多次 |
| `--n-action-steps` | chunk 全用 | 每次推理消费步数 |
| `--model` | auto | 模型适配器（默认按模型 metadata.protocol 自动路由；可强制指定，如 `cr-wam`） |
| `--insecure` | 关 | **不校验**模型证书（仍为 TLS 加密，但无身份验证；仅限受控内网使用） |
| `--cacert CA_FILE` | 无 | 私有 CA 证书路径，用于严格校验模型证书 |
| `--log-level` | `INFO` | 日志级别（如 `DEBUG`/`INFO`/`WARNING`），排障时可调低 |

> Token 优先级（与 `main.py` 实现一致）：`--api-key-file` > `--api-key` > `MODEL_API_KEY`。`--api-key-file` 存在时其余两项被忽略；文件内容会自动 `strip()`，权限非 0600 时启动告警。

## 11. 清理方式

```bash
# 1. 停止桥接层（前台启动用 Ctrl-C；后台启动时按记录的 PID 结束，或按监听端口反查确认为桥接层进程后再 kill）
ss -lptn 'sport = :5695'   # 核对 PID 与进程名
kill <PID>

# 2. 删除 token 文件与交付目录
rm -f ~/.config/robotwin_bridge/api_key
rmdir ~/.config/robotwin_bridge 2>/dev/null || true
rm -rf ~/robotwin_bridge

# 3. 可选：删除评测产物（保留 RoboTwin 环境与 robotwin_cache 种子文件）
rm -rf "$ROBOTWIN_ROOT/eval_result"
```

## 12. 已知限制

- 仅验证 ARX X5 单臂 + 三相机（`cam_head`/`cam_left_wrist`/`cam_right_wrist`）配置，其他本体需通过桥接层 `--camera-map`（§10）与官方 `eval_policy.sh` 的 `--env-cfg-type`（§7，属官方评测入口参数，勿配到桥接层）调整，未做兼容性验证。
- 桥接层依赖 RoboTwin 官方 XPolicyLab WS 协议与 `scripts/eval_policy.sh` 的 remote 模式；官方接口变更可能导致不可用，需同步适配。
- 动作按 14 维 joint 处理，`action_type` 非 `joint` 的模型需新增适配器。
- 模型服务需预先启动，桥接层不做本地推理、不做端点健康检查与自动重试；端口无实例监听时对应任务直接连接失败。
- 仅支持 TLS1.2+ 的 `wss://` 加密通道，模型侧不支持 TLS1.2 时无法握手。
- `robotwin_cache/` 中的 seed 文件用于复现榜单成绩，不构成完整任务集；任务集以 `env_cfg/eval/all_tasks.yml` 为准。
