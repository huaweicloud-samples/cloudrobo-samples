#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DRIVER="$SCRIPT_DIR/fixed_bridge_driver.py"
ENV_FILE="$SCRIPT_DIR/fixed_eval.env"

usage() {
  cat <<'EOF'
用法: bash run_fixed.sh <子命令> [参数]

子命令:
  check [--fix]                 环境与完整性检查, --fix 只做无损修复
  start [--run-id X] [--new-run] [--force] [--dry-run]
                                启动批次
  resume --run-id X             断点续跑指定批次(需重新提供模型地址/key 参数)
  status                        显示批次状态
  summary                       汇总成功率
  stop                          停止批次
  list                          列出全部批次
  rerun-failed                  重跑最近一次失败任务(需重新提供模型地址/key 参数)

通用参数:
  --root PATH                   RoboTwin 根目录
  --profile a,b                 按顺序执行 clean_seen / random_unseen
  --gpus 0,1,2                  指定卡号
  --num-gpus N                  使用 0..N-1 共 N 张卡
  --port-base N                 桥接层起始端口(默认 5695)
  --out-root PATH               输出根目录
  --task-timeout SEC            driver 单任务超时(0 表示不限)
  --ws-file PATH                模型地址文件(一行一个 wss://...), 优先级最高; check 可不填
  --api-key-file PATH           模型鉴权文件(推荐)
  --api-key KEY                 模型鉴权 token(有 ps 可见风险)
  --model NAME                  桥接层模型适配器(影响运行, 如 cloudrobo-wm)
  --log-level LEVEL             桥接层日志级别
  --insecure                    关闭桥接层 TLS 校验
  --model-name NAME             仅记录到 run.json, 便于追溯(不影响评测)
  --ckpt PATH                   仅记录到 run.json(权重路径)
  --note TEXT                   仅记录到 run.json(备注)
EOF
}

log() { printf '%s\n' "$*"; }
warn() { printf '提示: %s\n' "$*" >&2; }
die() { printf '错误: %s\n' "$*" >&2; exit 1; }

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  die "未找到 python3/python"
fi

declare -A PRE_DECL=()
capture_env() {
  local name
  for name in "$@"; do
    if declare -p "$name" >/dev/null 2>&1; then
      PRE_DECL["$name"]="$(declare -p "$name")"
    fi
  done
}
restore_env() {
  local name
  for name in "${!PRE_DECL[@]}"; do
    eval "${PRE_DECL[$name]}"
  done
}

capture_env ROBOTWIN_ROOT OUT_ROOT PORT_BASE TASK_TIMEOUT PROFILE MODEL_WS GPUS NUM_GPUS WS_FILE API_KEY_FILE API_KEY MODEL_API_KEY BRIDGE_MODEL BRIDGE_LOG_LEVEL BRIDGE_INSECURE MODEL_NAME CKPT NOTE
if [[ -f "$ENV_FILE" ]]; then
  source "$ENV_FILE"
fi
restore_env

ROBOTWIN_ROOT="${ROBOTWIN_ROOT:-}"
OUT_ROOT="${OUT_ROOT:-}"
PORT_BASE="${PORT_BASE:-5695}"
TASK_TIMEOUT="${TASK_TIMEOUT:-0}"
PROFILE_ARG="${PROFILE:-}"
if declare -p GPUS >/dev/null 2>&1 && declare -p GPUS 2>/dev/null | grep -q 'declare -a'; then
  GPUS_ARG="$(IFS=','; printf '%s' "${GPUS[*]}")"
else
  GPUS_ARG="${GPUS:-}"
fi
NUM_GPUS_ARG="${NUM_GPUS:-}"
WS_FILE="${WS_FILE:-}"
API_KEY_FILE="${API_KEY_FILE:-}"
API_KEY="${API_KEY:-}"
MODEL_API_KEY="${MODEL_API_KEY:-}"
BRIDGE_MODEL="${BRIDGE_MODEL:-}"
BRIDGE_LOG_LEVEL="${BRIDGE_LOG_LEVEL:-}"
BRIDGE_INSECURE="${BRIDGE_INSECURE:-0}"
MODEL_NAME="${MODEL_NAME:-}"
CKPT="${CKPT:-}"
NOTE="${NOTE:-}"
export MODEL_API_KEY

CMD="${1:-}"
[[ $# -gt 0 ]] && shift
RUN_ID_ARG=""
NEW_RUN=0
FORCE=0
DRY_RUN=0
FIX=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) [[ $# -ge 2 ]] || die "--root 需要参数"; ROBOTWIN_ROOT="$2"; shift 2 ;;
    --out-root|--output) [[ $# -ge 2 ]] || die "--out-root 需要参数"; OUT_ROOT="$2"; shift 2 ;;
    --profile) [[ $# -ge 2 ]] || die "--profile 需要参数"; PROFILE_ARG="$2"; shift 2 ;;
    --gpus) [[ $# -ge 2 ]] || die "--gpus 需要参数"; GPUS_ARG="$2"; shift 2 ;;
    --num-gpus) [[ $# -ge 2 ]] || die "--num-gpus 需要参数"; NUM_GPUS_ARG="$2"; shift 2 ;;
    --port-base) [[ $# -ge 2 ]] || die "--port-base 需要参数"; PORT_BASE="$2"; shift 2 ;;
    --task-timeout|--timeout) [[ $# -ge 2 ]] || die "--task-timeout 需要参数"; TASK_TIMEOUT="$2"; shift 2 ;;
    --ws-file) [[ $# -ge 2 ]] || die "--ws-file 需要参数"; WS_FILE="$2"; shift 2 ;;
    --api-key-file) [[ $# -ge 2 ]] || die "--api-key-file 需要参数"; API_KEY_FILE="$2"; shift 2 ;;
    --api-key) [[ $# -ge 2 ]] || die "--api-key 需要参数"; API_KEY="$2"; shift 2 ;;
    --model) [[ $# -ge 2 ]] || die "--model 需要参数"; BRIDGE_MODEL="$2"; shift 2 ;;
    --log-level) [[ $# -ge 2 ]] || die "--log-level 需要参数"; BRIDGE_LOG_LEVEL="$2"; shift 2 ;;
    --insecure) BRIDGE_INSECURE=1; shift ;;
    --model-name) [[ $# -ge 2 ]] || die "--model-name 需要参数"; MODEL_NAME="$2"; shift 2 ;;
    --ckpt) [[ $# -ge 2 ]] || die "--ckpt 需要参数"; CKPT="$2"; shift 2 ;;
    --note) [[ $# -ge 2 ]] || die "--note 需要参数"; NOTE="$2"; shift 2 ;;
    --run-id) [[ $# -ge 2 ]] || die "--run-id 需要参数"; RUN_ID_ARG="$2"; shift 2 ;;
    --new-run) NEW_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --fix) FIX=1; shift ;;
    -h|--help|help) usage; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

[[ "$PORT_BASE" =~ ^[0-9]+$ ]] || die "--port-base 必须是整数"
[[ "$TASK_TIMEOUT" =~ ^[0-9]+$ ]] || die "--task-timeout 必须是整数"
[[ "$BRIDGE_INSECURE" =~ ^[01]$ ]] || BRIDGE_INSECURE=0

ROBOTWIN_ROOT="${ROBOTWIN_ROOT%/}"
if [[ -n "$ROBOTWIN_ROOT" ]] && [[ -d "$ROBOTWIN_ROOT" ]]; then
  ROBOTWIN_ROOT="$(cd "$ROBOTWIN_ROOT" && pwd)"
fi
FIXED_EVAL_DIR="$ROBOTWIN_ROOT/XPolicyLab/policy/CloudRobo_WM/evaluation"
CACHE_DIR="$BRIDGE_DIR/robotwin_cache"
OUT_ROOT="${OUT_ROOT:-$ROBOTWIN_ROOT/fixed_out}"
RUNS_ROOT="$OUT_ROOT/runs"

declare -a PROFILES=()
IFS=',' read -r -a PROFILES <<< "${PROFILE_ARG:-clean_seen}"
PROFILES_CSV=""
for _p in "${PROFILES[@]}"; do
  _p="${_p// /}"
  case "$_p" in
    clean_seen|random_unseen) ;;
    *) die "未知 profile: $_p" ;;
  esac
  if [[ -n "$PROFILES_CSV" ]]; then PROFILES_CSV="$PROFILES_CSV,$_p"; else PROFILES_CSV="$_p"; fi
done
[[ -n "$PROFILES_CSV" ]] || die "--profile 不能为空"

declare -a GPUS=()
if [[ -n "$GPUS_ARG" ]]; then
  IFS=',' read -r -a GPUS <<< "$GPUS_ARG"
elif [[ -n "$NUM_GPUS_ARG" ]]; then
  [[ "$NUM_GPUS_ARG" =~ ^[0-9]+$ ]] || die "--num-gpus 必须是整数"
  [[ "$NUM_GPUS_ARG" -ge 1 ]] || die "--num-gpus 至少为 1"
  for ((_i=0; _i<NUM_GPUS_ARG; _i++)); do GPUS+=("$_i"); done
else
  GPUS=("0")
fi
[[ ${#GPUS[@]} -ge 1 ]] || die "至少需要 1 张卡"
GPUS_CSV=""
for _g in "${GPUS[@]}"; do
  if [[ -n "$GPUS_CSV" ]]; then GPUS_CSV="$GPUS_CSV,$_g"; else GPUS_CSV="$_g"; fi
done

declare -a PORTS=()
for _i in "${!GPUS[@]}"; do
  PORTS+=("$((PORT_BASE + _i))")
done

WS_FILE_MISSING=0
if [[ -n "$WS_FILE" ]]; then
  MODEL_WS=()
  if [[ -f "$WS_FILE" ]]; then
    while IFS= read -r _line || [[ -n "$_line" ]]; do
      _line="${_line%$'\r'}"
      _line="${_line#"${_line%%[![:space:]]*}"}"
      _line="${_line%"${_line##*[![:space:]]}"}"
      [[ -z "$_line" ]] && continue
      MODEL_WS+=("$_line")
    done < "$WS_FILE"
  else
    WS_FILE_MISSING=1
  fi
else
  if declare -p MODEL_WS 2>/dev/null | grep -q 'declare -a'; then
    :
  else
    _raw="${MODEL_WS[*]:-}"
    MODEL_WS=()
    if [[ -n "${_raw// /}" ]]; then
      read -r -a MODEL_WS <<< "$_raw"
    fi
  fi
fi

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    "$PY" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
  fi
}
sha256_lf_file() {
  "$PY" -c 'import hashlib,sys;d=open(sys.argv[1],"rb").read().replace(b"\r\n",b"\n");print(hashlib.sha256(d).hexdigest())' "$1"
}
crlf_to_lf() {
  "$PY" - "$1" <<'PY'
import sys
path = sys.argv[1]
data = open(path, "rb").read()
open(path, "wb").write(data.replace(b"\r\n", b"\n"))
PY
}
strip_bom() {
  "$PY" - "$1" <<'PY'
import sys
path = sys.argv[1]
data = open(path, "rb").read()
if data.startswith(b"\xef\xbb\xbf"):
    open(path, "wb").write(data[3:])
PY
}
has_bom() {
  [[ -f "$1" ]] && [[ "$(head -c 3 "$1" | od -An -tx1 | tr -d ' \n')" == "efbbbf" ]]
}
sdk_field() {
  "$PY" - "$1" "$2" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], ""))
except Exception:
    print("")
PY
}
json_get() {
  "$PY" - "$1" "$2" <<'PY'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], "")
except Exception:
    value = ""
if isinstance(value, (list, dict)):
    print(json.dumps(value, ensure_ascii=False))
else:
    print(value)
PY
}
port_in_use() {
  "$PY" - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    print("1")
    sys.exit(0)
finally:
    sock.close()
print("0")
PY
}
ensure_port_free() {
  if [[ "$(port_in_use "$1")" == "1" ]]; then
    die "端口 $1 已被占用"
  fi
}

CACHE_SHA=""
SDK_SHA=""
DRIVER_SHA=""
MODEL_WS_SHA=""
if [[ -f "$CACHE_DIR/evaluation_10000.json" ]]; then CACHE_SHA="$(sha256_file "$CACHE_DIR/evaluation_10000.json")"; fi
if [[ -f "$FIXED_EVAL_DIR/SDK_BINDING.json" ]]; then SDK_SHA="$(sha256_file "$FIXED_EVAL_DIR/SDK_BINDING.json")"; fi
if [[ -f "$DRIVER" ]]; then DRIVER_SHA="$(sha256_file "$DRIVER")"; fi
MODEL_WS_SHA="$("$PY" - "${MODEL_WS[@]+"${MODEL_WS[@]}"}" <<'PY'
import hashlib, sys
print(hashlib.sha256("\n".join(sys.argv[1:]).encode()).hexdigest())
PY
)"

CHECK_PROBLEMS=0
CHECK_FIXABLE=0
check_bom() {
  local file="$1" label="$2"
  [[ -f "$file" ]] || return 0
  if has_bom "$file"; then
    if [[ "$FIX" -eq 1 ]]; then
      strip_bom "$file"
      log "✓ [$label] 已去 BOM: $file"
    else
      log "! [$label] 存在 BOM（可 check --fix）: $file"
      CHECK_FIXABLE=$((CHECK_FIXABLE + 1))
    fi
  fi
}
check_sha() {
  local file="$1" expect="$2" label="$3" allow_fix="${4:-1}"
  if [[ -z "$expect" ]]; then
    log "! [$label] 无预期 sha256，跳过"
    return 0
  fi
  if [[ ! -f "$file" ]]; then
    log "✗ [$label] 缺少文件: $file"
    CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
    return 1
  fi
  local raw lf
  raw="$(sha256_file "$file")"
  if [[ "$raw" == "$expect" ]]; then
    log "✓ [$label] sha256 匹配: $file"
    return 0
  fi
  lf="$(sha256_lf_file "$file")"
  if [[ "$lf" == "$expect" ]]; then
    if [[ "$allow_fix" -eq 1 ]]; then
      if [[ "$FIX" -eq 1 ]]; then
        strip_bom "$file"
        crlf_to_lf "$file"
        raw="$(sha256_file "$file")"
        if [[ "$raw" == "$expect" ]]; then
          log "✓ [$label] 已无损修复并复检通过: $file"
          return 0
        fi
      fi
      log "! [$label] 仅 CRLF 归一化后匹配（可 check --fix）: $file"
      CHECK_FIXABLE=$((CHECK_FIXABLE + 1))
      return 2
    fi
    log "✗ [$label] 仅 CRLF 归一化后匹配，但基础包禁止修改: $file"
    CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
    return 1
  fi
  log "✗ [$label] 版本不符: $file"
  CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
  return 1
}
check_cache_checksums() {
  local sums="$CACHE_DIR/CHECKSUMS.json"
  if [[ ! -f "$sums" ]]; then
    log "✗ 缺少缓存校验文件: $sums"
    CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
    return
  fi
  local name expect
  while read -r name expect; do
    [[ -z "$name" ]] && continue
    check_sha "$CACHE_DIR/$name" "$expect" "cache:$name" 0
  done < <("$PY" - "$sums" <<'PY'
import json, sys
for name, item in json.load(open(sys.argv[1], encoding="utf-8"))["files"].items():
    print(name, item["sha256"])
PY
)
}

check_cmd() {
  CHECK_PROBLEMS=0
  CHECK_FIXABLE=0
  log "=== 固定评测环境检查 ==="
  if [[ -d "$ROBOTWIN_ROOT" ]]; then
    log "✓ ROBOTWIN_ROOT: $ROBOTWIN_ROOT"
  else
    log "✗ ROBOTWIN_ROOT 不存在: ${ROBOTWIN_ROOT:-<未设置>}"
    CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
  fi
  if "$PY" --version >/dev/null 2>&1; then
    log "✓ $("$PY" --version 2>&1)"
  else
    log "✗ python 不可用"
    CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
  fi
  local tool
  for tool in flock fuser timeout; do
    if command -v "$tool" >/dev/null 2>&1; then
      log "✓ 命令可用: $tool"
    else
      log "✗ 缺少命令: $tool"
      CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
    fi
  done
  local name
  for name in eval_policy_xpolicylab.py fixed_runtime.py fixed_rollout.py SDK_BINDING.json; do
    if [[ -f "$FIXED_EVAL_DIR/$name" ]]; then
      log "✓ 基础包文件: $FIXED_EVAL_DIR/$name"
    else
      log "✗ 缺少基础包文件: $FIXED_EVAL_DIR/$name"
      CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
    fi
  done
  if [[ ! -f "$DRIVER" ]]; then
    log "✗ 缺少驱动: $DRIVER"
    CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
  fi
  local sdk="$FIXED_EVAL_DIR/SDK_BINDING.json"
  if [[ -f "$sdk" ]]; then
    local expect_client expect_base client base
    expect_client="$(sdk_field "$sdk" official_client_sha256)"
    expect_base="$(sdk_field "$sdk" base_task_sha256)"
    client="$ROBOTWIN_ROOT/scripts/eval_policy_xpolicylab.py"
    base="$ROBOTWIN_ROOT/envs/_base_task.py"
    check_bom "$client" "official_client"
    check_sha "$client" "$expect_client" "official_client"
    check_bom "$base" "base_task"
    check_sha "$base" "$expect_base" "base_task"
    check_cache_checksums
  fi
  local i
  for i in "${!PORTS[@]}"; do
    if [[ "$(port_in_use "${PORTS[$i]}")" == "1" ]]; then
      log "✗ 端口被占用: ${PORTS[$i]}"
      CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
    else
      log "✓ 端口空闲: ${PORTS[$i]}"
    fi
  done
  if [[ "$WS_FILE_MISSING" -eq 1 ]]; then
    log "✗ --ws-file 不存在: $WS_FILE"
    CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
  elif [[ ${#MODEL_WS[@]} -eq 0 ]]; then
    log "! 未提供模型地址，跳过数量检查（check 不连接模型；正式 start 前需配置）"
  elif [[ ${#MODEL_WS[@]} -lt ${#GPUS[@]} ]]; then
    log "✗ MODEL_WS 数量(${#MODEL_WS[@]}) 少于卡数(${#GPUS[@]})"
    CHECK_PROBLEMS=$((CHECK_PROBLEMS + 1))
  else
    log "✓ MODEL_WS 数量 ${#MODEL_WS[@]} >= 卡数 ${#GPUS[@]}"
  fi
  log "=== 检查结束：问题 $CHECK_PROBLEMS 项，可无损修复 $CHECK_FIXABLE 项 ==="
  [[ "$CHECK_PROBLEMS" -eq 0 ]] && return 0
  return 1
}

require_root() {
  [[ -n "$ROBOTWIN_ROOT" ]] && [[ -d "$ROBOTWIN_ROOT" ]] || die "缺少或无效的 ROBOTWIN_ROOT（用 --root、环境变量或 fixed_eval.env 指定）"
}
require_model_ws() {
  if [[ "$WS_FILE_MISSING" -eq 1 ]]; then
    die "--ws-file 不存在: $WS_FILE"
  fi
  [[ ${#MODEL_WS[@]} -ge 1 ]] || die "缺少模型地址：用 --ws-file、环境变量 MODEL_WS（空格分隔）或 fixed_eval.env 的 MODEL_WS 数组"
  [[ ${#MODEL_WS[@]} -ge ${#GPUS[@]} ]] || die "MODEL_WS 数量(${#MODEL_WS[@]}) 少于卡数(${#GPUS[@]})"
}
require_static_inputs() {
  require_root
  if [[ "$WS_FILE_MISSING" -eq 1 ]]; then
    die "--ws-file 不存在: $WS_FILE"
  fi
  [[ -f "$CACHE_DIR/evaluation_10000.json" ]] || die "缺少缓存清单: $CACHE_DIR/evaluation_10000.json"
  [[ -f "$FIXED_EVAL_DIR/SDK_BINDING.json" ]] || die "缺少 SDK_BINDING.json: $FIXED_EVAL_DIR/SDK_BINDING.json"
}
require_inputs() {
  require_static_inputs
  require_model_ws
}

declare -a ALL_TASKS=()
load_tasks() {
  [[ -f "$CACHE_DIR/evaluation_10000.json" ]] || die "缺少缓存清单: $CACHE_DIR/evaluation_10000.json"
  mapfile -t ALL_TASKS < <("$PY" - "$CACHE_DIR/evaluation_10000.json" <<'PY'
import json, sys
print("\n".join(json.load(open(sys.argv[1], encoding="utf-8"))["task_names"]))
PY
)
  [[ ${#ALL_TASKS[@]} -eq 50 ]] || die "缓存清单任务数不是 50"
}

select_run_id() {
  if [[ -n "$RUN_ID_ARG" ]]; then
    RUN_ID="$RUN_ID_ARG"
  else
    local latest
    latest="$(ls -1 "$RUNS_ROOT" 2>/dev/null | sort | tail -n 1 || true)"
    RUN_ID="$latest"
  fi
  [[ -n "$RUN_ID" ]] || die "没有可用的 run；请先执行 start"
  RUN_DIR="$RUNS_ROOT/$RUN_ID"
  RUN_JSON="$RUN_DIR/run.json"
}
prepare_run_id() {
  if [[ -n "$RUN_ID_ARG" ]]; then
    RUN_ID="$RUN_ID_ARG"
  elif [[ "$NEW_RUN" -eq 1 ]]; then
    RUN_ID="$(date +%Y%m%d_%H%M%S)"
  else
    local latest
    latest="$(ls -1 "$RUNS_ROOT" 2>/dev/null | sort | tail -n 1 || true)"
    RUN_ID="${latest:-$(date +%Y%m%d_%H%M%S)}"
  fi
  RUN_DIR="$RUNS_ROOT/$RUN_ID"
  RUN_JSON="$RUN_DIR/run.json"
}

now() { date +%Y-%m-%dT%H:%M:%S%z; }

ensure_run_meta() {
  RJ_PATH="$RUN_JSON" RJ_RUN_ID="$RUN_ID" RJ_STATUS="运行中" RJ_PROFILES="$PROFILES_CSV" \
  RJ_GPUS="$GPUS_CSV" RJ_MODEL_WS_SHA="$MODEL_WS_SHA" RJ_CACHE_SHA="$CACHE_SHA" \
  RJ_SDK_SHA="$SDK_SHA" RJ_DRIVER_SHA="$DRIVER_SHA" RJ_MODEL_NAME="$MODEL_NAME" \
  RJ_CKPT="$CKPT" RJ_NOTE="$NOTE" RJ_PID="$$" RJ_PORT_BASE="$PORT_BASE" "$PY" - <<'PY'
import datetime, json, os
path = os.environ["RJ_PATH"]
data = {}
if os.path.exists(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        data = {}
stamp = datetime.datetime.now().isoformat(timespec="seconds")
data.update({
    "run_id": os.environ["RJ_RUN_ID"],
    "status": os.environ["RJ_STATUS"],
    "profiles": [item for item in os.environ["RJ_PROFILES"].split(",") if item],
    "gpus": [item for item in os.environ["RJ_GPUS"].split(",") if item != ""],
    "model_ws_sha256": os.environ["RJ_MODEL_WS_SHA"],
    "cache_manifest_sha256": os.environ["RJ_CACHE_SHA"],
    "sdk_binding_sha256": os.environ["RJ_SDK_SHA"],
    "driver_sha256": os.environ["RJ_DRIVER_SHA"],
    "model_name": os.environ["RJ_MODEL_NAME"],
    "ckpt": os.environ["RJ_CKPT"],
    "note": os.environ["RJ_NOTE"],
    "pid": os.environ["RJ_PID"],
    "port_base": int(os.environ["RJ_PORT_BASE"]),
})
data["started_at"] = data.get("started_at") or stamp
data["updated_at"] = stamp
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
}

update_run_status() {
  local status="$1" pid="${2:-}"
  [[ -n "${RUN_JSON:-}" ]] || return 0
  RJ_PATH="$RUN_JSON" RJ_STATUS="$status" RJ_PID="$pid" "$PY" - <<'PY'
import datetime, json, os
path = os.environ["RJ_PATH"]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    data = {}
data["status"] = os.environ["RJ_STATUS"]
data["pid"] = os.environ["RJ_PID"]
data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
}

fingerprint_field() {
  RF_PATH="$RUN_JSON" RF_PROFILES="$PROFILES_CSV" RF_GPU_COUNT="${#GPUS[@]}" \
  RF_CACHE="$CACHE_SHA" RF_SDK="$SDK_SHA" "$PY" - <<'PY'
import json, os, sys
try:
    data = json.load(open(os.environ["RF_PATH"], encoding="utf-8"))
except Exception:
    print("ok")
    sys.exit(0)
if [item for item in os.environ["RF_PROFILES"].split(",") if item] != (data.get("profiles") or []):
    print("profiles")
    sys.exit(0)
if len(data.get("gpus") or []) != int(os.environ["RF_GPU_COUNT"]):
    print("gpus")
    sys.exit(0)
if data.get("cache_manifest_sha256") != os.environ["RF_CACHE"]:
    print("cache_manifest_sha256")
    sys.exit(0)
if data.get("sdk_binding_sha256") != os.environ["RF_SDK"]:
    print("sdk_binding_sha256")
    sys.exit(0)
print("ok")
PY
}

result_trials() {
  local file="$1/FIXED_RESULT.json"
  if [[ ! -f "$file" ]]; then
    printf ''
    return 0
  fi
  "$PY" - "$file" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("trials", ""))
except Exception:
    print("")
PY
}
task_progress() {
  "$PY" - "$RUN_DIR" "$1" "$2" <<'PY'
import glob, json, os, sys
root, profile, task = sys.argv[1], sys.argv[2], sys.argv[3]
base = os.path.join(root, profile, task)
completed = success = 0
for directory in [base] + sorted(glob.glob(base + ".old*")):
    path = os.path.join(directory, "fixed_cases.jsonl")
    if not os.path.isfile(path):
        continue
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            item = json.loads(line)
        except Exception:
            continue
        if item.get("event") == "fixed_input_completed":
            completed += 1
            if item.get("success"):
                success += 1
print(completed, success)
PY
}

build_queue() {
  local mode="$1" out="$2" p t task_dir trials completed success rf pv
  : > "$out"
  for p in "${PROFILES[@]}"; do
    for t in "${ALL_TASKS[@]}"; do
      task_dir="$RUN_DIR/$p/$t"
      trials="$(result_trials "$task_dir")"
      if [[ "$trials" == "100" && "$FORCE" -ne 1 ]]; then
        continue
      fi
      rf=0
      pv=0
      if [[ "$mode" == "resume" ]]; then
        read -r completed success < <(task_progress "$p" "$t")
        rf="${completed:-0}"
        pv="${success:-0}"
      else
        rm -rf "$task_dir.old" "$task_dir".old.* 2>/dev/null || true
      fi
      [[ "$rf" -gt 100 ]] && rf=100
      [[ "$pv" -gt "$rf" ]] && pv="$rf"
      printf '%s %s %s %s\n' "$p" "$t" "$rf" "$pv" >> "$out"
    done
  done
}
build_failed_queue() {
  local out="$1" failed_file="$2" p t task_dir completed success
  : > "$out"
  while read -r p t; do
    [[ -z "$p" ]] && continue
    task_dir="$RUN_DIR/$p/$t"
    read -r completed success < <(task_progress "$p" "$t")
    [[ "${completed:-0}" -gt 100 ]] && completed=100
    [[ "${success:-0}" -gt "${completed:-0}" ]] && success="${completed:-0}"
    printf '%s %s %s %s\n' "$p" "$t" "${completed:-0}" "${success:-0}" >> "$out"
  done < "$failed_file"
}

queue_pop() {
  (
    flock 9
    if [[ -s "$QUEUE_FILE" ]]; then
      head -n 1 "$QUEUE_FILE"
      sed -i '1d' "$QUEUE_FILE"
    fi
  ) 9>"$QUEUE_LOCK"
}

kill_tree() {
  local pid="$1" child
  for child in $(ps -o pid= --ppid "$pid" 2>/dev/null); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

declare -a BRIDGE_PIDS=()
declare -a WORKER_PIDS=()
run_state=""
QUEUE_FILE=""
QUEUE_LOCK=""
DISPATCH_ACTIVE=0

cleanup_children() {
  local pid i
  if [[ ${#WORKER_PIDS[@]} -gt 0 ]]; then
    for pid in "${WORKER_PIDS[@]}"; do kill_tree "$pid"; done
  fi
  if [[ ${#BRIDGE_PIDS[@]} -gt 0 ]]; then
    for pid in "${BRIDGE_PIDS[@]}"; do kill_tree "$pid"; done
  fi
  for i in "${!PORTS[@]}"; do fuser -k "${PORTS[$i]}/tcp" >/dev/null 2>&1 || true; done
}

cleanup_exit() {
  local code=$?
  if [[ "$DISPATCH_ACTIVE" -eq 1 ]]; then
    DISPATCH_ACTIVE=0
    update_run_status "已中断" ""
    cleanup_children
  fi
  exit "$code"
}
trap cleanup_exit EXIT

on_signal() {
  trap - INT TERM
  log ""
  log "收到中断信号，正在清理..."
  update_run_status "已中断" ""
  DISPATCH_ACTIVE=0
  cleanup_children
  exit 130
}

start_bridges() {
  local i port ws logfile
  local -a args=()
  for i in "${!GPUS[@]}"; do
    port="${PORTS[$i]}"
    ws="${MODEL_WS[$((i % ${#MODEL_WS[@]}))]}"
    ensure_port_free "$port"
    logfile="$run_state/logs/bridge_${i}.log"
    args=(--ws-url "$ws" --port "$port")
    [[ -n "$API_KEY_FILE" ]] && args+=(--api-key-file "$API_KEY_FILE")
    [[ -z "$API_KEY_FILE" && -n "$API_KEY" ]] && args+=(--api-key "$API_KEY")
    [[ "$BRIDGE_INSECURE" -eq 1 ]] && args+=(--insecure)
    [[ -n "$BRIDGE_MODEL" ]] && args+=(--model "$BRIDGE_MODEL")
    [[ -n "$BRIDGE_LOG_LEVEL" ]] && args+=(--log-level "$BRIDGE_LOG_LEVEL")
    bash "$BRIDGE_DIR/start.sh" --robotwin-root "$ROBOTWIN_ROOT" "${args[@]}" > "$logfile" 2>&1 &
    BRIDGE_PIDS+=("$!")
    log "[bridge] 端口 $port pid=$! ws=$ws"
  done
}

wait_bridges() {
  local i logfile ok waited
  for i in "${!BRIDGE_PIDS[@]}"; do
    logfile="$run_state/logs/bridge_${i}.log"
    ok=0
    waited=0
    while [[ "$waited" -lt "${BRIDGE_WAIT:-120}" ]]; do
      if grep -q "policy server ready" "$logfile" 2>/dev/null; then
        ok=1
        break
      fi
      if ! kill -0 "${BRIDGE_PIDS[$i]}" 2>/dev/null; then
        break
      fi
      sleep 1
      waited=$((waited + 1))
    done
    if [[ "$ok" -eq 1 ]]; then
      log "[bridge] 端口 ${PORTS[$i]} 就绪"
    else
      die "桥接层端口 ${PORTS[$i]} 未就绪，查看 $logfile"
    fi
  done
}

worker_loop() {
  local idx="$1" gpu="$2" port="$3" logfile="$4"
  local line p t rf pv rc
  local -a args=()
  export CUDA_VISIBLE_DEVICES="$gpu"
  while :; do
    line="$(queue_pop)"
    [[ -z "$line" ]] && break
    read -r p t rf pv <<< "$line"
    printf '[worker %s] [%s] %s 开始 resume_from=%s prev_success=%s\n' "$idx" "$p" "$t" "$rf" "$pv" | tee -a "$logfile"
    args=("$PY" "$DRIVER" --profile "$p" --task "$t" --host 127.0.0.1 --port "$port" --root "$ROBOTWIN_ROOT" --cache "$CACHE_DIR" --eval-dir "$FIXED_EVAL_DIR" --output "$RUN_DIR")
    if [[ "$rf" != "0" ]]; then
      args+=(--resume-from "$rf" --prev-success "$pv")
    fi
    rc=0
    if [[ "${TASK_TIMEOUT:-0}" -gt 0 ]]; then
      timeout "$TASK_TIMEOUT" "${args[@]}" >> "$logfile" 2>&1 || rc=$?
    else
      "${args[@]}" >> "$logfile" 2>&1 || rc=$?
    fi
    if [[ "$rc" -eq 0 ]]; then
      printf '[worker %s] [%s] %s 完成\n' "$idx" "$p" "$t" | tee -a "$logfile"
    else
      printf '[worker %s] [%s] %s 失败(rc=%s)\n' "$idx" "$p" "$t" "$rc" | tee -a "$logfile"
      printf '%s %s\n' "$p" "$t" >> "$run_state/failed.txt"
    fi
  done
}

start_workers() {
  local i gpu port logfile
  for i in "${!GPUS[@]}"; do
    gpu="${GPUS[$i]}"
    port="${PORTS[$i]}"
    logfile="$run_state/logs/worker_${i}.log"
    worker_loop "$i" "$gpu" "$port" "$logfile" &
    WORKER_PIDS+=("$!")
    log "[worker] $i gpu=$gpu port=$port pid=$!"
  done
}

wait_workers() {
  local pid rc=0
  for pid in "${WORKER_PIDS[@]}"; do
    wait "$pid" || rc=1
  done
  return "$rc"
}

stop_bridges() {
  local pid i
  if [[ ${#BRIDGE_PIDS[@]} -gt 0 ]]; then
    for pid in "${BRIDGE_PIDS[@]}"; do kill_tree "$pid"; done
  fi
  for i in "${!PORTS[@]}"; do fuser -k "${PORTS[$i]}/tcp" >/dev/null 2>&1 || true; done
}

count_done() {
  "$PY" - "$RUN_DIR" <<'PY'
import glob, json, os, sys
done = 0
for path in glob.glob(os.path.join(sys.argv[1], "*", "*", "FIXED_RESULT.json")):
    try:
        if json.load(open(path, encoding="utf-8")).get("trials") == 100:
            done += 1
    except Exception:
        pass
print(done)
PY
}
finalize_status() {
  local total done
  total=$(( ${#PROFILES[@]} * 50 ))
  done="$(count_done)"
  if [[ "$done" -ge "$total" ]]; then
    update_run_status "已完成" ""
  else
    update_run_status "部分完成" ""
  fi
}

print_dry_run() {
  local queue="$1" i port ws item skip
  local -a args=() shown=()
  log "[dry-run] run=$RUN_ID 任务数=$(wc -l < "$queue" 2>/dev/null || echo 0)"
  log "[dry-run] 档位=$PROFILES_CSV 卡=$GPUS_CSV 端口=${PORTS[*]}"
  for i in "${!GPUS[@]}"; do
    port="${PORTS[$i]}"
    if [[ ${#MODEL_WS[@]} -gt 0 ]]; then
      ws="${MODEL_WS[$((i % ${#MODEL_WS[@]}))]}"
    else
      ws="<未配置模型地址>"
    fi
    args=(--ws-url "$ws" --port "$port")
    [[ -n "$API_KEY_FILE" ]] && args+=(--api-key-file "$API_KEY_FILE")
    [[ -z "$API_KEY_FILE" && -n "$API_KEY" ]] && args+=(--api-key "$API_KEY")
    [[ "$BRIDGE_INSECURE" -eq 1 ]] && args+=(--insecure)
    [[ -n "$BRIDGE_MODEL" ]] && args+=(--model "$BRIDGE_MODEL")
    [[ -n "$BRIDGE_LOG_LEVEL" ]] && args+=(--log-level "$BRIDGE_LOG_LEVEL")
    shown=()
    skip=0
    for item in "${args[@]}"; do
      if [[ "$skip" -eq 1 ]]; then
        shown+=("***")
        skip=0
        continue
      fi
      shown+=("$item")
      [[ "$item" == "--api-key" ]] && skip=1
    done
    log "[dry-run] bash $BRIDGE_DIR/start.sh --robotwin-root $ROBOTWIN_ROOT ${shown[*]}"
  done
  local p t rf pv
  while read -r p t rf pv; do
    [[ -z "$p" ]] && continue
    log "[dry-run] $PY $DRIVER --profile $p --task $t --host 127.0.0.1 --port <对应端口> --root $ROBOTWIN_ROOT --cache $CACHE_DIR --eval-dir $FIXED_EVAL_DIR --output $RUN_DIR --resume-from $rf --prev-success $pv"
  done < "$queue"
}

dispatch() {
  local queue="$1" stamp failed_count
  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_dry_run "$queue"
    return 0
  fi
  mkdir -p "$RUN_DIR"
  ensure_run_meta
  stamp="$(date +%Y%m%d_%H%M%S)"
  run_state="$RUN_DIR/.run/$stamp"
  mkdir -p "$run_state/logs"
  cp "$queue" "$run_state/queue.txt"
  : > "$run_state/failed.txt"
  echo "$stamp" > "$RUN_DIR/.run/current"
  QUEUE_FILE="$run_state/queue.txt"
  QUEUE_LOCK="$run_state/queue.lock"
  DISPATCH_ACTIVE=1
  trap on_signal INT TERM
  update_run_status "运行中" "$$"
  start_bridges
  wait_bridges
  start_workers
  wait_workers || true
  stop_bridges
  DISPATCH_ACTIVE=0
  trap - INT TERM
  finalize_status
  failed_count="$(wc -l < "$run_state/failed.txt" 2>/dev/null || echo 0)"
  log "[完成] run=$RUN_ID 失败任务=$failed_count 日志=$run_state/logs"
}

cmd_start() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    require_static_inputs
  else
    require_inputs
  fi
  load_tasks
  prepare_run_id
  if [[ "$NEW_RUN" -eq 1 ]] && [[ -e "$RUN_DIR" ]]; then
    die "run 已存在: $RUN_ID（--new-run 需要新的 run-id）"
  fi
  if [[ -f "$RUN_JSON" ]]; then
    local field
    field="$(fingerprint_field)"
    if [[ "$field" != "ok" ]]; then
      die "输入指纹变化（$field），拒绝续跑；请加 --new-run"
    fi
  fi
  local queue
  queue="$(mktemp)"
  build_queue start "$queue"
  if [[ ! -s "$queue" ]]; then
    log "没有需要执行的任务（全部 trials=100）"
    rm -f "$queue"
    return 0
  fi
  dispatch "$queue"
  rm -f "$queue"
}

cmd_resume() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    require_static_inputs
  else
    require_inputs
  fi
  load_tasks
  select_run_id
  [[ -f "$RUN_JSON" ]] || die "run 不存在: $RUN_ID"
  local field
  field="$(fingerprint_field)"
  if [[ "$field" != "ok" ]]; then
    die "输入指纹变化（$field），拒绝续跑；请用 --new-run 新建"
  fi
  local queue
  queue="$(mktemp)"
  build_queue resume "$queue"
  if [[ ! -s "$queue" ]]; then
    log "没有需要续跑的任务"
    rm -f "$queue"
    return 0
  fi
  dispatch "$queue"
  rm -f "$queue"
}

cmd_rerun_failed() {
  require_inputs
  select_run_id
  [[ -f "$RUN_JSON" ]] || die "run 不存在: $RUN_ID"
  local stamp failed
  stamp="$(cat "$RUN_DIR/.run/current" 2>/dev/null || true)"
  [[ -n "$stamp" ]] || die "找不到最近一次执行记录"
  failed="$RUN_DIR/.run/$stamp/failed.txt"
  [[ -f "$failed" ]] || die "失败清单不存在: $failed"
  local queue
  queue="$(mktemp)"
  build_failed_queue "$queue" "$failed"
  if [[ ! -s "$queue" ]]; then
    log "没有失败任务"
    rm -f "$queue"
    return 0
  fi
  dispatch "$queue"
  rm -f "$queue"
}

run_state_line() {
  "$PY" - "$RUN_JSON" "$RUN_DIR" <<'PY'
import glob, json, os, sys
meta_path, root = sys.argv[1], sys.argv[2]
try:
    meta = json.load(open(meta_path, encoding="utf-8"))
except Exception:
    meta = {}
alive = False
pid = str(meta.get("pid") or "")
if pid.isdigit():
    try:
        os.kill(int(pid), 0)
        alive = True
    except OSError:
        alive = False
total = len(meta.get("profiles") or []) * 50
done = 0
for path in glob.glob(os.path.join(root, "*", "*", "FIXED_RESULT.json")):
    try:
        if json.load(open(path, encoding="utf-8")).get("trials") == 100:
            done += 1
    except Exception:
        pass
if alive:
    status = "运行中"
elif total and done >= total:
    status = "已完成"
elif done > 0:
    status = "部分完成"
else:
    status = "已中断"
print(f"{status}|{done}|{total}")
PY
}

status_cmd() {
  local -a ids=()
  local id line status done total
  if [[ -n "$RUN_ID_ARG" ]]; then
    ids=("$RUN_ID_ARG")
  else
    while IFS= read -r id; do
      [[ -n "$id" ]] && ids+=("$id")
    done < <(ls -1 "$RUNS_ROOT" 2>/dev/null | sort)
  fi
  if [[ ${#ids[@]} -eq 0 ]]; then
    log "没有 run"
    return 0
  fi
  for id in "${ids[@]}"; do
    RUN_ID="$id"
    RUN_DIR="$RUNS_ROOT/$id"
    RUN_JSON="$RUN_DIR/run.json"
    [[ -f "$RUN_JSON" ]] || continue
    line="$(run_state_line)"
    IFS='|' read -r status done total <<< "$line"
    printf '%-24s %-6s %s/%s\n' "$id" "$status" "$done" "$total"
  done
}

list_cmd() {
  local id
  if [[ ! -d "$RUNS_ROOT" ]]; then
    log "没有 run"
    return 0
  fi
  for id in $(ls -1 "$RUNS_ROOT" | sort); do
    printf '%s\n' "$id"
  done
}

summary_cmd() {
  select_run_id
  [[ -d "$RUN_DIR" ]] || die "run 不存在: $RUN_ID"
  "$PY" - "$RUN_DIR" <<'PY'
import collections, csv, glob, json, os, sys
root = sys.argv[1]
profiles = collections.defaultdict(dict)
for path in sorted(glob.glob(os.path.join(root, "*", "*", "FIXED_RESULT.json"))):
    parts = path.replace("\\", "/").split("/")
    profile, task = parts[-3], parts[-2]
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        continue
    profiles[profile][task] = data
grand_s = grand_t = 0
for profile in sorted(profiles):
    rows = profiles[profile]
    successes = sum(int(row.get("successes") or 0) for row in rows.values())
    trials = sum(int(row.get("trials") or 0) for row in rows.values())
    percent = (100.0 * successes / trials) if trials else 0.0
    grand_s += successes
    grand_t += trials
    payload = {
        "profile": profile,
        "tasks": len(rows),
        "successes": successes,
        "trials": trials,
        "success_percent": round(percent, 2),
        "rows": [
            {"task": task, "successes": row.get("successes"), "trials": row.get("trials")}
            for task, row in sorted(rows.items())
        ],
    }
    with open(os.path.join(root, f"FIXED_SUMMARY_{profile}.json"), "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=1)
    with open(os.path.join(root, f"fixed_seed_results_{profile}.csv"), "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["profile", "task", "successes", "trials"])
        for task, row in sorted(rows.items()):
            writer.writerow([profile, task, row.get("successes", ""), row.get("trials", "")])
    print(f"[{profile}] 任务={len(rows)} 成功={successes}/{trials} = {percent:.2f}%")
total_percent = (100.0 * grand_s / grand_t) if grand_t else 0.0
print(f"[总计] 成功={grand_s}/{grand_t} = {total_percent:.2f}%")
PY
}

stop_cmd() {
  select_run_id
  [[ -f "$RUN_JSON" ]] || die "run 不存在: $RUN_ID"
  local pid port_base gpu_count i
  pid="$(json_get "$RUN_JSON" pid)"
  if [[ -n "$pid" ]] && [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill_tree "$pid"
    log "[stop] 已终止主进程 $pid"
  fi
  port_base="$(json_get "$RUN_JSON" port_base)"
  [[ "$port_base" =~ ^[0-9]+$ ]] || port_base="$PORT_BASE"
  gpu_count="$("$PY" - "$RUN_JSON" <<'PY'
import json, sys
try:
    print(len(json.load(open(sys.argv[1], encoding="utf-8")).get("gpus") or []))
except Exception:
    print(0)
PY
)"
  [[ "$gpu_count" =~ ^[0-9]+$ ]] || gpu_count=0
  for ((i = 0; i < gpu_count; i++)); do
    fuser -k "$((port_base + i))/tcp" >/dev/null 2>&1 || true
  done
  update_run_status "已中断" ""
  log "[stop] run $RUN_ID 已停止"
}

case "$CMD" in
  check) check_cmd ;;
  start) cmd_start ;;
  resume) cmd_resume ;;
  status) status_cmd ;;
  summary) summary_cmd ;;
  stop) stop_cmd ;;
  list) list_cmd ;;
  rerun-failed) cmd_rerun_failed ;;
  -h|--help|help) usage; exit 0 ;;
  "") usage; exit 1 ;;
  *) die "未知子命令: $CMD" ;;
esac
