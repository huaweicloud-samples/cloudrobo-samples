#!/usr/bin/env bash
# RoboTwin 桥接层启动脚本
#
# 用法:
#   ./start.sh --ws-url wss://<model-host>/ --port 5695 [--api-key <token>]
#   # 或 export MODEL_API_KEY=<token> 后省略 --api-key
#
# 前置要求 (客户环境):
#   - Python 3.10 + torch + sapien/mplib
#   - RoboTwin 仓库 (含 XPolicyLab submodule), 通过 --robotwin-root 指定
#
# 示例:
#   ./start.sh --ws-url wss://10.155.71.161:8000/ --port 5695 --robotwin-root /opt/RoboTwin

set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOTWIN_ROOT="${ROBOTWIN_ROOT:-}"
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --robotwin-root)
      if [[ $# -lt 2 ]]; then
        echo "[start] 错误: --robotwin-root 后必须指定路径值" >&2
        exit 1
      fi
      ROBOTWIN_ROOT="$2"; shift 2 ;;
    *)
      ARGS+=("$1"); shift ;;
  esac
done

if [[ -z "$ROBOTWIN_ROOT" ]]; then
  echo "[start] 未指定 RoboTwin 路径, 用 --robotwin-root 指定 (含 XPolicyLab submodule)" >&2
  exit 1
fi

export PYTHONPATH="$BRIDGE_DIR:$ROBOTWIN_ROOT:$ROBOTWIN_ROOT/XPolicyLab:$ROBOTWIN_ROOT/XPolicyLab/integrations${PYTHONPATH:+:$PYTHONPATH}"

echo "[start] PYTHONPATH=$PYTHONPATH"
echo "[start] 启动桥接层..."
exec python3 "$BRIDGE_DIR/main.py" "${ARGS[@]}"