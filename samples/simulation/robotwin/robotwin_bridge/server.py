"""
server.py — 桥接层: 对接官方 XPolicyLab PolicyServer (仅作为接入方)

本文件不修改 RoboTwin / XPolicyLab 任何源码:
  - 通过 PYTHONPATH 仅“引入”官方 XPolicyLab 对外提供的类并按官方用法调用;
  - 桥接层是独立交付的进程, 与官方评测脚本通过标准 WS 协议对接;
  - 官方 PolicyServer(model, config) 的 model 由本桥接层提供, 对外暴露
    ws://<host>:<port> (官方 RoboTwin 脚本连接此端口, 官方代码零改动)。

需要 XPolicyLab 在 import 路径中 (客户随 RoboTwin 官方安装即可):
    git clone --recurse-submodules https://github.com/RoboTwin-Platform/RoboTwin.git
    export PYTHONPATH=$PYTHONPATH:$ROBOTWIN_ROOT/XPolicyLab:$ROBOTWIN_ROOT/XPolicyLab/integrations
"""

import asyncio
import logging
import threading
from typing import Any

from models import get_model, resolve_model_name
from policy_client import PolicyClient

logger = logging.getLogger(__name__)


def _import_xpolicylab_server():
    """只读引入官方 XPolicyLab 对外导出的类 (不改动其源码)."""
    try:
        from client_server.ws.model_server import (
            PolicyServer as XPLPolicyServer,
        )
        from client_server.ws.model_server import (
            PolicyServerConfig as XPLPolicyServerConfig,
        )
        return XPLPolicyServer, XPLPolicyServerConfig
    except ImportError as exc:
        raise RuntimeError(
            "无法导入 XPolicyLab 的 client_server.ws.model_server。\n"
            "请确保 RoboTwin 仓库 (含 XPolicyLab submodule) 已在 PYTHONPATH 中。"
        ) from exc


class BridgePolicyServer:
    """包装 XPolicyLab PolicyServer, 在后台线程跑 event loop。"""

    def __init__(
        self,
        ws_url: str,
        port: int,
        host: str = "0.0.0.0",
        api_key: str = "",
        n_action_steps: int | None = None,
        camera_map: dict[str, str] | None = None,
        model_name: str | None = None,
        insecure: bool = False,
        ca_cert: str | None = None,
    ):
        self._ws_url = ws_url
        self._host = host
        self._port = port
        self._api_key = api_key
        self._n_action_steps = n_action_steps
        self._camera_map = camera_map
        self._model_name = model_name
        self._insecure = insecure
        self._ca_cert = ca_cert

        self._model = None
        self._server = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self._port}"

    @property
    def model(self) -> object | None:
        return self._model

    def _detect_protocol(self, cfg: dict[str, Any]) -> str:
        """连接远端模型读取其自报 protocol (用于 auto 路由模型适配器)."""
        probe = PolicyClient(ws_url=cfg.get("ws_url", ""), api_key=cfg.get("api_key", ""),
                         insecure=cfg.get("insecure", False), ca_cert=cfg.get("ca_cert"))
        try:
            probe.ensure_features()
            feats = probe.features or {}
            return str(feats.get("protocol") or "cr-wam")
        finally:
            probe.close()

    def start(self) -> None:
        if self._thread is not None:
            logger.warning("[BridgeServer] already started")
            return

        XPLPolicyServer, XPLPolicyServerConfig = _import_xpolicylab_server()

        model_cfg: dict[str, Any] = {
            "ws_url": self._ws_url,
            "api_key": self._api_key,
            "insecure": self._insecure,
        }
        if self._ca_cert:
            model_cfg["ca_cert"] = self._ca_cert
        if self._n_action_steps is not None:
            model_cfg["n_action_steps"] = self._n_action_steps
        if self._camera_map:
            model_cfg["camera_map"] = self._camera_map
        model_name = self._model_name or "auto"
        if model_name == "auto":
            model_name = resolve_model_name(self._detect_protocol(model_cfg))
        else:
            model_name = resolve_model_name(model_name)
        logger.info("[BridgeServer] using model adapter: %s", model_name)
        self._model = get_model(model_name, model_cfg)

        self._server = XPLPolicyServer(
            self._model,
            XPLPolicyServerConfig(host=self._host, port=self._port),
        )

        self._loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._server.serve_forever())
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("[BridgeServer] event loop exited with error")

        self._thread = threading.Thread(
            target=_run_loop, name="robotwin-bridge-server", daemon=True
        )
        self._thread.start()

        # 等待端口就绪: 服务线程存活即视为已启动.
        # (原实现用裸 TCP 自连端口探测, 会在本机 websockets 侧制造
        #  "opening handshake failed: did not receive a valid HTTP request"
        #  的一次性 ERROR 噪声; 改为等待线程稳定, 不产生额外连接.)
        import time

        time.sleep(0.3)  # 给 serve_forever 在事件循环内 bind 监听端口的时间
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if self._thread.is_alive():
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(
                f"[BridgeServer] server did not open port {self._port} in time"
            )
        logger.info("[BridgeServer] policy server ready at %s", self.url)

    def stop(self) -> None:
        if self._loop is None or self._server is None:
            return
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._server.stop())
            )
        except Exception as exc:
            logger.warning("[BridgeServer] stop signalling failed: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                logger.warning(
                    "[BridgeServer] background thread did not terminate within 10s "
                    "after stop signal"
                )
        self._thread = None
        self._loop = None
        self._server = None
        logger.info("[BridgeServer] stopped")