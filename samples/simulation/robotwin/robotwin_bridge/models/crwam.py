"""
crwam.py — cr-wam 协议模型的 XPolicyLab Model 适配器 (首个实现)

被 models/get_model 按模型自报的 protocol 选中, 作为 XPolicyLab
PolicyServer 的 model 参数。内部持有 PolicyClient, 连接远端推理服务 (wss://...)。

XPolicyLab WS 协议调用的 Model 接口:
    prepare_case(case_meta)  — 每 episode 前传入 task/seed/instruction
    reset()                  — 每 episode 清理 state
    update_obs(obs)          — 每步传入 XPolicyLab 标准 obs (vision 已解码为 HWC uint8 RGB)
    get_action()             — 返回 action chunk (chunk_size, action_dim)
    on_trial_end(payload)    — 每 episode 结束回调 (可选)

新增模型适配器: 在 models/ 下新建模块并注册到 MODE_REGISTRY (见 __init__.py)。
"""

import logging
from typing import Any

import numpy as np

from policy_client import PolicyClient

logger = logging.getLogger(__name__)

# XPolicyLab camera 名 -> 远端推理模型期望的 observation.images.<name> 键
DEFAULT_CAMERA_MAP = {
    "cam_head": "cam_high",
    "cam_left_wrist": "cam_left_wrist",
    "cam_right_wrist": "cam_right_wrist",
}


class CrWamModel:
    """cr-wam 协议远端模型的适配器 (负责 XPolicyLab ↔ cr-wam 的格式翻译)."""

    def __init__(self, cfg: dict[str, Any]):
        ws_url = cfg.get("ws_url")
        if not ws_url:
            raise ValueError("cfg['ws_url'] (远端推理服务地址) 必须提供")

        self._client = PolicyClient(
            ws_url=ws_url,
            api_key=cfg.get("api_key", ""),
            n_action_steps=cfg.get("n_action_steps"),
            insecure=cfg.get("insecure", False),
            ca_cert=cfg.get("ca_cert"),
        )
        self._client.ensure_features()
        self._chunk_size = self._client.chunk_size or 1

        self._camera_map = dict(cfg.get("camera_map") or DEFAULT_CAMERA_MAP)
        self._obs = None
        self._instruction = ""
        self._action_type = "joint"

        logger.info(
            "[CrWamModel] connected to %s, chunk_size=%s, camera_map=%s",
            ws_url, self._chunk_size, self._camera_map,
        )

    # ── XPolicyLab Model 协议 ──

    def prepare_case(self, case_meta: dict[str, Any]):
        self._instruction = str(case_meta.get("instruction") or "")
        self._action_type = str(case_meta.get("action_type") or "joint")
        logger.debug("[CrWamModel] prepare_case: %s", case_meta)

    def reset(self):
        self._client.reset()
        self._obs = None
        logger.debug("[CrWamModel] reset")

    def update_obs(self, obs: dict[str, Any]):
        self._obs = obs
        if obs and (obs.get("instruction") or obs.get("instructions")):
            instr = obs.get("instruction") or (obs.get("instructions") or [None])[0]
            if instr:
                self._instruction = str(instr)

    def get_action(self) -> np.ndarray:
        if self._obs is None:
            raise RuntimeError("get_action called before any update_obs")
        batch = self._obs_to_batch(self._obs)
        chunk = self._client.predict_action_chunk(batch)
        chunk = np.asarray(chunk)
        if chunk.ndim == 1:
            chunk = chunk[None, :]
        logger.debug(
            "[CrWamModel] get_action -> chunk %s, action_type=%s",
            chunk.shape, self._action_type,
        )
        return chunk

    def on_trial_end(self, payload: dict[str, Any]):
        logger.debug("[CrWamModel] trial_end: %s", payload)

    def close(self):
        self._client.close()

    # ── obs 转换 (cr-wam 格式) ──

    def _obs_to_batch(self, obs: dict[str, Any]) -> dict[str, Any]:
        batch: dict[str, Any] = {}

        vision = obs.get("vision") or {}
        for xpl_cam, remote_name in self._camera_map.items():
            cam = vision.get(xpl_cam)
            if not cam:
                continue
            color = cam.get("color")
            if color is None:
                continue
            arr = np.asarray(color)
            if arr.ndim == 3:
                batch[f"observation.images.{remote_name}"] = arr  # (H,W,3) uint8
            elif arr.ndim == 4:
                batch[f"observation.images.{remote_name}"] = arr[0]  # 剥掉 batch
            elif arr.ndim == 2:
                batch[f"observation.images.{remote_name}"] = arr  # (H,W)
            else:
                continue

        if not batch:
            raise ValueError(
                f"obs 不包含任何 vision camera; 收到 keys: {sorted(vision.keys())}"
            )

        state_vec = self._pack_state(obs.get("state") or {})
        if state_vec is not None:
            batch["observation.state"] = state_vec[None, ...]

        batch["task"] = [self._instruction]
        return batch

    @staticmethod
    def _pack_state(state: dict[str, Any]) -> np.ndarray | None:
        """XPolicyLab state dict -> RoboTwin 标准 joint 向量 [L6, L1, R6, R1]。"""
        parts = []
        for key in (
            "left_arm_joint_state",
            "left_ee_joint_state",
            "right_arm_joint_state",
            "right_ee_joint_state",
        ):
            val = state.get(key)
            if val is None:
                continue
            arr = np.asarray(val, dtype=np.float32).reshape(-1)
            parts.append(arr)
        if not parts:
            return None
        return np.concatenate(parts).astype(np.float32)