"""
cloudrobo_wm.py — CloudRobo-WM 模型适配器 (cr-wam 传输协议 + history 语义)

服务端要求 history.stride=1: 每个控制步都必须上报一次 obs;
每 32 步返回一个 action chunk; 每个 case 首帧携带 reset 清历史。
"""
import logging
from typing import Any

import numpy as np

from policy_client import PolicyClient

logger = logging.getLogger(__name__)

DEFAULT_CAMERA_MAP = {
    "cam_head": "cam_high",
    "cam_left_wrist": "cam_left_wrist",
    "cam_right_wrist": "cam_right_wrist",
}


class CloudRoboWMModel:
    """CloudRobo-WM 远端模型适配器 (XPolicyLab Model 接口)."""

    def __init__(self, cfg: dict[str, Any]):
        ws_url = cfg.get("ws_url")
        if not ws_url:
            raise ValueError("cfg['ws_url'] (远端推理服务地址) 必须提供")
        self._ws_url = ws_url
        self._client = PolicyClient(
            ws_url=ws_url,
            api_key=cfg.get("api_key", ""),
            n_action_steps=cfg.get("n_action_steps"),
            insecure=cfg.get("insecure", False),
            ca_cert=cfg.get("ca_cert"),
        )
        self._camera_map = dict(cfg.get("camera_map") or DEFAULT_CAMERA_MAP)
        self._instruction = ""
        self._action_type = "joint"
        self._last_action: np.ndarray | None = None
        self._client.connect()
        logger.info("[CloudRoboWM] remote=%s chunk_size=%s", ws_url, self._client.chunk_size)

    # ── XPolicyLab Model 协议 ──

    def prepare_case(self, case_meta: dict[str, Any]):
        self._instruction = str((case_meta or {}).get("instruction") or "")
        self._action_type = str((case_meta or {}).get("action_type") or "joint")
        self._last_action = None
        self._client.reset()

    def reset(self):
        self._last_action = None
        self._client.reset()

    def update_obs(self, obs: dict[str, Any]):
        """每步上报 obs; 服务端按 stride=1 累积历史, 顺带返回当前 chunk."""
        if obs and (obs.get("instruction") or obs.get("instructions")):
            instruction = obs.get("instruction") or (obs.get("instructions") or [None])[0]
            if instruction:
                self._instruction = str(instruction)
        payload = self._obs_to_batch(obs)
        action = self._client.step(payload)
        if action is not None:
            self._last_action = action

    def get_action(self) -> np.ndarray:
        if self._last_action is None:
            raise RuntimeError("get_action called before any update_obs produced an action")
        return self._last_action

    def on_trial_end(self, payload: dict[str, Any]):
        self._last_action = None

    def close(self):
        self._client.close()

    # ── XPolicyLab obs -> cr-wam payload ──

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
            if arr.ndim == 4:
                arr = arr[0]
            if arr.ndim == 3:
                batch[f"observation.images.{remote_name}"] = arr.astype(np.uint8, copy=False)
        if not batch:
            raise ValueError(
                f"obs 不包含任何 vision camera; 收到 keys: {sorted(vision.keys())}"
            )
        state_vec = self._pack_state(obs.get("state") or {})
        if state_vec is not None:
            batch["observation.state"] = state_vec[None, ...]
        batch["task"] = self._instruction
        return batch

    @staticmethod
    def _pack_state(state: dict[str, Any]) -> np.ndarray | None:
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
            parts.append(np.asarray(val, dtype=np.float32).reshape(-1))
        if not parts:
            return None
        return np.concatenate(parts).astype(np.float32)


CrWamModel = CloudRoboWMModel
