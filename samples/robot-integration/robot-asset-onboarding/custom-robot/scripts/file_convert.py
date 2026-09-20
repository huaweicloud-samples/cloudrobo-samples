# -*- coding: utf-8 -*-
"""
Patch a robot description JSON according to custom rules, parameterized by a YAML config.

使用方法：
python3 file_convert.py --json input_prefab/qinglong_input.prefab --yaml config/qinglong_config.yaml  --out output_prefab/qinglong_output.prefab
"""
import json, yaml, random
from pathlib import Path
from collections import OrderedDict


def load_ordered_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=OrderedDict)


def dump_ordered_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=4)


def collect_ids(obj, ids):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "Id" and isinstance(v, int):
                ids.add(v)
            collect_ids(v, ids)
    elif isinstance(obj, list):
        for item in obj:
            collect_ids(item, ids)


def collect_string_values(obj, target_key, values):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == target_key and isinstance(v, str):
                values.add(v)
            collect_string_values(v, target_key, values)
    elif isinstance(obj, list):
        for item in obj:
            collect_string_values(item, target_key, values)


def validate_config_against_prefab(data, cfg):
    """Validate YAML references against the original prefab content."""
    prefab_joint_names = set()
    prefab_entity_names = set()
    collect_string_values(data, "Joint Name", prefab_joint_names)
    collect_string_values(data, "Name", prefab_entity_names)

    missing_topic_joints = []
    topic_controllers = cfg.get("topic_controller", {}) or {}
    if isinstance(topic_controllers, dict):
        for controller_name, spec in topic_controllers.items():
            if not isinstance(spec, dict):
                continue
            joint_names = spec.get("joint_names", []) or []
            if not isinstance(joint_names, list):
                continue
            for joint_name in joint_names:
                if joint_name not in prefab_joint_names:
                    missing_topic_joints.append(
                        f"topic_controller.{controller_name}.joint_names: {joint_name}"
                    )

    missing_init_joints = []
    init_joints = cfg.get("init_joints", {}) or {}
    if isinstance(init_joints, dict):
        for joint_name in init_joints.keys():
            if joint_name not in prefab_joint_names:
                missing_init_joints.append(f"init_joints: {joint_name}")

    missing_camera_links = []
    cameras = cfg.get("camera", {}) or {}
    if isinstance(cameras, dict):
        for camera_key, cam_cfg in cameras.items():
            if not isinstance(cam_cfg, dict):
                continue
            cam_name = cam_cfg.get("CameraName", camera_key)
            camera_link = cam_cfg.get("CameraLink", cam_name)
            if camera_link not in prefab_entity_names:
                missing_camera_links.append(f"camera.{camera_key}.CameraLink: {camera_link}")

    errors = []
    if missing_topic_joints:
        errors.append(
            "topic_controller 中以下 joint_names 在 prefab 的 Joint Name 中不存在:\n"
            + "  - "
            + "\n  - ".join(missing_topic_joints)
        )
    if missing_init_joints:
        errors.append(
            "init_joints 中以下关节在 prefab 的 Joint Name 中不存在:\n"
            + "  - "
            + "\n  - ".join(missing_init_joints)
        )
    if missing_camera_links:
        errors.append(
            "camera 中以下 CameraLink 在 prefab 的实体 Name 中不存在:\n"
            + "  - "
            + "\n  - ".join(missing_camera_links)
        )

    if errors:
        raise ValueError("YAML 配置校验失败：\n" + "\n\n".join(errors))


def next_unique_id(existing_ids):
    # 生成 19 位范围的随机 Id，避免与已有冲突
    base = 10**18 + random.randrange(10**17, 9 * 10**17)
    while base in existing_ids:
        base += 1
    existing_ids.add(base)
    return base


def insert_after_key(odict, after_key, new_key, new_value):
    """Return a NEW OrderedDict where (new_key:new_value) is placed right after 'after_key'."""
    if not isinstance(odict, OrderedDict):
        odict = OrderedDict(odict)
    new_od = OrderedDict()
    inserted = False
    for k, v in odict.items():
        new_od[k] = v
        if k == after_key and new_key not in odict:
            new_od[new_key] = new_value
            inserted = True
    if not inserted and new_key not in new_od:
        new_od[new_key] = new_value
    return new_od


def set_after_key(odict, after_key, key, value):
    """Set key to value and place it right after after_key when possible."""
    if not isinstance(odict, OrderedDict):
        odict = OrderedDict(odict)
    cleaned = OrderedDict((k, v) for k, v in odict.items() if k != key)
    return insert_after_key(cleaned, after_key, key, value)


def upsert_at_end(odict, key, value):
    """Remove 'key' if present, then append it at the end with 'value'."""
    if not isinstance(odict, OrderedDict):
        odict = OrderedDict(odict)
    if key in odict:
        odict = OrderedDict((k, v) for k, v in odict.items() if k != key)
    odict[key] = value
    return odict


def process_mass_and_joint(obj):
    """Traverse and modify:
    - After "Mass": add "Gravity Enabled": false
    - When "Articulation Joint Type" in dict: add/replace "Motor configuration" in the SAME dict (at end)
    """
    if isinstance(obj, dict):
        # 1) Mass -> Gravity Enabled
        if "Mass" in obj:
            obj = set_after_key(obj, "Mass", "Gravity Enabled", False)
        # 2) Articulation Joint Type -> Motor configuration
        if "Articulation Joint Type" in obj:
            motor_cfg = OrderedDict(
                [
                    ("UseMotor", True),
                    ("ForceLimit", 10000.0),
                    ("Stiffness", 5000.0),
                    ("Damping", 1000.0),
                ]
            )
            obj = upsert_at_end(obj, "Motor configuration", motor_cfg)
        # recurse
        keys = list(obj.keys())
        for k in keys:
            obj[k] = process_mass_and_joint(obj[k])
        return obj
    elif isinstance(obj, list):
        return [process_mass_and_joint(x) for x in obj]
    else:
        return obj


def find_entities_container(d):
    return d.get("Entities", {}) if isinstance(d, dict) else {}


def ensure_namespace_configuration(components):
    """Add 'Namespace Configuration' under ROS2FrameEditorComponent/ROS2FrameConfiguration."""
    rfec = components.get("ROS2FrameEditorComponent")
    if isinstance(rfec, dict):
        cfg = rfec.get("ROS2FrameConfiguration")
        if isinstance(cfg, dict):
            cfg_od = OrderedDict(cfg)
            ns_cfg = OrderedDict([("Namespace Strategy", 1)])
            cfg_od = upsert_at_end(cfg_od, "Namespace Configuration", ns_cfg)
            rfec["ROS2FrameConfiguration"] = cfg_od


def clean_existing_joint_components(comps):
    """Remove any existing components that our script would re-inject to avoid duplicates."""
    prefixes = [
        "JointsArticulationControllerComponent",
        "JointsManipulationEditorComponent",
        "JointsPositionsEditorComponent",
        "JointsTrajectoryComponent",
    ]
    cleaned = OrderedDict()
    for k, v in comps.items():
        drop = False
        for p in prefixes:
            if k == p or (isinstance(k, str) and k.startswith(p + "_")):
                drop = True
                break
        if not drop:
            cleaned[k] = v
    return cleaned


def clean_existing_camera_component(comps):
    """Remove existing ROS2CameraSensorEditorComponent to avoid duplicates."""
    cleaned = OrderedDict()
    for k, v in comps.items():
        if k == "ROS2CameraSensorEditorComponent":
            # drop it; we'll re-insert
            continue
        cleaned[k] = v
    return cleaned


def insert_components_after_visibility(comps, items):
    """Insert (name, dict) pairs right after EditorVisibilityComponent; append if not found."""
    if not isinstance(comps, OrderedDict):
        comps = OrderedDict(comps)
    ordered_keys = list(comps.keys())
    insert_index = None
    for idx, name in enumerate(ordered_keys):
        if name == "EditorVisibilityComponent":
            insert_index = idx + 1
            break
    if insert_index is None:
        insert_index = len(ordered_keys)
    new_items = []
    for i in range(insert_index):
        if i < len(ordered_keys):
            k = ordered_keys[i]
            new_items.append((k, comps[k]))
    for nm, cobj in items:
        new_items.append((nm, cobj))
    for i in range(insert_index, len(ordered_keys)):
        k = ordered_keys[i]
        new_items.append((k, comps[k]))
    return OrderedDict(new_items)


def build_joint_components(
    new_id_cb, init_joints, topic_controllers, moveit_controller
):
    """Return a list of (name, component_obj) to be inserted, in order."""
    items = []

    # Component 1: JointsArticulationControllerComponent
    comp1_name = "JointsArticulationControllerComponent"
    comp1 = OrderedDict(
        [
            ("$type", "GenericComponentWrapper"),
            ("Id", new_id_cb()),
            (
                "m_template",
                OrderedDict([("$type", "JointsArticulationControllerComponent")]),
            ),
        ]
    )
    items.append((comp1_name, comp1))

    # Component 2: JointsManipulationEditorComponent
    init_pos_pairs = []
    if isinstance(init_joints, dict):
        for jn, val in init_joints.items():  # 保持 YAML 顺序
            try:
                init_pos_pairs.append([jn, float(val)])
            except Exception:
                init_pos_pairs.append([jn, 0.0])
    comp2_name = "JointsManipulationEditorComponent"
    comp2 = OrderedDict(
        [
            ("$type", "JointsManipulationEditorComponent"),
            ("Id", new_id_cb()),
            (
                "JointStatePublisherConfiguration",
                OrderedDict(
                    [
                        (
                            "Topic",
                            OrderedDict([("QoS", OrderedDict([("Reliability", 1)]))]),
                        ),
                        ("Frequency", 100.0),
                    ]
                ),
            ),
            ("Initial positions", init_pos_pairs),
        ]
    )
    items.append((comp2_name, comp2))

    # Component 3: loop
    loop_idx = 0
    if isinstance(topic_controllers, dict):
        for _, spec in topic_controllers.items():
            loop_idx += 1
            cname = (
                "JointsPositionsEditorComponent"
                if loop_idx == 1
                else f"JointsPositionsEditorComponent_{loop_idx}"
            )
            topic = ""
            jnames = []
            if isinstance(spec, dict):
                topic = spec.get("topic_name", "")
                jnames = list(spec.get("joint_names", []))
            comp = OrderedDict(
                [
                    ("$type", "JointsPositionsEditorComponent"),
                    ("Id", new_id_cb()),
                    (
                        "topicConfiguration",
                        OrderedDict(
                            [
                                ("Topic", topic),
                                ("QoS", OrderedDict([("Reliability", 1)])),
                            ]
                        ),
                    ),
                    ("jointNames", jnames),
                ]
            )
            items.append((cname, comp))

    # Component 4: JointsTrajectoryComponent
    comp4_name = "JointsTrajectoryComponent"
    comp4 = OrderedDict(
        [
            ("$type", "GenericComponentWrapper"),
            ("Id", new_id_cb()),
            (
                "m_template",
                OrderedDict(
                    [
                        ("$type", "JointsTrajectoryComponent"),
                        ("Action name", moveit_controller),
                    ]
                ),
            ),
        ]
    )
    items.append((comp4_name, comp4))
    return items


def build_camera_component(new_id_cb, cam_name, cam_cfg):
    """Construct ROS2CameraSensorEditorComponent based on YAML cam_cfg."""
    # 读取参数，提供合理的默认值
    vdeg = float(cam_cfg.get("VerticalFieldOfViewDeg", 65.0))
    width = int(cam_cfg.get("Width", 640))
    height = int(cam_cfg.get("Height", 480))
    depth = bool(cam_cfg.get("Depth", False))
    clip_near = float(cam_cfg.get("ClipNear", 0.01))
    clip_far = float(cam_cfg.get("ClipFar", 100.0))
    freq = float(cam_cfg.get("Frequency", 10.0))

    # Topic 前缀为 CameraName
    prefix = str(cam_cfg.get("CameraName", cam_name)).strip() or cam_name

    comp = OrderedDict(
        [
            ("$type", "ROS2CameraSensorEditorComponent"),
            ("Id", new_id_cb()),
            (
                "CameraSensorConfig",
                OrderedDict(
                    [
                        ("VerticalFieldOfViewDeg", vdeg),
                        ("Width", width),
                        ("Height", height),
                        ("Depth", depth),
                        ("ClipNear", clip_near),
                        ("ClipFar", clip_far),
                    ]
                ),
            ),
            (
                "SensorConfig",
                OrderedDict(
                    [
                        ("Frequency (HZ)", freq),
                        (
                            "Publishers",
                            OrderedDict(
                                [
                                    (
                                        "Color Camera Info",
                                        OrderedDict(
                                            [
                                                (
                                                    "Type",
                                                    "sensor_msgs::msg::CameraInfo",
                                                ),
                                                (
                                                    "Topic",
                                                    f"/{prefix}/color_camera_info",
                                                ),
                                            ]
                                        ),
                                    ),
                                    (
                                        "Color Image",
                                        OrderedDict(
                                            [
                                                ("Type", "sensor_msgs::msg::Image"),
                                                (
                                                    "Topic",
                                                    f"/{prefix}/camera_image_color",
                                                ),
                                                (
                                                    "QoS",
                                                    OrderedDict([("Reliability", 1)]),
                                                ),
                                            ]
                                        ),
                                    ),
                                    (
                                        "Depth Camera Info",
                                        OrderedDict(
                                            [
                                                (
                                                    "Type",
                                                    "sensor_msgs::msg::CameraInfo",
                                                ),
                                                (
                                                    "Topic",
                                                    f"/{prefix}/depth_camera_info",
                                                ),
                                            ]
                                        ),
                                    ),
                                    (
                                        "Depth Image",
                                        OrderedDict(
                                            [
                                                ("Type", "sensor_msgs::msg::Image"),
                                                (
                                                    "Topic",
                                                    f"/{prefix}/camera_image_depth",
                                                ),
                                            ]
                                        ),
                                    ),
                                ]
                            ),
                        ),
                    ]
                ),
            ),
        ]
    )
    return ("ROS2CameraSensorEditorComponent", comp)


def convert_prefab(json_path, yaml_path, out_path):
    """Convert one prefab file using a YAML config and write the result."""
    # Load data
    data = load_ordered_json(json_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    validate_config_against_prefab(data, cfg)

    topic_controllers = cfg.get("topic_controller", {}) or {}
    moveit_controller = cfg.get("moveit_controller", "") or ""
    if moveit_controller:
        moveit_controller = moveit_controller.rstrip("/")
        if not moveit_controller.endswith("/follow_joint_trajectory"):
            moveit_controller += "/follow_joint_trajectory"
    init_joints = cfg.get("init_joints", {}) or {}
    target_entity_name = cfg.get("fixed_base", "virtual_odom")

    # 功能1 & 功能2：全局遍历
    data = process_mass_and_joint(data)

    # Collect existing Ids
    existing_ids = set()
    collect_ids(data, existing_ids)

    def new_id():
        return next_unique_id(existing_ids)

    # 定位实体容器
    entities = find_entities_container(data)

    # 功能3：处理 fixed_base 实体
    target_key = None
    if isinstance(entities, dict):
        for k, ent in entities.items():
            if isinstance(ent, dict) and ent.get("Name") == target_entity_name:
                target_key = k
                break

    if target_key is None:
        raise ValueError(
            f"fixed_base '{target_entity_name}' 在 prefab 的实体 Name 中不存在"
        )

    ent = entities[target_key]
    comps = ent.get("Components", OrderedDict())
    if not isinstance(comps, OrderedDict):
        comps = OrderedDict(comps)

    # 3.1 Fixed Base after entityId
    ealc = comps.get("EditorArticulationLinkComponent")
    if isinstance(ealc, dict):
        ac = ealc.get("ArticulationConfiguration")
        if isinstance(ac, dict):
            ac_od = OrderedDict(ac)
            if "entityId" in ac_od:
                ac_od = insert_after_key(ac_od, "entityId", "Fixed Base", True)
            else:
                ac_od = upsert_at_end(ac_od, "Fixed Base", True)
            ealc["ArticulationConfiguration"] = ac_od

    # 3.3 Namespace Configuration
    ensure_namespace_configuration(comps)

    # 3.2 插入 joint 组件（先清洗同名组件）
    comps = clean_existing_joint_components(comps)
    joint_items = build_joint_components(
        new_id, init_joints, topic_controllers, moveit_controller
    )
    comps = insert_components_after_visibility(comps, joint_items)

    ent["Components"] = comps
    entities[target_key] = ent
    data["Entities"] = entities

    # 功能4：相机组件
    cameras = cfg.get("camera", {}) or {}
    if isinstance(cameras, dict) and entities:
        missing_camera_links = []
        for cam_key, cam_cfg in cameras.items():
            if not isinstance(cam_cfg, dict):
                continue
            cam_name = cam_cfg.get("CameraName", cam_key)
            camera_link = cam_cfg.get("CameraLink", cam_name)
            # 在 Entities 中查找 Name == camera_link 的实体
            found_key = None
            for ek, ent in entities.items():
                if isinstance(ent, dict) and ent.get("Name") == camera_link:
                    found_key = ek
                    break
            if found_key is None:
                missing_camera_links.append(f"{cam_key}: {camera_link}")
                continue

            ent = entities[found_key]
            comps = ent.get("Components", OrderedDict())
            if not isinstance(comps, OrderedDict):
                comps = OrderedDict(comps)

            # 避免重复：清除已存在的 ROS2CameraSensorEditorComponent
            comps = clean_existing_camera_component(comps)

            # 构造相机组件
            cam_item = build_camera_component(new_id, cam_name, cam_cfg)

            # 插入到 EditorVisibilityComponent 之后
            comps = insert_components_after_visibility(comps, [cam_item])

            # 覆写实体
            ent["Components"] = comps
            entities[found_key] = ent

        if missing_camera_links:
            raise ValueError(
                "CameraLink entities not found in prefab: "
                + ", ".join(missing_camera_links)
            )

        data["Entities"] = entities

    # 保存
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dump_ordered_json(data, out_path)
    return out_path


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="Path to input JSON")
    ap.add_argument("--yaml", required=True, help="Path to config YAML")
    ap.add_argument("--out", required=True, help="Path to output JSON")
    args = ap.parse_args()
    try:
        convert_prefab(args.json, args.yaml, args.out)
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
