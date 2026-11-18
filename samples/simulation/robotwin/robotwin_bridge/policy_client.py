"""
policy_client.py — CloudRobo-WM 远端客户端 (cr-wam 传输协议, 含 history 约定)

服务端 metadata 自报:
    action:  shape [32,14], chunk_size=32
    history: stride=1, chunk_size=32, reset_key="reset"

因此:
  * 每个控制步都要发一次 obs (stride=1), 服务端据此累积历史帧
  * 每 32 步拿到一个新 chunk (chunk_size=32)
  * 每个 case 的第一帧请求携带 reset=True, 清空服务端历史
"""
import logging
from typing import Any

import numpy as np

from ws_client import WSClient

logger = logging.getLogger(__name__)


class PolicyClient:
    """CloudRobo-WM 远端推理客户端 (wss + Bearer + msgpack)."""

    def __init__(self, ws_url: str, api_key: str = "", n_action_steps: int | None = None,
                 max_size: int = None, insecure: bool = False, ca_cert: str | None = None):
        self._ws_url = ws_url
        self._api_key = api_key
        self._n_action_steps = n_action_steps
        self._max_size = max_size
        self._insecure = insecure
        self._ca_cert = ca_cert
        self._conn: WSClient | None = None
        self._metadata: dict = {}
        self._output_key = "action"
        self._chunk_size: int | None = None
        self._need_reset = True

    @property
    def features(self) -> dict:
        return self._metadata

    @property
    def chunk_size(self) -> int | None:
        return self._chunk_size

    def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = WSClient(self._ws_url, api_key=self._api_key, max_size=self._max_size,
                              insecure=self._insecure, ca_cert=self._ca_cert)
        self._conn.connect(wait_for_metadata=True)
        self._metadata = self._conn.metadata or {}
        action_meta = self._metadata.get("action") or {}
        self._chunk_size = action_meta.get("chunk_size")
        logger.info("[PolicyClient] connected: protocol=%s chunk_size=%s history=%s",
                    self._metadata.get("protocol"), self._chunk_size,
                    self._metadata.get("history"))

    def ensure_features(self) -> dict:
        self.connect()
        return self._metadata

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as e:
                logger.error("policy client close error: %s", e)
            self._conn = None

    def reset(self) -> None:
        """标记下一个请求携带 reset=True, 由服务端清空历史."""
        self._need_reset = True

    def step(self, payload: dict[str, Any]) -> np.ndarray | None:
        """发送一步 obs(含历史推进入), 返回该步的 action chunk(可能为 None)."""
        self.connect()
        if self._need_reset:
            payload = dict(payload)
            payload[self._reset_key()] = True
            self._need_reset = False
        response = self._conn.send_request(payload)
        if isinstance(response, str):
            raise RuntimeError(f"Server-side error: {response[:2000]}")
        actions = response.get(self._output_key)
        if actions is None:
            for key in ("actions", "joint14"):
                if key in response:
                    self._output_key = key
                    actions = response[key]
                    break
        if actions is None:
            return None
        return np.asarray(actions)

    def _reset_key(self) -> str:
        history = self._metadata.get("history") or {}
        return str(history.get("reset_key") or "reset")
