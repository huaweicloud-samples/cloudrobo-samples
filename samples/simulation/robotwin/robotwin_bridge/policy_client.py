"""
policy_client.py — 远端模型 (cr-wam 协议) 推理客户端

在 WSClient (wss + Bearer token) 之上提供:
  - action queue (一次推理拿 chunk, 多步消费)
  - predict_action_chunk(obs) -> (chunk_size, action_dim)
"""
import logging
import time
from collections import deque
from typing import Any

import numpy as np

from ws_client import WSClient

logger = logging.getLogger(__name__)


class PolicyClient:
    """远端推理客户端 (独立交付版, 不依赖平台 common/env_config)."""

    def __init__(
        self,
        ws_url: str,
        api_key: str = "",
        n_action_steps: int | None = None,
        max_size: int = None,
        insecure: bool = False,
        ca_cert: str | None = None,
    ):
        """
        Args:
            ws_url: 远端推理服务 WebSocket URL (通常 wss://...)
            api_key: 鉴权 token, 拼入 Authorization: Bearer <api_key>
            n_action_steps: 每次拿到 chunk 后消费多少步, 用满整个 chunk 就传 None
            max_size: 单条消息最大 bytes
        """
        self._ws_url = ws_url
        self._api_key = api_key
        self._max_size = max_size
        self._n_action_steps = n_action_steps
        self._insecure = insecure
        self._ca_cert = ca_cert

        self._client: WSClient | None = None
        self._features: dict | None = None
        self._chunk_size: int | None = None
        self._action_queue: deque[np.ndarray] = deque()
        self._output_key: str | None = None

    @property
    def features(self) -> dict | None:
        return self._features

    @property
    def chunk_size(self) -> int | None:
        return self._chunk_size

    def connect(self) -> None:
        if self._client is not None:
            return
        self._client = WSClient(
            url=self._ws_url,
            api_key=self._api_key,
            max_size=self._max_size,
            insecure=self._insecure,
            ca_cert=self._ca_cert,
        )
        self._client.connect(wait_for_metadata=True)
        self._features = self._client.metadata or {}
        logger.info(f"[PolicyClient] Server metadata: {self._features}")

        out_feat = self._features.get("output_features", {}) if isinstance(self._features, dict) else {}
        if out_feat:
            first_action_feat = next(iter(out_feat.values()), {})
            if isinstance(first_action_feat, dict):
                self._chunk_size = first_action_feat.get("chunk_size")
            self._output_key = next(iter(out_feat.keys()))
        if self._output_key is None:
            self._output_key = "action"
        logger.info(
            f"[PolicyClient] output_key={self._output_key}, chunk_size={self._chunk_size}"
        )

    def ensure_features(self) -> dict:
        if self._features is None:
            self.connect()
        return self._features or {}

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.error(f"policy client connect close error: {e}")
            self._client = None

    def predict_action_chunk(self, obs: dict[str, Any]) -> np.ndarray:
        """发送一次 obs, 返回完整 action chunk (chunk_size, action_dim)."""
        if self._client is None:
            self.connect()

        t0 = time.perf_counter()
        response = self._client.send_request(obs)
        latency_ms = (time.perf_counter() - t0) * 1000

        if isinstance(response, str):
            logger.error(f"[PolicyClient] Server error: {response[:2000]}")
            raise RuntimeError("Server-side error during inference")

        if self._output_key not in response:
            for fallback_key in ("action", "actions"):
                if fallback_key in response:
                    self._output_key = fallback_key
                    break
            else:
                raise KeyError(
                    f"Server response missing action key, got keys: {list(response.keys())}"
                )

        actions = np.asarray(response[self._output_key])
        logger.info(
            f"[PolicyClient] inference: shape={actions.shape}, latency={latency_ms:.1f}ms"
        )
        return actions

    def select_action(self, obs: dict[str, Any]) -> np.ndarray:
        """LeRobot 风格单步动作接口; 队列空时请求新 chunk."""
        if len(self._action_queue) == 0:
            chunk = self.predict_action_chunk(obs)
            n_steps = self._n_action_steps or self._chunk_size or chunk.shape[0]
            n_steps = min(n_steps, chunk.shape[0])
            for i in range(n_steps):
                self._action_queue.append(chunk[i])
        return self._action_queue.popleft()

    def reset(self) -> None:
        """每 episode 开始调用, 清空 action queue."""
        self._action_queue.clear()