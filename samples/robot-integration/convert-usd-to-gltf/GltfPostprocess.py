# -*- coding: utf-8 -*-

# ============================================================================
# glTF 后处理模块
#
# 功能：glTF 导出后的一系列修正：
#   - 缺失纹理补齐 (导出器无法拷贝的 .jpg/.png)
#   - 透明材质 alpha 修正 (UsdPreviewSurface opacity / OmniGlass cutout_opacity)
#   - 假透明材质改回不透明 (OmniPBR enable_opacity=True + opacity_constant=1.0)
#   - OmniGlass 玻璃颜色 gamma 压暗 (glass_color 是透射色)
#   - 纯色材质纹理化 (1x1 PNG)
#   - 双面化 mesh (单面渲染下背面剔除)
# ============================================================================

import os
import json
import shutil
import struct
import zlib
from array import array
from pxr import UsdShade, Sdf


# ---- 日志 (与主文件风格一致) ----


def log_info(message):
    print(f"[INFO] {message}")


def log_tag(tag, message):
    print(f"[{tag}] {message}")


# ============================================================================
# 节点 transform 异常数值重置
# ============================================================================

def reset_invalid_node_transforms(gltf_data):
    """
    重置 node 的异常 transform 数值:
      - 含 NaN/Inf 或超大值 (超出 float32 范围) 的 translation/scale/rotation:
        导出器会残留垃圾数值 (如 translation 1.39e39, scale 3.4e28), 导致 world
        matrix 溢出为 inf/NaN, 目标引擎的 prefab JSON 序列化失败且子 mesh Aabb
        失效 ("Mesh does not have a valid Aabb"), 需重置为默认值。
      - scale 全 0 的 node (源 USD 隐藏/未初始化残留): 3x3 矩阵行列式为 0,
        求逆失败 (Matrix3x4 GetInverseFull 警告), 同样导致 prefab JSON 失败。
      - 缺省 transform 的 node (无 matrix/translation/scale/rotation) 补默认
        translation [0,0,0]。
    返回修复的 node 数量。
    """
    fixed = 0
    for node in gltf_data.get("nodes", []):
        if not isinstance(node, dict):
            continue

        def _sanitize_list(key, default, limit):
            """将含 NaN/Inf 或超限数值的列表重置为默认值。返回是否修改。"""
            vals = node.get(key)
            if not vals:
                return False
            bad = any(not (v == v) or abs(v) > limit for v in vals)
            if bad:
                node[key] = list(default)
                return True
            return False

        if _sanitize_list("translation", [0.0, 0.0, 0.0], 1e30):
            fixed += 1
            log_tag("NODE TRANSFORM", f"{node.get('name','?')}: reset absurd translation")
        if _sanitize_list("scale", [1.0, 1.0, 1.0], 1e10):
            fixed += 1
            log_tag("NODE TRANSFORM", f"{node.get('name','?')}: reset absurd scale")
        if _sanitize_list("rotation", [0.0, 0.0, 0.0, 1.0], 1e10):
            fixed += 1
            log_tag("NODE TRANSFORM", f"{node.get('name','?')}: reset absurd rotation")

        if node.get("scale") == [0.0, 0.0, 0.0]:
            node["scale"] = [1.0, 1.0, 1.0]
            fixed += 1
            log_tag("NODE TRANSFORM", f"{node.get('name','?')}: reset zero scale")

        has_transform = ("matrix" in node or "translation" in node or
                         "scale" in node or "rotation" in node)
        if not has_transform:
            node["translation"] = [0.0, 0.0, 0.0]
            fixed += 1
    if fixed:
        log_tag("NODE TRANSFORM", f"fixed {fixed} node transforms")
    return fixed


# ============================================================================
# 缺失纹理补齐
# ============================================================================

def patch_missing_textures(gltf_data, output_tex_dir, usd_dir):
    """
    扫描 glTF 里引用但输出目录缺失的纹理文件, 在源目录递归查找并拷贝。
    返回补齐数量。
    """
    patched_count = 0
    for img in gltf_data.get("images", []):
        uri = img.get("uri", "")
        if not uri.startswith("./textures/"):
            continue
        tex_name = os.path.basename(uri)
        dest_tex = os.path.join(output_tex_dir, tex_name)
        if os.path.exists(dest_tex):
            continue
        # 在源目录递归查找同名文件
        found = False
        for root, dirs, files in os.walk(usd_dir):
            if tex_name in files:
                src_tex = os.path.join(root, tex_name)
                try:
                    shutil.copy2(src_tex, dest_tex)
                    patched_count += 1
                    log_tag("TEXTURE PATCH", f"{src_tex} -> {dest_tex}")
                    found = True
                except Exception as e:
                    log_tag("TEXTURE PATCH WARN", f"{src_tex}: {e}")
                break
        if not found:
            # 尝试同名 .png 替代 .jpg
            if tex_name.lower().endswith(".jpg"):
                png_name = os.path.splitext(tex_name)[0] + ".png"
                for root, dirs, files in os.walk(usd_dir):
                    if png_name in files:
                        src_tex = os.path.join(root, png_name)
                        try:
                            shutil.copy2(src_tex, dest_tex)
                            patched_count += 1
                            log_tag("TEXTURE PATCH", f"{src_tex} -> {dest_tex} (png->jpg)")
                            found = True
                        except Exception as e:
                            log_tag("TEXTURE PATCH WARN", f"{src_tex}: {e}")
                        break
                # 同时修正 glTF URI 为 .png
                if found:
                    img["uri"] = "./textures/" + png_name
        if not found:
            log_tag("TEXTURE PATCH", f"MISSING: {tex_name} not found anywhere under {usd_dir}")
    return patched_count


# ============================================================================
# 材质透明度扫描与修正
# ============================================================================

def scan_material_opacity(stage):
    """
    从源 stage 扫描材质透明度参数。
    返回:
      mat_name_opacity:     材质名 -> alpha (opacity / cutout_opacity)
      mat_name_omni:        OmniGlass 材质集合 (需移除 transmission)
      mat_name_glass_color: 显式设置 glass_color 的 OmniGlass 材质名 -> (r,g,b)
      mat_name_fake_opaque: 假透明材质集合 (enable_opacity=True + opacity_constant=1.0)
      mat_name_double_sided: 源 USD doubleSided=True 的 mesh 所用材质集合
    """
    mat_name_opacity = {}
    mat_name_omni = set()
    mat_name_glass_color = {}
    mat_name_fake_opaque = set()
    mat_name_double_sided = set()

    for mprim in stage.Traverse():
        if mprim.GetTypeName() != "Material":
            continue
        mname = mprim.GetName()
        for child in mprim.GetChildren():
            for attr in child.GetAttributes():
                if attr.GetName() == "inputs:opacity":
                    v = attr.Get()
                    if v is not None:
                        mat_name_opacity[mname] = float(v)
                if attr.GetName() == "inputs:cutout_opacity":
                    v = attr.Get()
                    if v is not None:
                        # OmniGlass: cutout_opacity 直接作为不透明系数(alpha)
                        mat_name_opacity[mname] = float(v)
                        mat_name_omni.add(mname)
                if attr.GetName() == "inputs:glass_color":
                    v = attr.Get()
                    if v is not None and v != (0.0, 0.0, 0.0):
                        # 显式设置的 OmniGlass 玻璃颜色(透射色)
                        mat_name_glass_color[mname] = (float(v[0]), float(v[1]), float(v[2]))
                if attr.GetName() == "inputs:enable_opacity":
                    v = attr.Get()
                    if v is not None and bool(v):
                        # enable_opacity=True: 再查同一 shader 的 opacity_constant
                        oc = child.GetAttribute("inputs:opacity_constant").Get()
                        if oc is not None and float(oc) >= 0.999:
                            # 完全不透明却被标记透明 -> 假透明
                            mat_name_fake_opaque.add(mname)

    # 收集 doubleSided=True 的 mesh 绑定的材质 (导出后需双面化, 否则背面剔除)
    for mprim in stage.Traverse():
        if mprim.GetTypeName() != "Mesh":
            continue
        ds = mprim.GetAttribute("doubleSided")
        if not ds or ds.Get() != True:
            continue
        path = mprim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        try:
            mb = UsdShade.MaterialBindingAPI(mprim)
            mat, _ = mb.ComputeBoundMaterial()
        except Exception:
            continue
        if mat:
            mat_name_double_sided.add(mat.GetPrim().GetName())

    return (mat_name_opacity, mat_name_omni, mat_name_glass_color,
            mat_name_fake_opaque, mat_name_double_sided)


def fix_transparent_materials(gltf_data, mat_info):
    """
    修正半透明/玻璃材质的 alpha:
      - 源完全不透明(opacity=1.0): 移除 BLEND, 保持不透明
      - 源透明(alpha<1): 设 alpha + BLEND + doubleSided + OmniGlass 移除 transmission
      - 假透明材质(enable_opacity=True + opacity_constant=1.0): 移除 BLEND 恢复不透明
    返回 (alpha_fixed, fake_opaque_fixed)。
    """
    (mat_name_opacity, mat_name_omni, mat_name_glass_color,
     mat_name_fake_opaque, mat_name_double_sided) = mat_info
    mats = gltf_data.get("materials", [])
    alpha_fixed = 0

    for m in mats:
        mname = m.get("name", "")
        if mname not in mat_name_opacity:
            continue
        alpha = mat_name_opacity[mname]
        pbr = m.setdefault("pbrMetallicRoughness", {})
        bcf = pbr.get("baseColorFactor")
        if bcf is None:
            bcf = [1.0, 1.0, 1.0, 1.0]
            pbr["baseColorFactor"] = bcf
        if len(bcf) == 4:
            if abs(bcf[3] - 1.0) < 0.001:
                if alpha >= 0.999:
                    # 源完全不透明(opacity=1.0): 不应标 BLEND, 保持不透明
                    bcf[3] = 1.0
                    if m.get("alphaMode") == "BLEND":
                        m.pop("alphaMode", None)
                        m.pop("doubleSided", None)
                    alpha_fixed += 1
                    log_tag("ALPHA FIX", f"{mname}: alpha=1.0 (opaque), removed BLEND")
                else:
                    bcf[3] = alpha
                    m["alphaMode"] = "BLEND"
                    # 透明材质强制双面渲染, 避免从背面看被剔除
                    m["doubleSided"] = True
                    # OmniGlass 材质: 移除 transmission, 否则玻璃完全透光导致颜色不可见
                    if mname in mat_name_omni:
                        ext = m.get("extensions", {})
                        if "KHR_materials_transmission" in ext:
                            ext["KHR_materials_transmission"]["transmissionFactor"] = 0.0
                    alpha_fixed += 1
                    log_tag("ALPHA FIX", f"{mname}: alpha -> {alpha}, doubleSided=True, omniGlass={mname in mat_name_omni}")

    # ---- 假透明修复: OmniPBR enable_opacity=True + opacity_constant=1.0 的材质,
    # 源意图完全不透明但导出器机械输出 BLEND, 导致透明渲染下物体消失(如白色地毯)。
    # 移除 alphaMode/doubleSided, 恢复不透明渲染。----
    fake_opaque_fixed = 0
    for m in mats:
        mname = m.get("name", "")
        if mname not in mat_name_fake_opaque:
            continue
        if m.get("alphaMode") != "BLEND":
            continue
        m.pop("alphaMode", None)
        m.pop("doubleSided", None)
        # baseColorFactor alpha 强制 1.0
        pbr = m.get("pbrMetallicRoughness")
        if pbr:
            bcf = pbr.get("baseColorFactor")
            if bcf is not None and len(bcf) == 4:
                bcf[3] = 1.0
        fake_opaque_fixed += 1
        log_tag("FAKE OPAQUE", f"{mname}: removed BLEND, restored opaque")

    return alpha_fixed, fake_opaque_fixed


def fix_glass_gamma(gltf_data, mat_name_glass_color):
    """
    OmniGlass 玻璃颜色映射: glass_color 是透射色, 直接当 baseColor 会偏浅(如花瓶米色),
    按 gamma 2.2 压暗近似物理渲染效果(透射吸收后视觉变深)。仅处理显式设置 glass_color 的
    OmniGlass 材质; 未设置的(钟玻璃/灯罩默认白)保持不变。数据驱动, 不依赖材质名。
    返回处理数。
    """
    fixed = 0
    mats = gltf_data.get("materials", [])
    for m in mats:
        mname = m.get("name", "")
        gc = mat_name_glass_color.get(mname)
        if gc is None:
            continue
        pbr = m.setdefault("pbrMetallicRoughness", {})
        bcf = pbr.get("baseColorFactor")
        if bcf is None:
            bcf = [1.0, 1.0, 1.0, 1.0]
            pbr["baseColorFactor"] = bcf
        alpha = bcf[3] if len(bcf) == 4 else 1.0
        new_rgb = tuple(max(0.0, min(1.0, v ** 2.2)) for v in gc)
        bcf[0], bcf[1], bcf[2] = new_rgb
        if len(bcf) == 3:
            bcf.append(alpha)
        fixed += 1
        log_tag("GLASS GAMMA", f"{mname}: glass_color {gc} -> baseColorFactor ({new_rgb[0]:.3f},{new_rgb[1]:.3f},{new_rgb[2]:.3f},{alpha})")
    return fixed


# ============================================================================
# 纯色透明材质纹理化
# ============================================================================

def make_pure_color_textures(gltf_data, output_path, transparent_mat_names):
    """
    为半透明纯色材质(无 baseColorTexture)生成 1x1 纯色 PNG 纹理并设置 baseColorTexture,
    规避目标引擎对纯色 baseColorFactor 读取失败导致的无色问题。
    返回处理的材质数。
    """
    output_tex_dir = os.path.join(output_path, "textures")
    os.makedirs(output_tex_dir, exist_ok=True)

    def write_png(rgb):
        sig = b"\x89PNG\r\n\x1a\n"
        def chunk(ctype, data):
            c = ctype + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        raw = b"\x00" + bytes(rgb)
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    mats = gltf_data.get("materials", [])
    images = gltf_data.setdefault("images", [])
    textures = gltf_data.setdefault("textures", [])
    processed = 0
    for i, m in enumerate(mats):
        mname = m.get("name", "")
        if mname not in transparent_mat_names:
            continue
        pbr = m.get("pbrMetallicRoughness", {})
        if pbr.get("baseColorTexture") is not None:
            continue
        bcf = pbr.get("baseColorFactor")
        if bcf is None or len(bcf) < 4:
            continue
        # 只处理"非白色 + alpha>0.3"的半透明纯色材质:
        # - 白色(如钟玻璃/灯罩/灯面板)不需要颜色, 跳过
        # - alpha<=0.3(如窗玻璃几乎全透明)跳过, 保持透明
        if bcf[0] >= 0.9 and bcf[1] >= 0.9 and bcf[2] >= 0.9:
            continue
        if bcf[3] <= 0.3:
            continue
        r = max(0, min(255, int(round(bcf[0] * 255))))
        g = max(0, min(255, int(round(bcf[1] * 255))))
        b = max(0, min(255, int(round(bcf[2] * 255))))
        tex_name = f"{mname}_purecolor.png"
        png_path = os.path.join(output_tex_dir, tex_name)
        with open(png_path, "wb") as f:
            f.write(write_png((r, g, b)))
        img_idx = len(images)
        images.append({"name": tex_name, "uri": f"./textures/{tex_name}"})
        sampler_idx = len(gltf_data.get("samplers", []))
        gltf_data.setdefault("samplers", []).append({})
        tex_idx = len(textures)
        textures.append({"source": img_idx, "sampler": sampler_idx})
        pbr["baseColorTexture"] = {"index": tex_idx}
        processed += 1
        log_tag("PURE COLOR TEX", f"{mname}: ({r},{g},{b}) -> {tex_name}")
    return processed


# ============================================================================
# 退化 mesh 清理
# ============================================================================

def remove_degenerate_meshes(gltf_data):
    """
    移除几何退化的 mesh primitive (POSITION accessor 任一轴 extent == 0),
    以及仅引用被移除 mesh 的 node。目标引擎的 Atom ModelLodAsset 要求
    mesh 的 Aabb 在三个轴上都有效 (Aabb::IsValid), 零厚度的线/面 mesh
    (如导出器产生的 post_* 垃圾数据) 会导致整个 glTF 处理失败。
    返回移除的 mesh 数量。
    """
    removed_meshes = set()
    for mesh_idx, mesh in enumerate(gltf_data.get("meshes", [])):
        keep_prims = []
        for prim in mesh.get("primitives", []):
            pos_idx = prim.get("attributes", {}).get("POSITION")
            if pos_idx is None:
                keep_prims.append(prim)
                continue
            acc = gltf_data["accessors"][pos_idx]
            mn = acc.get("min")
            mx = acc.get("max")
            if mn is None or mx is None or len(mn) != 3 or len(mx) != 3:
                # 无包围盒信息, 保守保留
                keep_prims.append(prim)
                continue
            degenerate = any(mx[c] - mn[c] == 0.0 for c in range(3))
            if degenerate:
                log_tag("DEGEN MESH", f"removing degenerate primitive: mesh {mesh_idx} '{mesh.get('name','?')}' min={mn} max={mx}")
            else:
                keep_prims.append(prim)
        if len(keep_prims) < len(mesh.get("primitives", [])):
            if keep_prims:
                mesh["primitives"] = keep_prims
            else:
                # 整个 mesh 只剩空壳, 从 meshes 中移除
                removed_meshes.add(mesh_idx)

    if not removed_meshes:
        return 0

    # 移除仅引用已删除 mesh 的 node (它们已无意义)
    nodes = gltf_data.get("nodes", [])
    nodes_to_remove = {ni for ni, node in enumerate(nodes) if node.get("mesh") in removed_meshes}

    if nodes_to_remove:
        # 从父 node 的 children 列表移除
        for node in nodes:
            if node.get("children"):
                node["children"] = [c for c in node["children"] if c not in nodes_to_remove]
        # 重建 nodes 列表并重映射索引
        remap = {}
        new_nodes = []
        for old_i, n in enumerate(nodes):
            if old_i not in nodes_to_remove:
                remap[old_i] = len(new_nodes)
                new_nodes.append(n)
        for n in new_nodes:
            if n.get("children"):
                n["children"] = [remap[c] for c in n["children"] if c in remap]
        gltf_data["nodes"] = new_nodes
        # 更新 scene.nodes
        for scene in gltf_data.get("scenes", []):
            if scene.get("nodes"):
                scene["nodes"] = [remap[c] for c in scene["nodes"] if c in remap]
        log_tag("DEGEN MESH", f"removed {len(nodes_to_remove)} nodes referencing removed meshes")

    # 从 meshes 列表移除, 重映射 node.mesh
    new_meshes = [m for i, m in enumerate(gltf_data.get("meshes", [])) if i not in removed_meshes]
    mesh_remap = {}
    new_index = 0
    for old_i in range(len(gltf_data.get("meshes", []))):
        if old_i not in removed_meshes:
            mesh_remap[old_i] = new_index
            new_index += 1
    for node in gltf_data.get("nodes", []):
        if node.get("mesh") is not None and node["mesh"] in mesh_remap:
            node["mesh"] = mesh_remap[node["mesh"]]
    gltf_data["meshes"] = new_meshes

    log_tag("DEGEN MESH", f"removed {len(removed_meshes)} degenerate meshes")
    return len(removed_meshes)


# ============================================================================
# 双面化 mesh
# ============================================================================

def make_transparent_meshes_double_sided(gltf_data, gltf_file_path, transparent_mat_names):
    """
    将使用透明材质的 mesh primitive 双面化: 复制三角形并反转法线,
    使单面渲染的引擎也能从两侧看到透明物体。
    返回处理的 primitive 数量。
    """
    mats = gltf_data.get("materials", [])
    transparent_mat_indices = set()
    for i, m in enumerate(mats):
        if m.get("name", "") in transparent_mat_names:
            transparent_mat_indices.add(i)
    if not transparent_mat_indices:
        return 0

    bin_path = os.path.splitext(gltf_file_path)[0] + ".bin"
    if not os.path.exists(bin_path):
        log_tag("DOUBLE SIDED", f"bin not found: {bin_path}")
        return 0

    with open(bin_path, "rb") as f:
        bin_data = bytearray(f.read())

    def read_float(acc_idx, count, ncomp):
        """读取 float 数组 (紧凑布局批量, 非紧凑逐元素)"""
        acc = gltf_data["accessors"][acc_idx]
        bv = gltf_data["bufferViews"][acc.get("bufferView")]
        off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or (ncomp * 4)
        if stride == ncomp * 4:
            return array('f', bin_data[off:off + count * ncomp * 4])
        out = array('f')
        for i in range(count):
            base = off + i * stride
            out.extend(struct.unpack_from('<%df' % ncomp, bin_data, base))
        return out

    def read_uint(acc_idx, count):
        acc = gltf_data["accessors"][acc_idx]
        bv = gltf_data["bufferViews"][acc.get("bufferView")]
        off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or 4
        if stride == 4:
            return array('I', bin_data[off:off + count * 4])
        out = array('I')
        for i in range(count):
            base = off + i * stride
            out.append(struct.unpack_from('<I', bin_data, base)[0])
        return out

    def read_uint16(acc_idx, count, ncomp):
        """读取 uint16 normalized 顶点属性 (如 COLOR_0, VEC4)"""
        acc = gltf_data["accessors"][acc_idx]
        bv = gltf_data["bufferViews"][acc.get("bufferView")]
        off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or (ncomp * 2)
        if stride == ncomp * 2:
            return array('H', bin_data[off:off + count * ncomp * 2])
        out = array('H')
        for i in range(count):
            base = off + i * stride
            out.extend(struct.unpack_from('<%dH' % ncomp, bin_data, base))
        return out

    processed = 0
    for mesh in gltf_data.get("meshes", []):
        for prim in mesh.get("primitives", []):
            mi = prim.get("material")
            if mi not in transparent_mat_indices:
                continue
            attrs = prim.get("attributes", {})
            pos_acc = attrs.get("POSITION")
            idx_acc = prim.get("indices")
            if pos_acc is None or idx_acc is None:
                continue
            pos = gltf_data["accessors"][pos_acc]
            idx = gltf_data["accessors"][idx_acc]
            pos_count = pos.get("count", 0)
            idx_count = idx.get("count", 0)
            if pos_count == 0 or idx_count == 0:
                continue

            orig_pos = read_float(pos_acc, pos_count, 3)
            orig_indices = read_uint(idx_acc, idx_count)
            norm_acc = attrs.get("NORMAL")
            orig_norm = read_float(norm_acc, pos_count, 3) if norm_acc is not None else None
            tex_acc = attrs.get("TEXCOORD_0")
            orig_tex = read_float(tex_acc, pos_count, 2) if tex_acc is not None else None
            color_acc = attrs.get("COLOR_0")
            orig_color = None
            orig_color_comp = 0
            if color_acc is not None:
                cacc = gltf_data["accessors"][color_acc]
                ccomp = {"VEC4": 4, "VEC3": 3, "VEC2": 2}.get(cacc.get("type"), 4)
                if cacc.get("componentType") == 5123:
                    orig_color = read_uint16(color_acc, pos_count, ccomp)
                    orig_color_comp = ccomp
                else:
                    orig_color = read_float(color_acc, pos_count, ccomp)
                    orig_color_comp = ccomp

            # 反向索引 (引用背面顶点 pos_count 起)
            new_vertex_base = pos_count
            n_tri = idx_count // 3
            new_indices = array('I')
            for t in range(n_tri):
                a = orig_indices[t*3]
                b = orig_indices[t*3+1]
                c = orig_indices[t*3+2]
                new_indices.append(new_vertex_base + a)
                new_indices.append(new_vertex_base + c)
                new_indices.append(new_vertex_base + b)

            # 批量拼接: 正面(原) + 背面(副本, 法线取反)
            appended = bytearray()
            appended += orig_pos.tobytes()
            appended += orig_pos.tobytes()
            if orig_norm is not None:
                appended += orig_norm.tobytes()
                neg_norm = array('f', (-x for x in orig_norm))
                appended += neg_norm.tobytes()
            if orig_tex is not None:
                appended += orig_tex.tobytes()
                appended += orig_tex.tobytes()
            if orig_color is not None:
                # COLOR_0 顶点色: 背面复制相同颜色
                appended += orig_color.tobytes()
                appended += orig_color.tobytes()
            appended += orig_indices.tobytes()
            appended += new_indices.tobytes()

            append_offset = len(bin_data)
            bin_data.extend(appended)
            buffer_byte_length = len(bin_data)

            def new_accessor(comp_count, count, comp_type_str, elem_size, min_vals=None, max_vals=None, normalized=False):
                nonlocal append_offset
                bv_idx = len(gltf_data["bufferViews"])
                gltf_data["bufferViews"].append({"buffer": 0, "byteOffset": append_offset, "byteLength": count * elem_size})
                comp_type = {"f": 5126, "I": 5125, "H": 5123}.get(comp_type_str, 5126)
                acc_idx = len(gltf_data["accessors"])
                acc_entry = {
                    "bufferView": bv_idx, "byteOffset": 0, "componentType": comp_type,
                    "count": count, "type": {3: "VEC3", 2: "VEC2", 1: "SCALAR", 4: "VEC4"}[comp_count],
                    "min": min_vals if min_vals is not None else [0.0]*comp_count,
                    "max": max_vals if max_vals is not None else [0.0]*comp_count,
                }
                if normalized:
                    acc_entry["normalized"] = True
                gltf_data["accessors"].append(acc_entry)
                append_offset += count * elem_size
                return acc_idx

            # 计算 POSITION 真实包围盒 (双面化副本位置相同, 直接用原顶点数据计算)
            # 用切片 min/max (C 实现, 避免 Python 逐元素循环)
            pos_min = [min(orig_pos[0::3]), min(orig_pos[1::3]), min(orig_pos[2::3])]
            pos_max = [max(orig_pos[0::3]), max(orig_pos[1::3]), max(orig_pos[2::3])]
            new_pos_acc = new_accessor(3, pos_count * 2, "f", 12, pos_min, pos_max)
            new_norm_acc = new_accessor(3, pos_count * 2, "f", 12) if orig_norm is not None else None
            new_tex_acc = new_accessor(2, pos_count * 2, "f", 8) if orig_tex is not None else None
            new_color_acc = None
            if orig_color is not None:
                if orig_color_comp == 4:
                    new_color_acc = new_accessor(4, pos_count * 2, "H", 8, normalized=True)
                else:
                    new_color_acc = new_accessor(orig_color_comp, pos_count * 2, "H", orig_color_comp * 2, normalized=True)
            new_idx_acc = new_accessor(1, idx_count * 2, "I", 4)

            prim["attributes"]["POSITION"] = new_pos_acc
            if new_norm_acc is not None:
                prim["attributes"]["NORMAL"] = new_norm_acc
            if new_tex_acc is not None:
                prim["attributes"]["TEXCOORD_0"] = new_tex_acc
            if new_color_acc is not None:
                prim["attributes"]["COLOR_0"] = new_color_acc
            prim["indices"] = new_idx_acc

            processed += 1
            log_tag("DOUBLE SIDED", f"mesh {mesh.get('name','?')} mat {mi}: pos {pos_count}->{pos_count*2}")

    if gltf_data.get("buffers"):
        gltf_data["buffers"][0]["byteLength"] = buffer_byte_length
    with open(bin_path, "wb") as f:
        f.write(bin_data)
    return processed


# ============================================================================
# 后处理总入口
# ============================================================================

def postprocess_gltf(state, stage, gltf_file_path, output_path, thin_sheet_mat_names):
    """
    glTF 导出后的统一后处理入口:
      1. 补齐缺失纹理
      2. 修正透明/假透明/玻璃颜色
      3. 纯色材质纹理化
      4. 双面化 mesh
      5. 节点 transform 异常数值重置 + 退化 mesh 清理
      6. 写回 glTF
    返回 True 表示有修改写回, False 表示无需写回。
    """
    output_tex_dir = os.path.join(output_path, "textures")
    os.makedirs(output_tex_dir, exist_ok=True)
    usd_dir = os.path.dirname(os.path.abspath(state.usd_file))

    try:
        with open(gltf_file_path, "r", encoding="utf-8") as gf:
            gltf_data = json.loads(gf.read())

        patched_count = patch_missing_textures(gltf_data, output_tex_dir, usd_dir)

        # 节点 transform 异常数值重置: 重置 NaN/Inf/超大值/全 0 的 transform
        # (源 USD 垃圾数据), 否则 world matrix 溢出为 inf/NaN 或求逆失败,
        # 目标引擎 prefab JSON 序列化失败。
        node_fixed = reset_invalid_node_transforms(gltf_data)

        # 材质透明度扫描
        mat_info = scan_material_opacity(stage)
        mat_name_opacity, mat_name_omni, mat_name_glass_color, mat_name_fake_opaque, mat_name_double_sided = mat_info

        # 透明/假透明 alpha 修正
        alpha_fixed, fake_opaque_fixed = fix_transparent_materials(gltf_data, mat_info)

        # OmniGlass 玻璃颜色 gamma 压暗
        glass_gamma_fixed = fix_glass_gamma(gltf_data, mat_name_glass_color)

        # 纯色透明材质纹理化 (仅真正半透明的材质, alpha<1.0)
        transparent_names = set(name for name, alpha in mat_name_opacity.items() if alpha < 0.999)
        if transparent_names:
            try:
                pure_count = make_pure_color_textures(gltf_data, output_path, transparent_names)
                log_info(f"[PURE COLOR TEX] generated {pure_count} pure color textures")
            except Exception as e:
                log_info(f"[PURE COLOR TEX] error: {e}")

        # 双面化 mesh: 透明材质 ∪ 源 doubleSided=True ∪ 薄片mesh
        double_sided_names = set(mat_name_opacity.keys()) | mat_name_double_sided | thin_sheet_mat_names
        if double_sided_names:
            try:
                doubled = make_transparent_meshes_double_sided(gltf_data, gltf_file_path, double_sided_names)
                log_info(f"[DOUBLE SIDED] doubled {doubled} meshes")
            except Exception as e:
                log_info(f"[DOUBLE SIDED] error: {e}")

        # 退化 mesh 清理: 零厚度 (任一轴 extent==0) 的 mesh 会导致目标引擎
        # ModelLodAsset 报 "Mesh does not have a valid Aabb", 整个 glTF 处理失败。
        degen_removed = remove_degenerate_meshes(gltf_data)

        # 写回 glTF
        if (patched_count > 0 or alpha_fixed > 0 or glass_gamma_fixed > 0 or fake_opaque_fixed > 0
                or node_fixed > 0 or degen_removed > 0):
            with open(gltf_file_path, "w", encoding="utf-8") as gf:
                gf.write(json.dumps(gltf_data, ensure_ascii=False, indent=1))
            log_info(f"[TEXTURE PATCH] patched {patched_count} textures, {alpha_fixed} alphas, "
                     f"glass_gamma={glass_gamma_fixed}, fake_opaque={fake_opaque_fixed}, nodes={node_fixed}, degen={degen_removed}, glTF updated")
            return True
    except Exception as e:
        log_info(f"[TEXTURE PATCH] error: {e}")
    return False
