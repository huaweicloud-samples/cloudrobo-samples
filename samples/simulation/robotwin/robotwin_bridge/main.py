"""
main.py — 桥接层独立入口

用法:
    python main.py --ws-url wss://<model-host>/  --port 5695 [--api-key xxx]
    # 或环境变量 MODEL_API_KEY (< api-key 时使用)

启动后监听 ws://0.0.0.0:<port>, RoboTwin 官方脚本连该端口即可:
    python scripts/eval_policy_xpolicylab.py --protocol ws --host 127.0.0.1 \
        --port 5695 --task_name adjust_bottle --task_config demo_clean \
        --test_num 5 --policy_name bridge --root_dir $ROBOTWIN_ROOT
"""
import argparse
import logging
import os
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bridge.main")


def parse_camera_map(raw: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in raw or []:
        if "=" not in item:
            raise ValueError(f"camera-map 需 key=value 格式, 收到: {item}")
        key, val = item.split("=", 1)
        mapping[key.strip()] = val.strip()
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RoboTwin 桥接层: ws↔wss + 格式转换 + token 认证",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--ws-url", required=True, help="远端模型 wss 地址 (如 wss://host:8000/)")
    parser.add_argument("--port", type=int, default=5695, help="本桥接层 ws 监听端口 (官方脚本连接)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--api-key", default=None, help="模型鉴权 token (直接命令行传入, 有 ps 可见风险)")
    parser.add_argument("--api-key-file", default=None, metavar="KEY_FILE",
                        help="从受保护文件(推荐 0600)读取鉴权 token, 优先级最高 (> --api-key > env MODEL_API_KEY)")
    parser.add_argument(
        "--camera-map", action="append", metavar="XPL=REMOTE",
        help="相机键映射, 可多次 (默认 cam_head=cam_high, cam_left_wrist=cam_left_wrist, cam_right_wrist=cam_right_wrist)",
    )
    parser.add_argument("--n-action-steps", type=int, default=None,
                        help="每次推理消费步数 (默认用满 chunk)")
    parser.add_argument("--model", default=None,
                        help="模型适配器名 (默认 auto: 按模型 metadata.protocol 自动选择; 当前可选 cr-wam)")
    parser.add_argument("--insecure", action="store_true",
                        help="禁用 TLS 证书校验 (仅限受控内网, 需评估风险)")
    parser.add_argument("--cacert", default=None, metavar="CA_FILE",
                        help="私有 CA 证书路径 (严格校验时加载)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level.upper())

    if args.api_key_file:
        try:
            with open(args.api_key_file, "r", encoding="utf-8") as f:
                api_key = f.read().strip()
        except OSError as exc:
            logger.error("无法读取 --api-key-file %s: %s", args.api_key_file, exc)
            return 2
        if not api_key:
            logger.error("--api-key-file %s 内容为空", args.api_key_file)
            return 2
        try:
            mode = os.stat(args.api_key_file).st_mode & 0o777
            if mode & 0o044:
                logger.warning(
                    "--api-key-file %s 权限为 %o, 建议 chmod 600 仅 owner 可读写",
                    args.api_key_file, mode,
                )
        except OSError:
            pass
    else:
        api_key = args.api_key if args.api_key is not None else os.getenv("MODEL_API_KEY", "")
    camera_map = parse_camera_map(args.camera_map) if args.camera_map else None

    # 客户端从官网复制的 wss 地址通常没有末尾 '/', 而模型端点要求路径尾斜杠
    # (缺省会得到 HTTP 301 拒握). 这里对用户透明地自动补齐, 不要求手动加.
    ws_url = args.ws_url
    if ws_url.endswith("/"):
        ws_url = ws_url[:-1]
    ws_url += "/"

    logger.info("=== RoboTwin 桥接层 ===")
    logger.info("模型 ws_url : %s", ws_url)
    logger.info("监听端口    : ws://%s:%d", args.host, args.port)
    logger.info("token       : %s", "已配置" if api_key else "未配置(模型侧可能不校验)")
    logger.info("camera_map  : %s", camera_map or "默认(cam_head->cam_high)")
    logger.info("model 适配  : %s", args.model or "auto(由模型 metadata 决定)")

    from server import BridgePolicyServer

    server = BridgePolicyServer(
        ws_url=ws_url,
        port=args.port,
        host=args.host,
        api_key=api_key,
        n_action_steps=args.n_action_steps,
        camera_map=camera_map,
        model_name=args.model,
        insecure=args.insecure,
        ca_cert=args.cacert,
    )

    stop_event = None  # 预留: 如需集成平台优雅退出可在此接入

    # SIGTERM(k8s 停 Pod / kill 默认信号) 也转为 KeyboardInterrupt, 确保 finally 清理执行
    def _sigterm_handler(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        server.start()
        logger.info("就绪! 在 RoboTwin 侧执行:")
        logger.info(
            "  python scripts/eval_policy_xpolicylab.py --protocol ws "
            "--host 127.0.0.1 --port %d --task_name <task> --task_config demo_clean "
            "--test_num 5 --policy_name bridge --root_dir $ROBOTWIN_ROOT", args.port,
        )
        # 阻塞直到 Ctrl+C / SIGTERM
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("收到退出信号, 正在关闭...")
    finally:
        server.stop()
        logger.info("桥接层已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())