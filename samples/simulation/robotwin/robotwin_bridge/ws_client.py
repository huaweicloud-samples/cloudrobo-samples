"""
ws_client.py — 独立的 WebSocket 客户端 (wss + TLS + Bearer token)

功能:
  - 支持 wss (TLS1.2 + 指定 cipher)
  - TLS 证书校验三态: 严格(默认) / --cacert 私有CA / --insecure 跳过身份验证(仍加密)
  - 连接时注入 Authorization: Bearer <api_key>
  - msgpack 序列化 (内置 numpy 数组/标量处理)
  - 连接后接收并解析服务端 metadata
"""
import ssl
import functools
import logging
from typing import Any, Dict

import msgpack
import numpy as np
import websockets.sync.client

logger = logging.getLogger(__name__)

CIPHERS = (
    "ECDHE-ECDSA-AES256-GCM-SHA384:"
    "ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:"
    "ECDHE-RSA-CHACHA20-POLY1305"
)

def create_ssl_context(insecure: bool = False, ca_cert: str | None = None) -> ssl.SSLContext:
    """创建客户端 SSLContext (TLS1.2 + 指定 cipher).

    Args:
        insecure: True=不校验证书 (仅受控内网/评估后使用)
        ca_cert: 私有 CA 证书路径, 用于严格校验
    默认 (insecure=False, 无 ca_cert): 严格校验系统可信 CA.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(CIPHERS)
    context.options |= ssl.OP_NO_COMPRESSION
    context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif ca_cert:
        context.load_verify_locations(ca_cert)
    return context


# msgpack 序列化 / 反序列化 (支持 numpy)
def pack_array(obj: Any) -> Any:
    """将 numpy 数组和标量序列化为 msgpack 兼容格式."""
    if (isinstance(obj, (np.ndarray, np.generic))) and obj.dtype.kind in ("V", "O", "c"):
        raise ValueError(f"Unsupported dtype: {obj.dtype}")

    if isinstance(obj, np.ndarray):
        return {
            b"__ndarray__": True,
            b"data": obj.tobytes(),
            b"dtype": obj.dtype.str,
            b"shape": obj.shape,
        }

    if isinstance(obj, np.generic):
        return {
            b"__npgeneric__": True,
            b"data": obj.item(),
            b"dtype": obj.dtype.str,
        }

    return obj


def unpack_array(obj: Any) -> Any:
    """从 msgpack 兼容格式反序列化 numpy 数组和标量."""
    if isinstance(obj, dict) and b"__ndarray__" in obj:
        return np.ndarray(
            buffer=obj[b"data"],
            dtype=np.dtype(obj[b"dtype"]),
            shape=obj[b"shape"]
        )

    if isinstance(obj, dict) and b"__npgeneric__" in obj:
        return np.dtype(obj[b"dtype"]).type(obj[b"data"])

    return obj


def packb(obj: Any, **kwargs) -> bytes:
    return msgpack.packb(obj, default=pack_array, **kwargs)


def unpackb(data: bytes, **kwargs) -> Any:
    return msgpack.unpackb(data, object_hook=unpack_array, **kwargs)


_Packer = functools.partial(msgpack.Packer, default=pack_array)
Packer = _Packer


class WSClient:
    """同步 WebSocket 客户端 (wss + Bearer token + msgpack)."""

    def __init__(self, url: str, api_key: str = "", max_size: int = None,
                 insecure: bool = False, ca_cert: str | None = None):
        self.url = url
        self.max_size = max_size
        self.conn = None
        self._packer = Packer()
        self.metadata = None
        self.api_key = api_key
        self._insecure = insecure
        self._ca_cert = ca_cert

    def connect(self, wait_for_metadata: bool = True, max_wait_seconds: int = 30):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        logger.info(f"Connecting to {self.url}...")
        self.conn = websockets.sync.client.connect(
            self.url,
            compression=None,
            max_size=self.max_size,
            ping_interval=10,
            ping_timeout=60,
            additional_headers=headers,
            ssl=create_ssl_context(self._insecure, self._ca_cert) if self.url.startswith("wss://") else None
        )

        if wait_for_metadata:
            data = self.conn.recv()
            self.metadata = unpackb(data)

        logger.info(f"Connected to {self.url}")

    def send_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # 入口: 连接未建立时主动建连(带重试), 不再直接抛
        if self.conn is None:
            self._reconnect_with_retry("入口建连")

        packed_data = self._packer.pack(data)
        logger.debug(
            "send_request >> 发给模型 keys=%s payload=%dB",
            list(data.keys()), len(packed_data),
        )
        try:
            self.conn.send(packed_data)
        except Exception:
            logger.warning("连接已断开(可能被远端空闲超时/网关掐断), 进入重连重试...")
            self.close()
            self._reconnect_with_retry("发送失败后重连")
            self.conn.send(packed_data)

        response = self.conn.recv()

        if isinstance(response, str):
            raise ValueError(f"Error in inference server:\n{response}")

        return unpackb(response)

    def _reconnect_with_retry(self, reason: str, max_attempts: int = 30, interval: float = 5.0):
        """带间隔的重连, 直到远端恢复 (覆盖瞬时 502/网关抖动导致连接被断)."""
        import time as _time

        logger.warning(f"[{reason}] 开始重连 (最多 {max_attempts} 次, 每 {interval:.0f}s)")
        for attempt in range(1, max_attempts + 1):
            try:
                self.connect(wait_for_metadata=True)
                logger.info(f"重连成功 (第 {attempt} 次)")
                return
            except Exception as _e:
                logger.warning(
                    f"重连第 {attempt}/{max_attempts} 次失败: {_e!r}, {interval:.0f}s 后再试"
                )
                _time.sleep(interval)
        raise RuntimeError(f"重连 {max_attempts} 次仍失败, 放弃本次请求")

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
            logger.info(f"Connection to {self.url} closed")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()