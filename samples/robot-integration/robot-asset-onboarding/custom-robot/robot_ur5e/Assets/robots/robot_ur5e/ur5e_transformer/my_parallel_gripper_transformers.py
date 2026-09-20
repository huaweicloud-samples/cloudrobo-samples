"""Third-party value transformers for a parallel gripper (fingers <-> stroke).

This module demonstrates the ``r2c_sdk.transformers`` entry-point extension
mechanism.  It re-implements -- outside the SDK -- the parallel-gripper
finger/stroke conversions that the builtin ``parallel_gripper_fingers_to_stroke``
and ``parallel_gripper_stroke_to_fingers`` transformers provide, so a third
party can ship their own mapping rules without forking the SDK.

To make these classes discoverable:

1. Register them in ``pyproject.toml`` under ``[project.entry-points."r2c_sdk.transformers"]``
   (each name must point to an ``IValueTransformer`` **subclass**).
2. ``pip install -e .`` the package so the entry_point metadata is recorded.
3. Reference the registered name in a robot config YAML:

       mapping:
         # fingers -> stroke
         gripper_stroke:
           source: joint_states.position       # [left, right]
           transform: my_gripper_fingers_to_stroke
         # stroke -> fingers
         gripper_fingers:
           source: gripper_stroke
           transform: my_gripper_stroke_to_fingers

When a name collides with a builtin, the builtin always wins (see
``TransformerRegistry``), so the names below are prefixed to stay unique.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from r2c_sdk.core.interfaces import IValueTransformer


@dataclass(frozen=True)
class MyGripperFingersToStrokeTransformer(IValueTransformer):
    """平行夹爪两手指位置 -> 行程转换器(第三方实现)。

    行程 = 左手指位置 + 右手指位置(单位: 米)。
    两手指读数不一致时同样直接相加(如 左=0.02, 右=0.04 -> 行程=0.06)。

    输入: ``[left, right]`` 两元素序列(单位: 米)
    输出: 行程 float(单位: 米)

    与内置 ``parallel_gripper_fingers_to_stroke`` 等价, 但作为独立的第三方
    模块演示通过 entry-point 机制扩展转换器。
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        if isinstance(value, (str, bytes, bytearray, Mapping)):
            raise TypeError(
                "my_gripper_fingers_to_stroke expects a 2-element "
                "sequence [left, right]"
            )
        try:
            left, right = value
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "my_gripper_fingers_to_stroke expects a 2-element "
                "sequence [left, right]"
            ) from exc
        return (float(left) + float(right))*1250.


@dataclass(frozen=True)
class MyGripperStrokeToFingersTransformer(IValueTransformer):
    """平行夹爪行程 -> 两手指位置转换器(第三方实现, 逆变换)。

    行程 S -> ``[S/2, S/2]``(两手指对称运动, 各占行程一半)。

    输入: 行程 float(单位: 米)
    输出: ``[left, right]`` 两元素列表(单位: 米)

    与内置 ``parallel_gripper_stroke_to_fingers`` 等价, 这里同时提供反向变换
    使往返转换可逆, 便于在 device_to_r2c / r2c_to_device 两个方向成对使用。
    """

    def transform(self, value: Any, config: Any = None, context: Any = None) -> Any:
        if isinstance(value, (str, bytes, bytearray)):
            raise TypeError(
                "my_gripper_stroke_to_fingers expects a numeric "
                "stroke value in meters"
            )
        try:
            stroke = float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "my_gripper_stroke_to_fingers expects a numeric "
                "stroke value in meters"
            ) from exc
        return [stroke / 2500., stroke / 2500.]
