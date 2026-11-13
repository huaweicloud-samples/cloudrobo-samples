# robotwin_bridge 使用说明

> RoboTwin 2.0 评测桥接层：在 RoboTwin 2.0 官方环境（XPolicyLab WS 协议）中评测时，将官方评测脚本与华为云远端世界模型（cr-wam 协议，`wss://`）打通。桥接层面向官方脚本暴露本地 `ws://` 端口，对远端模型走 `wss://` 加密通道，并完成 obs/state 与 14 维动作的格式翻译。评测方无需改动任何官方代码。

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

我们除了提供桥接层程序共 8 个文件（另有本使用说明）之外，还额外提供种子文件可用于重现我们的榜单成绩：

```text
robotwin/
├──robotwin_bridge/
│  ├── main.py                 入口 (解析参数, 生命周期; SIGTERM 优雅退出)
│  ├── server.py               XPolicyLab PolicyServer 桥接 (后台线程, 按模型 protocol 自动路由)
│  ├── models/                模型适配层 (可插拔)
│  │   ├── __init__.py        注册表: get_model/按模型 metadata.protocol 自动路由
│  │   └── crwam.py           格式翻译 (cr-wam 协议, 相机映射/uint8/14维joint)
│  ├── policy_client.py        cr-wam 推理客户端 (action queue)
│  ├── ws_client.py            wss(TLS1.2指定cipher)+Bearer token+msgpack; TLS 三态(见 §5)
│  ├── requirements.txt        桥接层自身依赖 (仅 websockets/msgpack/numpy)
│  ├── start.sh                启动脚本 (自动组装 PYTHONPATH, 参数缺失校验)
│──└──robotwin_cache/        
│     ├──clean_seen.jsonl     clean类型种子文件
└─────└──random_seen.jsonl    random类型种子文件
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

Token 三选一（生产推荐 ①，优先级恒为 ① > ② > ③）：

| 方式 | 用法 | 安全性 |
|---|---|---|
| ① 文件（推荐） | `--api-key-file /path/keyfile`（文件 `chmod 600`） | 只在进程内存, 不落 ps/env, 受 OS 权限保护 |
| ② 环境变量 | `export MODEL_API_KEY=<token>` | 同级任意进程可经 `/proc/<pid>/environ` 读取 |
| ③ 命令行 | `--api-key <token>` | `ps aux` 对**所有用户**可见（不建议生产） |

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

**全部 50 个任务（官方 multitask 入口，无需写循环）**：RoboTwin 官方提供 `scripts/eval_policy.sh` 统一评测入口（2026-08 起），`multitask` 子命令读取 `env_cfg/eval/all_tasks.yml`（官方维护的全部 50 个任务与 GPU 设置），一次跑完并多任务/多 GPU 调度；桥接层本身就是预启动的 XPolicyLab policy server，配合官方 remote 模式直连即可：

```bash
conda activate <robotwin_env>
cd $ROBOTWIN_ROOT
bash scripts/eval_policy.sh multitask \
  --config env_cfg/eval/all_tasks.yml \
  --task-config demo_clean \
  --env-cfg-type arx_x5 \
  --eval-env-conda-env <robotwin_env> \
  --action-type joint \
  --enable-remote \
  --policy-server-ip 127.0.0.1 --policy-server-port 5695
```

**任务与配置说明**

- 任务全集由 `env_cfg/eval/all_tasks.yml` 的 `tasks` 字段决定（默认全部 50 个）；只评单个任务时把该文件 `tasks` 裁成一项即可
- **任务配置有两类**：`demo_clean`（默认，干净场景）与 `demo_randomized`（随机化场景，物体/纹理/光照更强泛化挑战）；改 `--task-config demo_randomized` 再执行一次即可跑另一类，或在 yml 的 `tasks` 条目上按任务单独覆写
- `--enable-remote` 表示直连**已预启动**的远端 policy server（即桥接层），本地只跑 SAPIEN 仿真客户端，无需 `--ckpt-name` / `--policy-conda-env` / `--policy-name` 本地适配器

**并发路数怎么算（关键）**

实际并发路数 = `min(GPU 预算, 端点数)`：

- **GPU 预算** = `len(gpu_ids) × jobs_per_gpu`。官方 yml 默认 `gpu_ids: "0-7"`（8 卡）`jobs_per_gpu: 1` → 预算 8；按客户实际 GPU 数在 yml 中调整（如 4 卡改 `gpu_ids: "0-3"` → 预算 4）
- **端点数** = 命令行 `--policy-server-ip/--policy-server-port` 传了几对（`--policy-server-ip` 是 append 参数，可重复）。**CLI 传了端点会整组替换 yml 里的 `policy_server_ip/policy_server_port`，不叠加**；不传则回落到 yml 配置（官方默认 4 个端口 `[18080..18083]`）

| 传法 | 端点数 | GPU 预算 | 实际并发 | 50 任务走向 |
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
| 官方脚本 | `[CONNECTED]` → `Success! n / m` |
| 最终结果 | `Final success rate: x/y = z%` |
| 视频产物 | `$ROBOTWIN_ROOT/eval_result/<task>/bridge/<cfg>/<ckpt>/<time>/episode*.mp4` |

## 9. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 桥接层连接模型 504 / `proxy rejected connection` | 走代理访问内网模型地址 | `export no_proxy` 含内网地址后重启 |
| 官方脚本连本地 5695 报 `InvalidProxy` | 代理变量残留 | 同样设置 `no_proxy` 含 127.0.0.1 |
| 端口占用 | 上次进程未退出 | `pkill -9 -f start.sh; pkill -9 -f main.py` 后重起, 或换 `--port` |
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
| `--api-key` / `MODEL_API_KEY` | 空 | 模型鉴权 token（`--api-key` 在进程列表可见，env 会被同级进程读取） |
| `--camera-map XPL=REMOTE` | 默认映射 | 相机键映射，可多次 |
| `--n-action-steps` | chunk 全用 | 每次推理消费步数 |
| `--model` | auto | 模型适配器（默认按模型 metadata.protocol 自动路由；可强制指定，如 `cr-wam`） |
| `--insecure` | 关 | **不校验**模型证书（仍为 TLS 加密，但无身份验证；仅限受控内网使用） |
| `--cacert CA_FILE` | 无 | 私有 CA 证书路径，用于严格校验模型证书 |