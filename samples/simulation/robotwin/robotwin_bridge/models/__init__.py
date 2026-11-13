"""
models — 模型适配层 (可插拔)

桥接层按“模型自报的 protocol”从 MODEL_REGISTRY 选择对应适配器,
由适配器负责 XPolicyLab 接口与具体远端模型之间的格式翻译。

新增模型适配器:
    1. 在 models/ 下新建模块 (如 xxx.py), 实现同一 XPolicyLab Model 接口;
    2. 在 MODEL_REGISTRY 注册 `"protocol名": 适配器类`;
    3. server 侧无需改动, 模型 metadata 携带该 protocol 即自动路由。
"""

from .crwam import CrWamModel

MODEL_REGISTRY = {
    "cr-wam": CrWamModel,   # metadata.protocol 使用连字符; 兼容别名见下
    "crwam": CrWamModel,
}

DEFAULT_MODEL = "cr-wam"


def resolve_model_name(name: str | None) -> str:
    """把用户/模型给的适配器名标准化到注册表键。"""
    if not name:
        return DEFAULT_MODEL
    key = name.strip().lower()
    if key in MODEL_REGISTRY:
        return key
    # 小写/去连字符兜底
    alias = {k.replace("-", ""): k for k in MODEL_REGISTRY}
    return alias.get(key.replace("-", ""), DEFAULT_MODEL)


def get_model(name: str | None, cfg: dict) -> object:
    """按模型适配器名实例化模型对象 (name=None 时用默认 cr-wam)。

    cfg 透传给适配器, 至少需含 ws_url; 可选 api_key/n_action_steps/camera_map。
    """
    resolved = resolve_model_name(name)
    try:
        cls = MODEL_REGISTRY[resolved]
    except KeyError:  # pragma: no cover - resolve_model_name 已兜底
        cls = MODEL_REGISTRY[DEFAULT_MODEL]
    return cls(cfg)


__all__ = ["MODEL_REGISTRY", "DEFAULT_MODEL", "resolve_model_name", "get_model", "CrWamModel"]