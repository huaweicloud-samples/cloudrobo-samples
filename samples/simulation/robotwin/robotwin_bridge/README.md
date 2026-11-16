# robotwin_bridge 使用说明

> **成熟度：runnable** — 本示例可直接运行，按文档步骤操作即可完成 RoboTwin 2.0 评测接入。

> RoboTwin 2.0 评测桥接层：在 RoboTwin 2.0 官方环境中评测时，将官方评测脚本与华为云 CloudRobo 远端世界模型打通。桥接层面向官方脚本暴露本地 ws 端口，对远端模型走 wss 加密通道，并完成 obs/state 与动作的格式转换。评测方无需改动任何官方代码。

## 解决的问题

RoboTwin 2.0 官方评测脚本使用 WS，华为云 CloudRobo 远端世界模型使用 WSS。桥接层在中间完成两者之间的通信打通与格式转换，评测方**无需改动任何官方代码**即可完成评测。

## 预计耗时

全量评测耗时取决于任务数与 GPU 数。

## 适用平台版本

- RoboTwin 2.0（含 XPolicyLab submodule）
- Python 3.10
- 操作系统：Linux

## 硬件 / 软件前置条件

- **硬件**：GPU（仿真评测需要）、可访问模型服务的网络
- **软件**：RoboTwin 2.0 官方环境（含 sapien、curobo 等，按官方 INSTALLATION 安装）、模型服务端 wss 地址及 token
- 详见 [§2 前置要求](#2-前置要求评测方自备)

## 最终效果

官方评测脚本成功连接桥接层，完成指定任务评测，输出 `Final success rate: x/y = z%` 及 episode 视频，产物路径见 §8。

## 1. 桥接层功能

| 功能 | 说明 | 实现 |
|---|---|---|
| WS ↔ WSS | 对官方脚本暴露本地 ws 端口，对模型走 wss 加密通道 | `ws_client.py` |
| 格式转换 | XPolicyLab obs/state → CloudRobo-WM-7B-Preview obs/state 格式转换 | `models/crwam.py` |
| Token 认证 | 连接模型时注入 `Authorization: Bearer <token>` | `ws_client.py` + `main.py` |

## 2. 前置要求（评测方自备）

- Python 3.10 + RoboTwin 2.0 官方环境（含 XPolicyLab submodule、sapien、curobo 等，按官方 INSTALLATION 安装）：
  ```bash
  git clone --recurse-submodules https://github.com/RoboTwin-Platform/RoboTwin.git
  ```
- 评测资产已解压到 `$ROBOTWIN_ROOT/assets/`（background_texture/embodiments/objects）
- 模型服务端 wss 地址及 token（模型侧鉴权必填）

## 3. 交付物

我们除了提供桥接层程序之外，还额外提供缓存文件可用于重现我们的榜单成绩：

````text
robotwin/
└── robotwin_bridge/
    ├── README.md               使用说明
    ├── main.py                 入口
    ├── server.py               服务端桥接
    ├── policy_client.py        推理客户端
    ├── ws_client.py            WS/WSS 客户端
    ├── requirements.txt        依赖清单
    ├── start.sh                启动脚本
    ├── models/                 模型适配层
    │   ├── __init__.py         注册表
    │   └── crwam.py            格式转换
    └── robotwin_cache/
        ├── clean_seen.jsonl    clean 缓存文件
        └── random_unseen.jsonl random 缓存文件
````

## 4. 部署步骤

```bash
# 4.1 放置交付包 (建议放在 RoboTwin 根目录下, 与官方代码同级)
cd $ROBOTWIN_ROOT
# 将 robotwin_bridge/ 目录整体放入 $ROBOTWIN_ROOT/, 然后:
pip install -r $ROBOTWIN_ROOT/robotwin_bridge/requirements.txt
# 注: 若 RoboTwin 环境已含 websockets/msgpack/numpy, 可跳过此步
# 注: robotwin_bridge/ 是独立交付包, 不要提交到 RoboTwin 官方 git 仓库;
#     若 RoboTwin 是 git clone 的, 建议把 robotwin_bridge/ 加进 .gitignore
chmod +x $ROBOTWIN_ROOT/robotwin_bridge/start.sh

# 4.2 代理豁免: 评测与本机/模型链路均为内网直连, 默认无需操作
# (仅当评测机配置了 HTTP 代理且连接报 504 / InvalidProxy 时, 才补下述豁免:
export no_proxy="localhost,127.0.0.1,<MODEL_HOST>"
export NO_PROXY="$no_proxy"

# 4.3 启动桥接层 (监听本地 ws 端口, 等待官方脚本连接)
获取 `<模型服务 wss 地址>` 与 API Key：

- wss 地址：登录CloudRobo平台，进入模型部署，在对应模型部署详情界面获取。
- API Key（按类型二选一）：
  - 公共池：在部署详情页获取该服务的 API Key；
  - 专属池：使用客户自己的 API Key（专属池服务绑定客户自有密钥）。

API Key 属于敏感凭据：不要提交进仓库、工单或聊天记录。将其保存为 0600 权限文件供桥接层读取：

mkdir -p ~/.config/robotwin_bridge && umask 077
printf '%s\n' '<APIKey>' > ~/.config/robotwin_bridge/api_key
chmod 600 ~/.config/robotwin_bridge/api_key

cd $ROBOTWIN_ROOT/robotwin_bridge && ./start.sh \
  --ws-url <模型服务 wss 地址> \
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

```bash
# 推荐做法
mkdir -p ~/.config/robotwin_bridge && umask 077
printf '%s\n' 'YOUR_API_KEY' > ~/.config/robotwin_bridge/api_key && chmod 0600 ~/.config/robotwin_bridge/api_key
```

## 5. TLS 校验模式

桥接层对模型走 wss，默认**严格校验**模型证书；三种模式仅影响证书身份验证，均为 TLS 加密通道：

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

**全部任务（官方 multitask 入口，无需写循环）**：RoboTwin 官方提供 `scripts/eval_policy.sh` 统一评测入口（2026-08 起），`multitask` 子命令读取 `env_cfg/eval/all_tasks.yml`（官方维护的任务与 GPU 设置，截至本文档编写时为 50 个任务，以该 yml 实际内容为准），一次跑完并多任务/多 GPU 调度；桥接层本身就是预启动的 XPolicyLab policy server，配合官方 remote 模式直连即可：

```bash
conda activate <robotwin_env>
cd $ROBOTWIN_ROOT
bash scripts/eval_policy.sh multitask \
  --config env_cfg/eval/all_tasks.yml \
  --task-config demo_clean \
  --env-cfg-type arx_x5 \
  --eval-env-conda-env <robotwin_env> \
  --action-type joint \
  --policy-name CloudRobo_WM \
  --enable-remote \
  --policy-server-ip 127.0.0.1 --policy-server-port 5695
```

**任务与配置说明**

- 任务全集由 `env_cfg/eval/all_tasks.yml` 的 `tasks` 字段决定（截至本文档编写时为 50 个，以该 yml 实际内容为准）；只评单个任务时把该文件 `tasks` 裁成一项即可
- **任务配置有两类**：`demo_clean`（默认，干净场景）与 `demo_randomized`（随机化场景，物体/纹理/光照更强泛化挑战）；改 `--task-config demo_randomized` 再执行一次即可跑另一类，或在 yml 的 `tasks` 条目上按任务单独覆写
- `--enable-remote` 表示直连**已预启动**的远端 policy server（即桥接层），本地只跑 SAPIEN 仿真客户端；`--ckpt-name` / `--policy-conda-env` 可省，`--policy-name` 仍必填（填 `CloudRobo_WM`，对应 `XPolicyLab/policy/` 下的客户端适配器目录，remote 模式下仅用于选择客户端启动脚本，不会绕开桥接层）

**并发路数怎么算（关键）**

实际并发路数 = `min(GPU 预算, 端点数)`：

- **GPU 预算** = `len(gpu_ids) × jobs_per_gpu`。官方 yml 默认 `gpu_ids: "0-7"`（8 卡）`jobs_per_gpu: 1` → 预算 8；按客户实际 GPU 数在 yml 中调整（如 4 卡改 `gpu_ids: "0-3"` → 预算 4）
- **端点数** = 命令行 `--policy-server-ip/--policy-server-port` 传了几对（`--policy-server-ip` 是 append 参数，可重复）。**CLI 传了端点会整组替换 yml 里的 `policy_server_ip/policy_server_port`，不叠加**；不传则回落到 yml 配置（官方默认 4 个端口 `[18080..18083]`）

| 传法 | 端点数 | GPU 预算 | 实际并发 | 任务走向 |
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
  --policy-name CloudRobo_WM \
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
| 桥接层日志 | `Connected to wss://...` + `policy server ready` |
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
| `--ws-url` | 必填 | 远端模型地址（wss） |
| `--port` | 5695 | 桥接层监听端口（官方脚本连接） |
| `--host` | 0.0.0.0 | 监听地址 |
| `--api-key-file KEY_FILE` | 无 | **推荐**：从受保护文件（0600）读 token，优先级最高 |
| `--api-key` | 空 | 命令行传入 token（`ps aux` 对所有用户可见，不建议生产） |
| `MODEL_API_KEY`（环境变量） | 空 | 环境变量传入 token（同级进程可读 `/proc/<pid>/environ`） |
| `--camera-map XPL=REMOTE` | 默认映射 | 相机键映射，可多次 |
| `--model` | auto | 模型适配器（默认按模型 metadata.protocol 自动路由；可强制指定） |
| `--insecure` | 关 | **不校验**模型证书（仍为 TLS 加密，但无身份验证；仅限受控内网使用） |
| `--cacert CA_FILE` | 无 | 私有 CA 证书路径，用于严格校验模型证书 |

> 优先级：`--api-key-file` > `--api-key` > `MODEL_API_KEY`

## 11. 清理方式

评测完成后，停止桥接层进程即可：

```bash
# 停止桥接层进程
pkill -f "robotwin_bridge/main.py"

# 停止官方评测脚本（如有残留）
pkill -f "eval_policy_xpolicylab.py"
```

## 12. 已知限制

- 桥接层依赖 RoboTwin 官方 XPolicyLab 的 `client_server.ws.model_server` 接口，官方接口变更时需同步适配
- 当前仅支持 CloudRobo-WM-7B-Preview 模型，新增模型需在 `models/` 下实现适配器并注册到 `MODEL_REGISTRY`
- TLS 固定 TLS1.2 + 指定 cipher，模型侧需支持 TLS1.2
- 全量评测的并发路数受 GPU 预算和桥接层实例数共同限制（见 §7）
- `--insecure` 仅限受控内网联调，生产环境勿用
