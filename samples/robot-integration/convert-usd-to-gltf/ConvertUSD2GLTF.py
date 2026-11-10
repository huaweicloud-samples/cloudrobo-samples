# !/usr/bin/python3
# -*- coding: utf-8 -*-

# ============================================================================
# USD 转 glTF 转换器
#
# 功能：将 USD (.usd/.usda/.usdc/.usdz) 文件转换为 glTF 2.0 格式
# 环境：Isaac Sim (Windows 5.1.0)
#
# 使用方式：
#   cd /d D:\isaac\
#   python.bat ConvertUSD2GLTF.py -i "D:/input/model.usd" -o "D:/output/"
#
# ============================================================================

from isaacsim import SimulationApp
simulation_app = SimulationApp({
    "headless": True,
})

import os
import sys
import json
import argparse
import omni.kit.app
from pxr import Usd, UsdGeom, Sdf, Gf, UsdShade, Vt
from GltfPostprocess import postprocess_gltf


def parse_args():
    parser = argparse.ArgumentParser(description="Headless USD to glTF converter")
    parser.add_argument("--input", "-i", required=True, help="输入 USD 文件路径")
    parser.add_argument("--output", "-o", required=True, help="输出目录")
    return parser.parse_args()


def _enable_omni_extensions():
    """启用 USD 导出相关的 Omni 扩展（必须在导入导出器之前调用）。"""
    ext_manager = omni.kit.app.get_app().get_extension_manager()
    ext_manager.set_extension_enabled_immediate("omni.kit.tool.asset_exporter", True)
    ext_manager.set_extension_enabled_immediate("omni.kit.asset_converter", True)


_enable_omni_extensions()


import asyncio
import omni.usd
import omni.kit.tool.asset_exporter.exporter as exporter
import omni.kit.asset_converter as asset_converter
import omni.kit.commands
import omni.graph.core as og
import omni.timeline
import warnings
from collections import defaultdict
from dataclasses import dataclass
import re
import shutil
import time
from itertools import accumulate
import secrets
import string
import math
from typing import Dict

warnings.filterwarnings("default")
_export_stage_keepalive = None


# ============================================================================
# 常量定义
# ============================================================================

RANDOM_CHAR_SIZE = 4
USD_EXTENSIONS = ['.usd', '.usda', '.usdc', '.usdz']
COMMON_TEXTURE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.tga', '.tif', '.tiff', '.exr', '.bmp']

OPTIONAL_TEXTURE_KEYWORDS = (
    "normal", "roughness", "metallic", "occlusion",
    "ambient_occlusion", "specular", "geometrynormal",
)
REQUIRED_TEXTURE_KEYWORDS = (
    "basecolor", "base_color", "diffuse", "albedo", "opacity",
)


# ============================================================================
# 错误码定义
# ============================================================================

@dataclass(frozen=True)
class ErrorInfo:
    """错误信息定义"""
    code: str
    message: str
    hint: str


ERROR_CODES: Dict[str, ErrorInfo] = {
    "USD_FILE_NOT_FOUND": ErrorInfo(
        code="USD_FILE_NOT_FOUND",
        message="输入文件不存在",
        hint="请检查 --input 参数路径是否拼写正确，文件是否存在。",
    ),
    "USD_DIR_NOT_FOUND": ErrorInfo(
        code="USD_DIR_NOT_FOUND",
        message="输入目录不存在",
        hint="请检查 --input 参数路径是否拼写正确，目录是否存在。",
    ),
    "USD_EXT_INVALID": ErrorInfo(
        code="USD_EXT_INVALID",
        message="文件扩展名不支持",
        hint=f"仅支持 {' / '.join(USD_EXTENSIONS)} 四种格式。请使用支持的格式。",
    ),
    "USD_OPEN_FAILED": ErrorInfo(
        code="USD_OPEN_FAILED",
        message="USD 文件加载失败",
        hint="文件可能已损坏，或 USD 版本不兼容。请确认文件可正常在 Isaac Sim / OmniUSD 中打开。",
    ),
    "OUTPUT_DIR_INVALID": ErrorInfo(
        code="OUTPUT_DIR_INVALID",
        message="输出目录无效或无写入权限",
        hint="请检查 --output 参数路径是否有效，当前用户是否有写入权限。",
    ),
    "UDIM_MATERIAL_FAILED": ErrorInfo(
        code="UDIM_MATERIAL_FAILED",
        message="UDIM 关键贴图缺失，材质创建失败",
        hint="UDIM 拆分的 tile 缺少 BaseColor/Opacity 等关键贴图。请补全纹理后重新导出。",
    ),
    "EXPORT_FAILED": ErrorInfo(
        code="EXPORT_FAILED",
        message="glTF 导出失败",
        hint="导出器内部错误，请查看上方日志定位具体原因。可尝试减少场景规模或简化材质后重试。",
    ),
    "UNKNOWN_ERROR": ErrorInfo(
        code="UNKNOWN_ERROR",
        message="未知错误",
        hint="请查看上方日志定位具体原因。",
    ),
}


# ============================================================================
# 统一日志输出
# ============================================================================

def log_info(message):
    print(f"[INFO] {message}")


def log_warn(message):
    print(f"[WARN] {message}")


def log_error(message):
    print(f"[ERROR] {message}")


def log_tag(tag, message):
    """带自定义短标签的日志，用于保留原脚本中的 [graph] / [TEXTURE FIX] / [cleanup] 等前缀。"""
    print(f"[{tag}] {message}")


def error_exit(error_key, extra_detail=""):
    """
    统一错误输出并退出。

    注意：本函数只负责打印结构化错误信息并终止进程；
    资源清理（关闭 stage / simulation_app）仍由最外层 finally 处理，
    以保持与原脚本的清理顺序一致。
    """
    error = ERROR_CODES.get(error_key, ERROR_CODES["UNKNOWN_ERROR"])

    print("\n" + "=" * 80)
    print(f"[ERROR] 错误码: {error.code}")
    print(f"[ERROR] 错误描述: {error.message}")
    if extra_detail:
        print(f"[ERROR] 详细信息: {str(extra_detail)[:500]}")
    print(f"[HINT] 修复建议: {error.hint}")
    print("=" * 80 + "\n")

    sys.exit(1)


# ============================================================================
# 全局状态容器（替代原脚本中的全局变量）
# ============================================================================

class ConverterState:
    """
    转换过程中所有可变状态的容器。

    将原脚本中的模块级全局变量收拢到实例中，避免使用 global 关键字；
    同时保持字段名、数据结构、默认值与原脚本完全一致。
    """
    def __init__(self, usd_file, output_folder):
        self.usd_file = usd_file
        self.output_folder = output_folder

        # {key1:  {key2: value}}
        # key1:   USD中使用udim纹理的material prim
        # key2:   udim编号
        # value:  key1拆分后使用明确udim编号纹理的material prim
        self.matPrim_newMatPrim_map = defaultdict(lambda: defaultdict(Usd.Prim))

        # 当前打开的USD文件内的material, 不包含通过Reference引入的其它USD文件内的material
        self.non_instance_materials_set = set()

        # {material: scope}
        # material:   material prim路径
        # scope:      创建的scope的路径
        self.material_scope_map = defaultdict(str)

        # 使用udim纹理的material
        self.udim_material_prims = set()

        # 提示信息, 进度百分比
        self.progress_percentage = 0.0

# 薄片 mesh 所用材质集合 (cleanup 阶段检测, 双面化阶段处理)
thin_sheet_mat_names = set()


def disable_runtime_graphs_for_static_export(stage):
    """
    静态 glTF 导出前，停止 Timeline 并禁用所有 OmniGraph。
    尤其防止 Audio2Face 在 Mesh 被删除后继续访问旧路径。
    """
    timeline = omni.timeline.get_timeline_interface()

    try:
        timeline.stop()
        log_tag("graph", "timeline stopped")
    except Exception as error:
        log_tag("graph", f"timeline stop warning: {error}")

    graph_paths = []

    # 收集标准OmniGraph Prim
    for prim in stage.Traverse():
        if prim.GetTypeName() == "OmniGraph":
            graph_paths.append(prim.GetPath().pathString)

    audio2face_path = "/World/audio2face"
    audio2face_prim = stage.GetPrimAtPath(audio2face_path)

    if (audio2face_prim and audio2face_prim.IsValid()):
        graph_paths.append(audio2face_path)

    for graph_path in sorted(set(graph_paths)):
        log_tag("graph", f"disabling: {graph_path}")

        try:
            graph = og.get_graph_by_path(graph_path)
            if graph is not None:
                if hasattr(graph, "set_usd_notice_handling_enabled"):
                    graph.set_usd_notice_handling_enabled(False)
                graph.set_disabled(True)
                log_tag("graph", f"runtime disabled: {graph_path}")
        except Exception as error:
            log_tag("graph", f"runtime disable warning: {graph_path}: {error}")

        # 从USD Stage上停用Graph Prim。
        try:
            graph_prim = stage.GetPrimAtPath(graph_path)
            if (graph_prim and graph_prim.IsValid()):
                graph_prim.SetActive(False)
                log_tag("graph", f"USD prim inactive: {graph_path}")
        except Exception as error:
            log_tag("graph", f"prim deactivate warning: {graph_path}: {error}")


# 显示进度条
def show_progress(state, iteration, total, prefix='Progress...', suffix='Complete', length=100):
    percent = 100.0 * iteration / total
    if iteration == total:
        state.progress_percentage = 0.0
    elif percent - state.progress_percentage < 1.0:
        return
    else:
        state.progress_percentage = percent
    filled_length = int(length * iteration // total)
    bar = '█' * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent:.1f}% ({iteration}/{total}) {suffix}')
    sys.stdout.flush()


# 加载USD文件
# [file_path]:    文件路径
# [ret]:          文件加载状态
def import_usd(file_path):
    # 文件不存在
    if not os.path.exists(file_path):
        log_error(f"File {file_path} not exists.")
        return False
    name, ext = os.path.splitext(os.path.basename(file_path))

    # 文件扩展名不符合
    if ext.lower() not in USD_EXTENSIONS:
        log_error(f"File {file_path} is not a USD type file.")
        return False

    import_status = omni.usd.get_context().open_stage(file_path)
    # 加载错误, isaac-sim或usd文件错误
    if not import_status:
        log_error(f"Failed to Load {file_path}. Open stage error.")
        return False
    return True


# 复制一份新的source_prim
def copy_prim(stage, source_prim, udimIndex, scope_path=None):
    source_path = source_prim.GetPath().pathString
    source_name = source_prim.GetName()
    dest_path = ""

    # 在scope目录下创建
    if scope_path:
        dest_path = scope_path + '/' + source_name + '_' + str(udimIndex)
    else:
        dest_path = source_path + '_' + str(udimIndex)

    log_info(f"Creating new prim, source: {source_path}, dest: {dest_path}")
    omni.kit.commands.execute('CopyPrim',
        path_from=source_path,
        path_to=dest_path,
        copy_to_introducing_layer=False)

    destPrim = stage.GetPrimAtPath(dest_path)
    if not destPrim:
        log_warn(f"Unable to create a new prim, source: {source_path}, dest: {dest_path}")
    return destPrim


# 删除给定的prim
def delete_prims(prim_path):
    omni.kit.commands.execute('DeletePrims',
        paths=[prim_path],
        destructive=False)


# 创建scope, 用于挂载其它节点
def create_scope(stage, path):
    scope_path = path + "_UDIM"
    while stage.GetPrimAtPath(scope_path):
        chars = string.ascii_letters + string.digits
        random_str = ''.join(secrets.choice(chars) for _ in range(RANDOM_CHAR_SIZE))
        scope_path = path + "_UDIM_" + random_str
    stage.DefinePrim(scope_path, 'Scope')
    return scope_path


def is_ref_or_instance(prim):
    return (
        prim.HasAuthoredReferences()
        or prim.IsInstance()
        or prim.IsInstanceProxy()
        or prim.IsInPrototype()
    )


# 计算udim index, st: uv坐标
def get_udimIndex(st):
    # uv坐标小于0为非法数据
    if st[0] < 0 or st[1] < 0:
        log_warn(f"The UV value {st} is negative.")
        return -1

    u_tile = math.floor(st[0])
    v_tile = math.floor(st[1])
    return 1001 + int(u_tile) + 10 * int(v_tile)


# 验证udim index是否合法: 满足>=1001
def verify_udimIndex(udimIndex):
    if udimIndex == -1:
        return False
    elif udimIndex < 1001:
        log_warn(f"The udim value {udimIndex} is invalid.")
        return False
    return True


def _safe_name(s):
    return "".join(c if c.isalnum() or c == "_" else "_" for c in s)


def _is_finite_uv(uv):
    return math.isfinite(uv[0]) and math.isfinite(uv[1])


# 获取mesh面片的udim index
def _face_udim_index(fv_start, fv_count, st_indices, st_values):
    # 若一个面跨多个tile，返回None（建议先过滤）
    tiles = set()
    for k in range(fv_start, fv_start + fv_count):
        si = st_indices[k]
        uv = st_values[si]
        if not _is_finite_uv(uv):
            return None
        tiles.add(get_udimIndex(uv))
        if len(tiles) > 1:
            return None
    return next(iter(tiles)) if tiles else None


# 检测prim是否使用了UDIM纹理
def detect_udim_texture(stage, prim):
    for child_prim in prim.GetAllChildren():
        shader = UsdShade.Shader(child_prim)
        if not shader:
            continue
        for input in shader.GetInputs():
            for attr in input.GetValueProducingAttributes():
                assetPath = attr.Get()
                if isinstance(assetPath, Sdf.AssetPath) \
                    and "<UDIM>" in assetPath.path \
                    and not os.path.exists(assetPath.resolvedPath):
                    return True
    return False


def replace_udim_assets_safely(new_material_prim, udim_index):
    """
    将 <UDIM> 替换为具体 tile。

    可选贴图缺失：
        清空路径，允许使用材质默认值。

    BaseColor / Opacity 等关键贴图缺失：
        返回 False，不创建这份材质。
    """
    success = True

    for shader_prim in Usd.PrimRange(new_material_prim):
        shader = UsdShade.Shader(shader_prim)

        if not shader:
            continue

        for shader_input in shader.GetInputs():
            for attr in shader_input.GetValueProducingAttributes():
                asset_path = attr.Get()

                if not isinstance(asset_path, Sdf.AssetPath):
                    continue

                if "<UDIM>" not in asset_path.path:
                    continue

                authored_path = asset_path.path.replace("<UDIM>", str(udim_index))

                resolved_path = (asset_path.resolvedPath or asset_path.path).replace("<UDIM>", str(udim_index))

                if os.path.exists(resolved_path):
                    attr.Set(Sdf.AssetPath(authored_path))
                    log_tag("UDIM TEXTURE OK", f"{attr.GetPath()} -> {authored_path}")
                    continue

                attr_description = (attr.GetPath().pathString.lower() + " " + authored_path.lower())

                is_optional = any(word in attr_description for word in OPTIONAL_TEXTURE_KEYWORDS)
                is_required = any(word in attr_description for word in REQUIRED_TEXTURE_KEYWORDS)

                if is_optional and not is_required:
                    # 不要留下不存在的路径。
                    # 使用空 AssetPath，让材质回退到默认值。
                    attr.Set(Sdf.AssetPath(""))
                    log_tag("UDIM OPTIONAL MISSING", f"{attr.GetPath()}: {resolved_path}; disabled")
                else:
                    log_tag("UDIM REQUIRED MISSING", f"{attr.GetPath()}: {resolved_path}")
                    success = False

    return success


# 处理meterial prim: []
def process_material(state, stage, udimIndex, material_prim):
    material_prim_path = material_prim.GetPath().pathString
    scope_path = state.material_scope_map[material_prim_path]
    failed_udim_materials = set()
    # udim material放置在名为[原材质名+'_UDIM'(+'_'+随机数)]Scope目录下
    # 是否存在scope, 不存在则创建
    if not scope_path:
        scope_path = create_scope(stage, material_prim_path)
        state.material_scope_map[material_prim_path] = scope_path
    # 复制material prim
    new_material_prim = copy_prim(stage, material_prim, udimIndex, scope_path)
    if not new_material_prim:
        return None

    # 若material prim属于通过Reference引用的USD文件, material复制后, 节点路径错误, 需要修正
    source_name = material_prim.GetName()
    for attr in new_material_prim.GetAttributes():
        conns = []
        for conn in attr.GetConnections():
            error_path = conn.pathString
            if error_path.find("UDIM") != -1:
                break
            idx = error_path.find(source_name)
            if idx == -1:
                log_warn(f"Cann't find {source_name} in {error_path}")
                continue
            right_path = new_material_prim.GetPath().pathString + error_path[idx + len(source_name):]
            conns.append(right_path)
        if conns:
            attr.SetConnections(conns)

    # input信息无法复制, 需要手动创建
    material = UsdShade.Material(material_prim)
    new_material = UsdShade.Material(new_material_prim)
    for input_slot in material.GetInputs():
        new_input_slot = new_material.CreateInput(input_slot.GetBaseName(), input_slot.GetTypeName())
        new_input_slot.Set(input_slot.Get())

    # 将UDIM路径设置为实际使用的纹理路径
    material_ok = replace_udim_assets_safely(new_material_prim, udimIndex)
    if not material_ok:
        log_error(f"UDIM MATERIAL FAILED: {material_prim_path}, tile={udimIndex}")
        failed_udim_materials.add(material_prim)
        delete_prims(new_material_prim.GetPath().pathString)
        return None
    return new_material_prim


# 创建并且绑定material到mesh/subset
def create_bind_material(state, stage, udimIndex_prim_map, material_prim):
    for udimIndex, prim in udimIndex_prim_map.items():
        material = None
        if state.matPrim_newMatPrim_map[material_prim]:
            material = state.matPrim_newMatPrim_map[material_prim][udimIndex]
            if material:
                log_info(f"The target material: {material.GetPath()} already exists. It will be used directly.")
        if not material:
            log_info(f"The target material does not exist. Proceed to create...")
            material = process_material(state, stage, udimIndex, material_prim)
        if not material:
            continue

        state.matPrim_newMatPrim_map[material_prim][udimIndex] = material
        log_info(f"Remove the original material: {prim.GetPath()}; Bind the new material: {material.GetPath()}, udimIndex: {udimIndex}")
        rel = prim.GetRelationship("material:binding")
        # rel.ClearTargets(True)
        rel.SetTargets([material.GetPath()])


# 法线按 USD interpolation 展开到 faceVarying，保留原始法线而不是重新计算。
def _get_facevarying_source_normals(mesh, face_counts, face_indices, points):
    corner_count = sum(int(c) for c in face_counts)
    primvar = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("normals")
    sources = []
    if primvar and primvar.HasValue():
        sources.append(("primvars:normals", primvar.Get(), primvar.GetInterpolation(), primvar.GetIndices()))
    values = mesh.GetNormalsAttr().Get()
    if values is not None:
        sources.append(("normals", values, mesh.GetNormalsInterpolation(), None))

    if not sources:
        return None, "none", "missing"

    for source, values, interp, indices in sources:
        try:
            if interp == UsdGeom.Tokens.faceVarying:
                slots = range(corner_count)
                expected = corner_count
            elif interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
                slots = [int(i) for i in face_indices]
                expected = len(points)
            elif interp == UsdGeom.Tokens.uniform:
                slots = [face for face, count in enumerate(face_counts) for _ in range(int(count))]
                expected = len(face_counts)
            elif interp == UsdGeom.Tokens.constant:
                slots = [0] * corner_count
                expected = 1
            else:
                continue

            if values is None or len(slots) != corner_count:
                continue
            if indices is not None and len(indices) > 0:
                if len(indices) != expected:
                    continue
                result = [values[int(indices[i])] for i in slots]
            else:
                if len(values) != expected:
                    continue
                result = [values[i] for i in slots]

            out = []
            for n in result:
                x, y, z = float(n[0]), float(n[1]), float(n[2])
                mag2 = x * x + y * y + z * z
                if not math.isfinite(mag2) or mag2 < 1e-20:
                    raise ValueError("invalid normal")
                mag = math.sqrt(mag2)
                out.append(Gf.Vec3f(x / mag, y / mag, z / mag))
            return Vt.Vec3fArray(out), source, "ok"
        except (IndexError, TypeError, ValueError, OverflowError):
            continue
    return None, "none", "invalid"


def _write_facevarying_normals(mesh, normals):
    mesh.GetNormalsAttr().Set(Vt.Vec3fArray(normals))
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    pv = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("normals")
    if pv and pv.HasValue():
        pv.Set(Vt.Vec3fArray(normals))
        pv.SetInterpolation(UsdGeom.Tokens.faceVarying)
        pv.BlockIndices()


def _slice_facevarying_normals(face_counts, normals, face_ids):
    offsets = []
    offset = 0
    for count in face_counts:
        offsets.append(offset)
        offset += int(count)
    return Vt.Vec3fArray([
        normals[i] for fid in face_ids
        for i in range(offsets[fid], offsets[fid] + int(face_counts[fid]))
    ])


def process_geosubset(state, stage, mesh_prim):
    # mesh 无任何子节点
    if len(mesh_prim.GetAllChildren()) == 0:
        return False

    mesh = UsdGeom.Mesh(mesh_prim)
    faceVertexCounts = mesh.GetFaceVertexCountsAttr().Get()
    faceVertexIndices = mesh.GetFaceVertexIndicesAttr().Get()
    sts = mesh_prim.GetAttribute("primvars:st").Get()
    stIndices = mesh_prim.GetAttribute("primvars:st:indices").Get()
    pv = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st")
    interp = pv.GetInterpolation()

    if not stIndices and interp == UsdGeom.Tokens.faceVarying:
        expected = sum(faceVertexCounts)
        stIndices = Vt.IntArray(expected)
        for i in range(expected):
            stIndices[i] = i
    # 不包含face or uv数据, 直接跳过
    if not faceVertexCounts or not faceVertexIndices or not sts or not stIndices:
        log_info(f"{mesh.GetPath().pathString} does not have mesh face or UV data, skipping...")
        return True

    # face count前缀和
    prefix_sums = None
    processed = False
    # 遍历mesh prim所有子节点, 处理Geom subset
    for child in mesh_prim.GetAllChildren():
        if child.GetTypeName() != 'GeomSubset':
            continue
        subset_prim = child
        processed = True
        subset_prim_path = subset_prim.GetPath().pathString
        material_bindingAPI = UsdShade.MaterialBindingAPI(subset_prim)
        material, _ = material_bindingAPI.ComputeBoundMaterial()
        # 未绑定材质, 跳过
        if not material:
            log_info("Not a material prim, skipping...")
            continue
        # 材质未使用UDIM纹理
        material_prim = material.GetPrim()
        if material_prim not in state.udim_material_prims:
            log_info(f"Geom subset: {subset_prim_path} does not use the UDIM texture, skipping...")
            continue
        log_info(f"Geom subset: {subset_prim_path} uses UDIM textures, perform the split...")

        # 计算face count前缀和
        if not prefix_sums:
            prefix_sums = list(accumulate(faceVertexCounts))

        subset = UsdGeom.Subset(subset_prim)
        indices = subset.GetIndicesAttr().Get()

        # 拆分indeces, 存放在udimIndex_indeces_map
        udimIndex_indeces_map = defaultdict(list)
        for indice in indices:
            startIndex = prefix_sums[indice - 1] if indice > 0 else 0
            end_Index = prefix_sums[indice]
            count = end_Index - startIndex
            udimIndex = _face_udim_index(startIndex, count, stIndices, sts)
            if not verify_udimIndex(udimIndex):
                continue
            udimIndex_indeces_map[udimIndex].append(indice)

        # {udimIndex: prim}, prim: 拆分的prim
        udimIndex_subsetPrim_map = defaultdict(Usd.Prim)

        # 根据拆分的indice, 创建Geom subset
        if len(udimIndex_indeces_map) > 1:
            for udimIndex, indices in udimIndex_indeces_map.items():
                dest_prim = copy_prim(stage, subset_prim, udimIndex)
                dest_subset = UsdGeom.Subset(dest_prim)
                dest_subset.GetIndicesAttr().Set(indices)
                udimIndex_subsetPrim_map[udimIndex] = dest_prim
            # 移除旧Geom subset
            delete_prims(subset_prim_path)
        else:
            udimIndex_subsetPrim_map[udimIndex] = subset_prim
        # 创建和绑定材质
        create_bind_material(state, stage, udimIndex_subsetPrim_map, material_prim)
    return processed


def _create_child_mesh_path(stage, parent_path, base_name):
    parent = Sdf.Path(parent_path).GetParentPath()
    scope_path = parent.AppendChild(_safe_name(base_name + "_udim_split"))
    if not stage.GetPrimAtPath(scope_path):
        UsdGeom.Scope.Define(stage, scope_path)
    return scope_path


def _build_submesh_data(face_counts, face_indices, st_indices, st_values, points, picked_face_ids):
    """
    返回：
      new_counts, new_fv_indices(local), new_points, new_st_flat(faceVarying), valid
    """
    # 每个面的起始偏移
    offsets = []
    cur = 0
    for c in face_counts:
        offsets.append(cur)
        cur += c

    # 收集子mesh数据（先用 Python list）
    sub_counts_py = []
    sub_fv_global_py = []
    sub_st_flat_py = []

    for fid in picked_face_ids:
        c = int(face_counts[fid])
        start = offsets[fid]
        sub_counts_py.append(c)

        for i in range(start, start + c):
            gvi = int(face_indices[i])
            sub_fv_global_py.append(gvi)

            si = int(st_indices[i])
            uv = st_values[si]
            sub_st_flat_py.append(Gf.Vec2f(float(uv[0]), float(uv[1])))

    # global point index -> local point index
    g2l = {}
    unique_global_py = []
    new_fv_local_py = []

    for gvi in sub_fv_global_py:
        if gvi not in g2l:
            g2l[gvi] = len(unique_global_py)
            unique_global_py.append(gvi)
        new_fv_local_py.append(g2l[gvi])

    # 构建 points
    new_points_py = [points[gvi] for gvi in unique_global_py]

    # 转成 Vt 数组
    new_counts = Vt.IntArray(sub_counts_py)
    new_fv_local = Vt.IntArray(new_fv_local_py)
    new_points = Vt.Vec3fArray(new_points_py)
    new_st_flat = Vt.Vec2fArray(sub_st_flat_py)

    # 校验
    nv = sum(sub_counts_py)
    valid = True
    if nv != len(new_fv_local): valid = False
    if nv != len(new_st_flat): valid = False
    if len(new_points) == 0: valid = False
    if any(c < 3 for c in sub_counts_py): valid = False
    if len(new_fv_local_py) > 0:
        if min(new_fv_local_py) < 0 or max(new_fv_local_py) >= len(new_points):
            valid = False

    return new_counts, new_fv_local, new_points, new_st_flat, valid


def _copy_xform_as_matrix(src_prim, dst_prim):
    src_xf = UsdGeom.Xformable(src_prim)
    dst_xf = UsdGeom.Xformable(dst_prim)
    if not src_xf or not dst_xf:
        return
    try:
        m = src_xf.GetLocalTransformation()  # Gf.Matrix4d
        op = dst_xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble)
        op.Set(m)
    except Exception as e:
        log_warn(f"copy xform as matrix failed: {e}")


def process_refmesh(state, stage, mesh_prim):
    mesh = UsdGeom.Mesh(mesh_prim)
    mesh_path = mesh_prim.GetPath().pathString
    mesh_prim_path = mesh_prim.GetPath().pathString

    # 材质检查
    mb = UsdShade.MaterialBindingAPI(mesh_prim)
    material, _ = mb.ComputeBoundMaterial()
    if not material:
        log_tag("skip", f"{mesh_path} no material")
        return
    material_prim = material.GetPrim()
    if material_prim not in state.udim_material_prims:
        log_tag("skip", f"{mesh_path} material not UDIM")
        return

    # 基础数据
    face_counts = mesh.GetFaceVertexCountsAttr().Get() or []
    face_indices = mesh.GetFaceVertexIndicesAttr().Get() or []
    points = mesh.GetPointsAttr().Get() or []
    original_normals, _, _ = _get_facevarying_source_normals(mesh, face_counts, face_indices, points)

    pv_st = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st")
    if not pv_st or not pv_st.HasValue():
        log_tag("skip", f"{mesh_path} no primvars:st")
        return
    st_values = pv_st.Get()
    interp = pv_st.GetInterpolation()

    if interp != UsdGeom.Tokens.faceVarying:
        log_tag("skip", f"{mesh_path} st interp={interp}, only faceVarying handled")
        return

    st_indices = mesh_prim.GetAttribute("primvars:st:indices").Get()
    if st_indices is None:
        # 非索引 faceVarying，补齐 identity 索引
        nv = sum(face_counts)
        st_indices = Vt.IntArray(nv)
        for i in range(nv):
            st_indices[i] = i

    # 基础一致性检查
    nv = sum(face_counts)
    if not face_counts or not face_indices or not points or st_values is None:
        log_tag("skip", f"{mesh_path} missing topology/points/st")
        return
    if len(face_indices) != nv:
        log_tag("skip", f"{mesh_path} invalid fv: len(face_indices)={len(face_indices)} nv={nv}")
        return
    if len(st_indices) != nv:
        log_tag("skip", f"{mesh_path} invalid st_indices: {len(st_indices)} != {nv}")
        return
    if min(face_indices) < 0 or max(face_indices) >= len(points):
        log_tag("skip", f"{mesh_path} face vertex index out of points range")
        return
    if min(st_indices) < 0 or max(st_indices) >= len(st_values):
        log_tag("skip", f"{mesh_path} st index out of st range")
        return

    # 按 face 分组到 UDIM
    udim_to_face_ids = defaultdict(list)
    dropped = 0

    fstart = 0
    face_count = len(face_counts)
    for fid, c in enumerate(face_counts):
        show_progress(state, fid, face_count, prefix="Processing mesh face...")
        udim = _face_udim_index(fstart, c, st_indices, st_values)
        fstart += c
        if udim is None:
            dropped += 1
            continue
        # 可按需加 verify_udimIndex(udim)
        udim_to_face_ids[udim].append(fid)

    if not udim_to_face_ids:
        log_tag("skip", f"{mesh_path} no valid faces after UDIM grouping")
        return

    # 创建容器Scope
    base_name = mesh_prim.GetName()
    split_scope = _create_child_mesh_path(stage, mesh_path, base_name)

    created = {}
    for udim, face_ids in udim_to_face_ids.items():
        sub_counts, sub_fv_local, sub_points, sub_st_flat, valid = _build_submesh_data(
            face_counts, face_indices, st_indices, st_values, points, face_ids
        )
        if not valid:
            log_tag("skip", f"{mesh_path} udim {udim} invalid submesh")
            continue

        child_name = _safe_name(f"{base_name}_{udim}")
        child_path = split_scope.AppendChild(child_name)
        submesh = UsdGeom.Mesh.Define(stage, child_path)
        prim = submesh.GetPrim()

        # 白名单写入
        submesh.GetPointsAttr().Set(sub_points)
        submesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(sub_counts))
        submesh.GetFaceVertexIndicesAttr().Set(sub_fv_local)
        orientation = mesh.GetOrientationAttr().Get()
        if orientation is not None:
            submesh.GetOrientationAttr().Set(orientation)
        if original_normals is not None:
            _write_facevarying_normals(submesh, _slice_facevarying_normals(face_counts, original_normals, face_ids))

        # st 扁平化（无 indices）
        sub_pv_st = UsdGeom.PrimvarsAPI(submesh).CreatePrimvar(
            "st",
            Sdf.ValueTypeNames.TexCoord2fArray,
            UsdGeom.Tokens.faceVarying
        )
        sub_pv_st.Set(sub_st_flat)
        if prim.HasProperty("primvars:st:indices"):
            prim.RemoveProperty("primvars:st:indices")

        # 可选：继承/复制 transform（如果原mesh是Xformable）
        _copy_xform_as_matrix(mesh_prim, prim)

        # 绑定材质（先绑定同一个，后续你可按udim切具体材质）
        # UsdShade.MaterialBindingAPI(prim).Bind(material)

        created[udim] = prim
        log_tag("ok", f"create {child_path} faces={len(face_ids)} nv={sum(sub_counts)}")

    if not created:
        log_tag("skip", f"{mesh_path} no submesh created")
        return

    # 删除旧mesh
    delete_prims(mesh_prim_path)
    create_bind_material(state, stage, created, material_prim)
    log_tag("done", f"split {mesh_path}, dropped_cross_tile_faces={dropped}, created={len(created)}")


def process_mesh(state, stage, mesh_prim):
    mesh_prim_path = mesh_prim.GetPath().pathString
    material_bindingAPI = UsdShade.MaterialBindingAPI(mesh_prim)
    material, _ = material_bindingAPI.ComputeBoundMaterial()
    # 未绑定材质, 跳过
    if not material:
        log_info("Not a material prim, skipping...")
        return
    # 材质未使用UDIM纹理
    material_prim = material.GetPrim()
    if material_prim not in state.udim_material_prims:
        log_info(f"Mesh: {mesh_prim_path} does not use the UDIM texture, skipping...")
        return

    log_info(f"Mesh: {mesh_prim_path} uses UDIM textures; perform the split...")

    mesh = UsdGeom.Mesh(mesh_prim)
    points = mesh.GetPointsAttr().Get()
    faceVertexCounts = mesh.GetFaceVertexCountsAttr().Get()
    faceVertexIndices = mesh.GetFaceVertexIndicesAttr().Get()
    sts = mesh_prim.GetAttribute("primvars:st").Get()
    pv = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st")
    interp = pv.GetInterpolation()
    stIndices = mesh_prim.GetAttribute("primvars:st:indices").Get()
    original_normals, _, _ = _get_facevarying_source_normals(mesh, faceVertexCounts, faceVertexIndices, points)

    # sts为非索引，插值为faceVarying时，自行填充st indices
    if not stIndices and interp == UsdGeom.Tokens.faceVarying:
        expected = sum(faceVertexCounts)
        stIndices = Vt.IntArray(expected)
        for i in range(expected):
            stIndices[i] = i

    # {udimIndex: faceVertexCounts}, faceVertexCounts: 面片顶点数量
    udimIndex_faceVertexCounts_map = defaultdict(list)
    # {udimIndex: faceVertexIndices}, faceVertexIndices: 面片索引
    udimIndex_faceVertexIndices_map = defaultdict(list)
    # {udimIndex: stIndices}, stIndices: uv索引
    udimIndex_stIndices_map = defaultdict(list)
    # {udimIndex: normals}, normals: faceVarying 法线
    udimIndex_normals_map = defaultdict(list)
    # {udimIndex: prim}, prim: 拆分的prim
    udimIndex_meshPrim_map = defaultdict(Usd.Prim)

    # 不包含mesh face or uv数据, 直接跳过
    if not faceVertexCounts or not faceVertexIndices or not sts or not stIndices:
        log_info(f"{mesh.GetPath().pathString} does not have mesh face or UV data, skipping...")
        return

    # 根据面片uv所属UDIM纹理, 拆分
    index = 0
    face_count = len(faceVertexCounts)
    for i, count in enumerate(faceVertexCounts, start=1):
        show_progress(state, i, face_count, prefix="Processing mesh face...")
        udimIndex = _face_udim_index(index, count, stIndices, sts)
        index += count
        if not verify_udimIndex(udimIndex):
            continue
        udimIndex_faceVertexCounts_map[udimIndex].append(count)
        udimIndex_faceVertexIndices_map[udimIndex].extend(faceVertexIndices[index - count:index])
        udimIndex_stIndices_map[udimIndex].extend(stIndices[index - count:index])
        if original_normals is not None:
            udimIndex_normals_map[udimIndex].extend(original_normals[index - count:index])

    # 根据拆分结果, 创建mesh prim
    if len(udimIndex_faceVertexCounts_map) > 0:
        scope_path = create_scope(stage, mesh_prim_path)
        for udimIndex in udimIndex_faceVertexCounts_map:
            udimPrim = copy_prim(stage, mesh_prim, udimIndex, scope_path)
            if not udimPrim:
                continue

            udimIndex_meshPrim_map[udimIndex] = udimPrim
            udimMesh = UsdGeom.Mesh(udimPrim)

            faceVertexCountsAttr = udimMesh.GetFaceVertexCountsAttr()
            faceVertexCountsAttr.Set(udimIndex_faceVertexCounts_map[udimIndex])

            faceVertexIndices = udimMesh.GetFaceVertexIndicesAttr()
            faceVertexIndices.Set(udimIndex_faceVertexIndices_map[udimIndex])

            stIndices = udimPrim.GetAttribute("primvars:st:indices")

            pv = UsdGeom.PrimvarsAPI(udimMesh).GetPrimvar("st")
            pv.SetIndices(udimIndex_stIndices_map[udimIndex])
            if original_normals is not None:
                _write_facevarying_normals(udimMesh, udimIndex_normals_map[udimIndex])

        # Mesh拆分完成, 移除旧Mesh
        delete_prims(mesh_prim_path)
    else:
        log_info("UdimIndex faceVertexCounts map is empty.")
    log_info("The Mesh has been successfully split.")

    create_bind_material(state, stage, udimIndex_meshPrim_map, material_prim)


# 修复悬空的材质绑定: mesh整体material:binding指向不存在的材质时, 移除该绑定,
# 让导出器回退到GeomSubset级别的正确绑定
def fix_dangling_material_bindings(stage):
    fixed_count = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Mesh":
            continue
        path = prim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        # 用 MaterialBindingAPI 获取直接绑定（不走 ComputeBoundMaterial 的 fallback）
        mb = UsdShade.MaterialBindingAPI(prim)
        direct_rel = mb.GetDirectBinding()
        if not direct_rel:
            continue
        direct_path = direct_rel.GetMaterialPath()
        if not direct_path or not str(direct_path):
            continue
        # 检查绑定的材质 prim 是否存在
        target_prim = stage.GetPrimAtPath(direct_path)
        if target_prim and target_prim.IsValid() and target_prim.GetTypeName() == "Material":
            continue  # 绑定有效，跳过
        # 悬空绑定，检查是否有 GeomSubset 子节点带有效材质
        has_subset_binding = False
        for child in prim.GetChildren():
            if child.GetTypeName() == "GeomSubset":
                sub_mb = UsdShade.MaterialBindingAPI(child)
                sub_direct = sub_mb.GetDirectBinding()
                if sub_direct:
                    sub_path = sub_direct.GetMaterialPath()
                    if sub_path and str(sub_path):
                        sp = stage.GetPrimAtPath(sub_path)
                        if sp and sp.IsValid():
                            has_subset_binding = True
                            break
        if has_subset_binding:
            # 尝试清除直接绑定
            try:
                mb.UnbindDirectBinding()
                fixed_count += 1
                print(f"[DANGLING FIX] removed dangling material:binding on {path} (target: {direct_path})")
            except Exception as e:
                # fallback: 用 Sdf 层级操作
                try:
                    prim_path = prim.GetPath()
                    for spec in prim.GetPrimStack():
                        layer = spec.layer
                        rel_spec = layer.GetRelationshipAtPath(prim_path.AppendProperty("material:binding"))
                        if rel_spec:
                            rel_spec.targetPathList.ClearAndMakeExplicit()
                            fixed_count += 1
                            print(f"[DANGLING FIX] SDF: cleared binding on {path} in layer {layer.identifier}")
                except Exception as e2:
                    print(f"[DANGLING FIX] FAILED on {path}: {e} / {e2}")
    print(f"[DANGLING FIX] fixed {fixed_count} dangling material bindings")


# 收集所有mesh和使用了UDIM纹理的material
def collect_allUdimMat_allMeshes(state, stage):
    contain_refmesh = False
    mesh_prims = []
    for prim in stage.Traverse():
        path = prim.GetPath().pathString

        # 跳过isaac-sim默认添加的节点, 仅处理从USD文件中导入的节点
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        if is_ref_or_instance(prim):
            contain_refmesh = True
        typeName = prim.GetTypeName()
        if typeName == "Material" and detect_udim_texture(stage, prim):
            state.udim_material_prims.add(prim)
        if typeName == "Mesh":
            mesh_prims.append(prim)
    return mesh_prims, contain_refmesh




def fix_project_uvw_materials(stage):
    """
    OmniPBR 的 inputs:project_uvw=True 时, 纹理坐标由物体空间位置投影生成(triplanar),
    glTF 导出器不支持此功能, 导致纹理只铺一次且方向错误。
    对每个使用 project_uvw 材质的 mesh, 根据面法线方向将顶点世界坐标投影到对应平面,
    重新生成 UV 坐标, 近似恢复投影纹理的平铺密度和方向。
    同时将法线转为 faceVarying, 确保导出器拆分顶点后同一面法线一致。
    注意: primvar 'st' 使用 faceVarying 插值, UV 数量必须等于 faceVertexIndices 数量。
    """
    # 三平面投影 UV 轴映射配置
    # 格式: (u_axis, v_axis, u_sign, v_sign)
    # axis: 0=X, 1=Y, 2=Z; sign: 1 或 -1
    # 纹理横竖错误(旋转90度) → 交换 u_axis 和 v_axis
    # 纹理镜像 → 翻转对应 sign
    TRIPLANAR_UV_MAP = {
        'x': (1, 2, 1, 1),   # X法线面(左右墙): u=Y, v=Z
        'y': (0, 2, 1, 1),   # Y法线面(地板/天花板): u=X, v=Z
        'z': (1, 0, 1, 1),   # Z法线面(前后墙): u=Y, v=X
    }

    project_uvw_mats = set()
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Material":
            continue
        path = prim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        for child in prim.GetAllChildren():
            shader = UsdShade.Shader(child)
            if not shader:
                continue
            shader_id = shader.GetIdAttr().Get()
            if shader_id != "OmniPBR":
                continue
            project_uvw_attr = child.GetAttribute("inputs:project_uvw")
            if project_uvw_attr:
                val = project_uvw_attr.Get()
                if val:
                    project_uvw_mats.add(prim)
                    log_tag("PROJECT UVW", f"{prim.GetName()}: project_uvw=True")
                    break

    if not project_uvw_mats:
        return 0

    fixed = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Mesh":
            continue
        path = prim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue

        mb = UsdShade.MaterialBindingAPI(prim)
        material, _ = mb.ComputeBoundMaterial()
        mat_prim = material.GetPrim() if material else None
        has_puvw = mat_prim in project_uvw_mats if mat_prim else False

        if not has_puvw:
            for child in prim.GetAllChildren():
                if child.GetTypeName() != "GeomSubset":
                    continue
                sub_mb = UsdShade.MaterialBindingAPI(child)
                sub_mat, _ = sub_mb.ComputeBoundMaterial()
                if sub_mat and sub_mat.GetPrim() in project_uvw_mats:
                    has_puvw = True
                    break

        if not has_puvw:
            continue

        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        face_counts = mesh.GetFaceVertexCountsAttr().Get()
        face_indices = mesh.GetFaceVertexIndicesAttr().Get()

        if not points or not face_counts or not face_indices or len(points) == 0:
            continue

        # 获取 mesh 的 world transform, project_uvw 使用世界空间位置
        xformable = UsdGeom.Xformable(prim)
        try:
            world_matrix = xformable.ComputeLocalToWorldTransform(0.0)
        except Exception:
            world_matrix = None

        # 将局部坐标点变换到世界空间
        world_points = []
        for p in points:
            if world_matrix:
                wp = world_matrix.Transform(Gf.Vec3f(float(p[0]), float(p[1]), float(p[2])))
                world_points.append((float(wp[0]), float(wp[1]), float(wp[2])))
            else:
                world_points.append((float(p[0]), float(p[1]), float(p[2])))

        # 为每个面顶点生成 UV (faceVarying 插值, 数量 = len(face_indices))
        # 使用面法线确定投影方向, 同一顶点在不同面上可有不同 UV
        # 同时处理法线: 优先使用 USD 已有法线 (展开为 faceVarying), 无法线时才计算面法线
        corner_count = sum(int(c) for c in face_counts)
        existing_normals, _, n_status = _get_facevarying_source_normals(mesh, face_counts, face_indices, points)
        use_existing = (n_status == "ok" and existing_normals is not None
                        and len(existing_normals) == corner_count)
        new_uvs = []
        new_normals = []
        idx = 0
        for count in face_counts:
            count = int(count)
            if count < 3:
                for _ in range(count):
                    new_uvs.append((0.0, 0.0))
                    if not use_existing:
                        new_normals.append((0.0, 0.0, 1.0))
                idx += count
                continue

            v0 = world_points[int(face_indices[idx])]
            v1 = world_points[int(face_indices[idx + 1])]
            v2 = world_points[int(face_indices[idx + 2])]
            e1 = (v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2])
            e2 = (v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2])
            nx = e1[1]*e2[2] - e1[2]*e2[1]
            ny = e1[2]*e2[0] - e1[0]*e2[2]
            nz = e1[0]*e2[1] - e1[1]*e2[0]
            mag = math.sqrt(nx*nx + ny*ny + nz*nz)
            if mag > 1e-10:
                nx, ny, nz = nx/mag, ny/mag, nz/mag

            ax, ay, az = abs(nx), abs(ny), abs(nz)

            # 无已有法线时, 从局部空间点计算面法线 (法线属性需要局部空间)
            lnx = lny = lnz = 0.0
            if not use_existing:
                lv0 = points[int(face_indices[idx])]
                lv1 = points[int(face_indices[idx + 1])]
                lv2 = points[int(face_indices[idx + 2])]
                le1 = (float(lv1[0])-float(lv0[0]), float(lv1[1])-float(lv0[1]), float(lv1[2])-float(lv0[2]))
                le2 = (float(lv2[0])-float(lv0[0]), float(lv2[1])-float(lv0[1]), float(lv2[2])-float(lv0[2]))
                lnx = le1[1]*le2[2] - le1[2]*le2[1]
                lny = le1[2]*le2[0] - le1[0]*le2[2]
                lnz = le1[0]*le2[1] - le1[1]*le2[0]
                lmag = math.sqrt(lnx*lnx + lny*lny + lnz*lnz)
                if lmag > 1e-10:
                    lnx, lny, lnz = lnx/lmag, lny/lmag, lnz/lmag
                else:
                    lnx, lny, lnz = 0.0, 0.0, 1.0

            if ax >= ay and ax >= az:
                ua, va, us, vs = TRIPLANAR_UV_MAP['x']
            elif ay >= ax and ay >= az:
                ua, va, us, vs = TRIPLANAR_UV_MAP['y']
            else:
                ua, va, us, vs = TRIPLANAR_UV_MAP['z']

            for i in range(count):
                vi = int(face_indices[idx + i])
                if 0 <= vi < len(world_points):
                    wp = world_points[vi]
                    u = wp[ua] * us
                    v = wp[va] * vs
                    new_uvs.append((float(u), float(v)))
                else:
                    new_uvs.append((0.0, 0.0))
                if not use_existing:
                    new_normals.append((float(lnx), float(lny), float(lnz)))

            idx += count

        # 显式构造 Vt.Vec2fArray
        uv_array = Vt.Vec2fArray(len(new_uvs))
        for i, (u, v) in enumerate(new_uvs):
            uv_array[i] = Gf.Vec2f(u, v)

        # 设置 primvar: 使用 faceVarying 插值, 清除 indices
        pv_api = UsdGeom.PrimvarsAPI(mesh)
        pv_st = pv_api.GetPrimvar("st")
        if pv_st:
            pv_st.SetInterpolation(UsdGeom.Tokens.faceVarying)
            indices_attr = prim.GetAttribute("primvars:st:indices")
            if indices_attr:
                indices_attr.Clear()
        else:
            pv_st = pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
        pv_st.Set(uv_array)

        # 写入法线 (faceVarying): 优先用 USD 已有法线, 否则用计算的面法线
        # 确保导出 glTF 拆分顶点后法线正确, 同一面顶点法线一致
        if use_existing:
            n_array = existing_normals
        elif new_normals:
            n_array = Vt.Vec3fArray(len(new_normals))
            for i, (nnx, nny, nnz) in enumerate(new_normals):
                n_array[i] = Gf.Vec3f(nnx, nny, nnz)
        else:
            n_array = None
        if n_array is not None:
            mesh.GetNormalsAttr().Set(n_array)
            mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
            pv_n = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("normals")
            if pv_n and pv_n.HasValue():
                pv_n.Set(n_array)
                pv_n.SetInterpolation(UsdGeom.Tokens.faceVarying)
                pv_n.BlockIndices()

        # 验证
        verify = pv_st.Get()
        vok = len(verify) == len(new_uvs) if verify else False

        fixed += 1
        min_u = min(uv[0] for uv in new_uvs)
        max_u = max(uv[0] for uv in new_uvs)
        min_v = min(uv[1] for uv in new_uvs)
        max_v = max(uv[1] for uv in new_uvs)
        log_tag("PROJECT UVW", f"{path}: regenerated {len(new_uvs)} faceVarying UVs, U=[{min_u:.2f},{max_u:.2f}], V=[{min_v:.2f},{max_v:.2f}], verify={'OK' if vok else 'FAIL'}")

    log_tag("PROJECT UVW", f"fixed {fixed} meshes")
    return fixed


# 导出为gltf


async def convert_USD_to_gltf(state, stage):
    usd_file_name = os.path.splitext(os.path.basename(state.usd_file))[0]
    # 若文件或路径名中, 存在多个由"."分割的部分(例: Rack.Small/Rack.Small.Empty.gltf), MetaEngine解析存在问题, 仅保留第一部分
    usd_file_name = usd_file_name.split('.')[0]
    output_path = os.path.join(state.output_folder, usd_file_name)
    # 文件夹已存在, 移除
    if os.path.exists(output_path):
        log_info("The folder where the file is to be saved already exists. Removing.")
        shutil.rmtree(output_path)

    # exporter 的目标父目录必须真实存在
    os.makedirs(output_path, exist_ok=True)

    gltf_file_path = os.path.join(output_path, usd_file_name + '.gltf')
    log_info(f"File saving path: {gltf_file_path}")

    asset_exporter = exporter.Exporter()
    asset_exporter.on_startup()

    # 导出设置, 控制是否导出灯光、材质、是否嵌入纹理、mesh数据等
    context = asset_converter.AssetConverterContext()
    context.embed_textures = False          # Embedding textures into output. This is only enabled for FBX and glTF export.
    context.export_separate_gltf = True     # Export gltf with separate bin file if true, else export one standalone gltf.
    context.ignore_light = False            # Don't import/export lights

    import carb
    _settings = carb.settings.get_settings()
    _old_level = _settings.get("/log/threads/omni.hydra/level")
    _settings.set("/log/threads/omni.hydra/level", 4)

    try:
        await asset_exporter._start_usd_export_internal(stage, gltf_file_path, context)
    except Exception as e:
        error_exit("EXPORT_FAILED", f"导出异常: {e}")
    finally:
        _settings.set("/log/threads/omni.hydra/level", _old_level if _old_level is not None else 2)

    print("=" * 136)
    log_info("File export completed, task finished.")

    # ---- glTF 后处理 (独立模块 gltf_postprocess.py):
    # 补齐缺失纹理 / 修正透明材质 alpha / 假透明改不透明 / 玻璃颜色 gamma /
    # 纯色材质纹理化 / 双面化 mesh ----
    postprocess_gltf(state, stage, gltf_file_path, output_path, thin_sheet_mat_names)


# 移除所有使用了UDIM纹理的材质, 防止导出时报错
def remove_all_udimMat(state, stage):
    if len(state.udim_material_prims) == 0:
        return
    for material in state.udim_material_prims:
        delete_prims(material.GetPath().pathString)
    log_info("All materials that used udim textures have been removed.")


# 收集当前打开usd非reference直接定于的material
def collect_non_instance_material(state, stage):
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        # 跳过isaac-sim默认添加的节点, 仅处理从USD文件中导入的节点
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        typeName = prim.GetTypeName()
        if typeName == "Material":
            state.non_instance_materials_set.add(prim)


def clear_empty_meshes(stage):
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            m = UsdGeom.Mesh(prim)
            counts = m.GetFaceVertexCountsAttr().Get() or []
            indices = m.GetFaceVertexIndicesAttr().Get() or []
            if (len(counts) == 0) or (len(indices) == 0) or (sum(counts) != len(indices)):
                log_info(f"remove path: {prim.GetPath()}")
                delete_prims(prim.GetPath().pathString)


# 将所有reference的usd取消instance
def cancel_all_instance(stage):
    instance_prim = []
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        # 跳过isaac-sim默认添加的节点, 仅处理从USD文件中导入的节点
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        if prim.IsInstanceable():
            instance_prim.append(prim)
    if len(instance_prim) == 0:
        return

    for prim in instance_prim:
        prim.SetInstanceable(False)
    cancel_all_instance(stage)


def _expand_st_to_facevarying(mesh, st_pv, face_vertex_indices, corner_count):
    """
    将任意常见插值的 st primvar 转成 faceVarying 非索引数据。
    返回: Vt.Vec2fArray 或 None
    """
    if not st_pv:
        log_info("st is none")
        return None

    st_vals = st_pv.Get()
    if st_vals is None or len(st_vals) == 0:
        log_info(f"st_vals is none; {len(st_vals)}")
        return None

    interp = st_pv.GetInterpolation()
    st_indices = st_pv.GetIndices()

    # 1) 已是 indexed：优先按 indices 展开
    if st_indices is not None and len(st_indices) > 0:
        if len(st_indices) != corner_count:
            return None
        out = []
        for si in st_indices:
            if si < 0 or si >= len(st_vals):
                return None
            out.append(st_vals[si])
        return Vt.Vec2fArray(out)

    # 2) 非索引数据：按插值处理
    if interp == UsdGeom.Tokens.faceVarying:
        if len(st_vals) != corner_count:
            return None
        return Vt.Vec2fArray(st_vals)

    if interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
        # 按点索引展开到 corner
        out = []
        for pi in face_vertex_indices:
            if pi < 0 or pi >= len(st_vals):
                return None
            out.append(st_vals[pi])
        return Vt.Vec2fArray(out)

    # constant/uniform 对 glTF UV 通常不实用，直接判失败
    return None


def flatten_and_pad_uv_to_facevarying(pv_api, pvar_name, corner_count, path, face_vertex_indices=None):
    """
    核心提炼函数：将任意 UV Primvar 转换为扁平化的 faceVarying 状态，
    并严格对齐到指定的 corner_count 长度。
    [face_vertex_indices]: 当 source 为 vertex/varying 插值 (值数=顶点数<corner数) 时,
                           必须按面片顶点索引展开到 corner, 否则补零会导致纹理采样错乱。
    """
    source_pv = pv_api.GetPrimvar(pvar_name)
    if not source_pv or not source_pv.HasValue():
        return False

    source_values = source_pv.Get()
    source_indices = source_pv.GetIndices()
    interp = source_pv.GetInterpolation()
    if source_values is None or len(source_values) == 0:
        return False

    expanded_values = []

    if source_indices is not None and len(source_indices) > 0:
        # 索引化数据: 直接按索引展开
        for raw_index in source_indices:
            uv_index = int(raw_index)
            if uv_index < 0 or uv_index >= len(source_values):
                log_tag("UV PAD ERROR", f"{path}: {pvar_name} index {uv_index} out of range")
                return False
            uv = source_values[uv_index]
            expanded_values.append(Gf.Vec2f(float(uv[0]), float(uv[1])))
    elif interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying) and face_vertex_indices is not None:
        # vertex/varying 插值: 值数=顶点数, 需按面片顶点索引展开到 corner
        if len(source_values) < corner_count and len(face_vertex_indices) == corner_count:
            for pi in face_vertex_indices:
                if pi < 0 or pi >= len(source_values):
                    print(f"[UV PAD ERROR] {path}: {pvar_name} vertex index {pi} out of range")
                    return False
                uv = source_values[pi]
                expanded_values.append(Gf.Vec2f(float(uv[0]), float(uv[1])))
        else:
            expanded_values = [Gf.Vec2f(float(uv[0]), float(uv[1])) for uv in source_values]
    else:
        # 非索引, 非 vertex: 直接使用 (faceVarying/constant 等)
        expanded_values = [Gf.Vec2f(float(uv[0]), float(uv[1])) for uv in source_values]

    source_count = len(expanded_values)
    fixed_values = expanded_values[:corner_count]
    if len(fixed_values) < corner_count:
        log_tag("UV PAD WARN", f"{path}: {pvar_name} has {len(expanded_values)} values > corner_count {corner_count}, truncating")
        fixed_values.extend(Gf.Vec2f(0.0, 0.0) for _ in range(corner_count - len(fixed_values)))

    fixed_pv = pv_api.CreatePrimvar(pvar_name, Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
    fixed_pv.SetInterpolation(UsdGeom.Tokens.faceVarying)
    fixed_pv.Set(Vt.Vec2fArray(fixed_values))
    fixed_pv.BlockIndices()

    log_tag("UV PAD OK", f"{path}: {pvar_name}, source={source_count}, output={len(fixed_values)}")
    return True


def cleanup_stage_for_gltf_export(state, stage, deactivate_invalid=True):
    """
    在导出 glTF 前清理当前 stage，降低 Isaac Sim 导出崩溃概率。
    主要动作：
    - 拓扑一致性校验
    - st 统一转为 faceVarying + 非索引
    - 法线统一转为 faceVarying（保留原始方向，不重算）
    - subdivisionScheme 强制为 none
    """
    total_mesh = 0
    cleaned = 0
    deactivated = 0
    skipped = 0

    needs_udim_uv_cleanup = len(state.udim_material_prims) > 0

    for prim in Usd.PrimRange(stage.GetPseudoRoot()):
        if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive():
            continue

        total_mesh += 1
        mesh = UsdGeom.Mesh(prim)
        path = prim.GetPath().pathString

        # --- 基础拓扑 ---
        fvc = mesh.GetFaceVertexCountsAttr().Get()
        fvi = mesh.GetFaceVertexIndicesAttr().Get()
        points = mesh.GetPointsAttr().Get()

        if not fvc or not fvi or not points:
            log_tag("cleanup", f"empty/invalid mesh data: {path}")
            if deactivate_invalid: prim.SetActive(False); deactivated += 1
            else: skipped += 1
            continue

        corner_count = sum(fvc)
        if len(fvi) != corner_count or (len(fvi) > 0 and (min(fvi) < 0 or max(fvi) >= len(points))):
            log_tag("cleanup", f"topology mismatch or indices out of range: {path}")
            if deactivate_invalid: prim.SetActive(False); deactivated += 1
            else: skipped += 1
            continue

        pv_api = UsdGeom.PrimvarsAPI(mesh)

        # --- UV 规范化 ---
        if needs_udim_uv_cleanup:
            # UDIM
            st_pv = pv_api.GetPrimvar("st")
            st_facevarying = _expand_st_to_facevarying(mesh, st_pv, fvi, corner_count)
            if st_facevarying is None:
                log_tag("cleanup", f"invalid/unsupported st primvar: {path}")
                if deactivate_invalid: prim.SetActive(False); deactivated += 1
                else: skipped += 1
                continue

            new_st = pv_api.CreatePrimvar("st", st_pv.GetTypeName() if st_pv else Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
            new_st.Set(st_facevarying)
            st_idx_attr = prim.GetAttribute("primvars:st:indices")
            if st_idx_attr and st_idx_attr.IsValid():
                st_idx_attr.Block()
        else:
            result = flatten_and_pad_uv_to_facevarying(pv_api, 'st', corner_count, path, fvi)
            if not result:
                if deactivate_invalid: prim.SetActive(False); deactivated += 1
                else: skipped += 1

        # 针对副UV连环报错，采用数据对齐覆写消除警告
        secondary_uv_names = []
        for pv in pv_api.GetPrimvars():
            pvar_name = str(pv.GetPrimvarName())
            if re.fullmatch(r"st_\d+", pvar_name):
                secondary_uv_names.append(pvar_name)

        secondary_uv_names.sort(key=lambda name: int(name.split("_")[-1]))
        for pvar_name in secondary_uv_names:
            result = flatten_and_pad_uv_to_facevarying(pv_api, pvar_name, corner_count, path, fvi)
            if not result:
                if deactivate_invalid: prim.SetActive(False); deactivated += 1
                else: skipped += 1

        # 薄片 mesh 检测: 目标引擎忽略 doubleSided, 单面薄片 mesh(如贴地地毯)从背面看被剔除。
        # 薄片 bbox 最小轴厚度 < 最大轴的 5% 且 < 2cm, 且绕序可能颠倒(单面渲染下两面都剔除),
        # 标记其材质, 在双面化阶段复制三角形保证两面可见。只处理 Visual mesh, 跳过 Collision。
        needs_double_side = False
        if "/Collisions/" not in path and "Collision" not in path:
            if points:
                xs = [p[0] for p in points]; ys = [p[1] for p in points]; zs = [p[2] for p in points]
                sx = max(xs) - min(xs); sy = max(ys) - min(ys); sz = max(zs) - min(zs)
                max_span = max(sx, sy, sz)
                min_span = min(sx, sy, sz)
                if max_span > 1e-4 and min_span < max_span * 0.05 and min_span < 0.02:
                    needs_double_side = True
                    print(f"[THIN SHEET] {path}: spans x={sx:.4f} y={sy:.4f} z={sz:.4f}, double-side")
            if needs_double_side:
                global thin_sheet_mat_names
                try:
                    mb = UsdShade.MaterialBindingAPI(prim)
                    mat, _ = mb.ComputeBoundMaterial()
                    if mat:
                        thin_sheet_mat_names.add(mat.GetPrim().GetName())
                        print(f"[DOUBLE-SIDE NEEDED] {path}: will double-side")
                except Exception as e:
                    print(f"[DOUBLE-SIDE NEEDED WARN] {path}: {e}")

        # 法线：展开为 faceVarying 保留原始方向（不再直接 Block）
        expanded_normals, source, normal_status = _get_facevarying_source_normals(mesh, fvc, fvi, points)
        if expanded_normals is not None:
            _write_facevarying_normals(mesh, expanded_normals)

        mesh.GetSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        cleaned += 1

    log_tag("cleanup", f"done. total_mesh={total_mesh}, cleaned={cleaned}, deactivated={deactivated}, skipped={skipped}")


def flatten_material_interface(stage):
    """
    将 Material 接口模式展开为 Shader 赋值。

    USD 的 Material 接口模式:
      Material { inputs:diffuseColor = (0,0,0) }
        Shader { inputs:diffuseColor.connect = </Material.inputs:diffuseColor> }

    glTF 导出器不跟随 Material→Shader 的连接链, 导致颜色/PBR 参数丢失。
    本函数检测同名匹配: Material 有 inputs:X = value, Shader 有 inputs:X.connect,
    则断开连接并写入值。不依赖路径比较, 避免多层引用路径重映射问题。
    """
    fixed_count = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Material":
            continue
        path = prim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue

        mat = UsdShade.Material(prim)

        mat_input_values = {}
        for mat_input in mat.GetInputs():
            val = mat_input.Get()
            if val is not None:
                mat_input_values[mat_input.GetBaseName()] = val

        if not mat_input_values:
            continue

        for child in prim.GetChildren():
            if child.GetTypeName() != "Shader":
                continue
            sh = UsdShade.Shader(child)
            for shader_input in sh.GetInputs():
                base_name = shader_input.GetBaseName()
                if base_name not in mat_input_values:
                    continue
                attr = shader_input.GetAttr()
                if not attr or not attr.GetConnections():
                    continue
                val = mat_input_values[base_name]
                attr.SetConnections([])
                shader_input.Set(val)
                after_conn = attr.GetConnections()
                after_val = shader_input.Get()
                if after_conn:
                    log_tag("MAT INTERFACE WARN", f"{path}/{child.GetName()}.{base_name}: connections still present")
                fixed_count += 1
                log_tag("MAT INTERFACE", f"{path}/{child.GetName()}.{base_name} = {val} (conn={after_conn}, val={after_val})")
    log_tag("MAT INTERFACE", f"flattened {fixed_count} material interface connections")


# 展开 Material 内的 NodeGraph: 把 NodeGraph 内部的 UsdUVTexture 提升为 Material 直接子节点,
# 并重连 diffuseColor -> UsdUVTexture.outputs:rgb, 使材质结构与导出器能正常处理的
# MI_White001 (UsdPreviewSurface + 直接 UsdUVTexture) 一致。
def flatten_material_nodegraphs(stage):
    fixed_count = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Material":
            continue
        path = prim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        mat = UsdShade.Material(prim)
        # 找 diffuseColor 连接到的 NodeGraph 输出
        shader_nodes = {}
        for child in prim.GetChildren():
            if child.GetTypeName() == "Shader":
                shader_nodes[child.GetName()] = child
        # 收集 NodeGraph 子节点
        node_graphs = {}
        for child in prim.GetChildren():
            if child.GetTypeName() == "NodeGraph":
                node_graphs[child.GetName()] = child
        if not node_graphs:
            continue
        # 遍历 shader, 把 diffuseColor 的连接从 NodeGraph 改到提升后的 UsdUVTexture
        for sname, sprim in shader_nodes.items():
            sh = UsdShade.Shader(sprim)
            for inp in sh.GetInputs():
                if inp.GetBaseName() not in ("diffuseColor", "opacity"):
                    continue
                srcs = inp.GetConnectedSources()
                if not srcs:
                    continue
                # GetConnectedSources 返回 tuple, 每元素是 list(ConnectionSourceInfo)
                for src_group in srcs:
                    for src_info in src_group:
                        src_path = src_info.source.GetPath()
                        src_name = src_info.sourceName
                        # 连接来源是 NodeGraph 的输出
                        src_prim = stage.GetPrimAtPath(src_path)
                        if not src_prim or src_prim.GetTypeName() != "NodeGraph":
                            continue
                        ng_children = src_prim.GetChildren()
                        # 在 NodeGraph 内部找同名输出对应的 shader (如 outputs:rgb -> UsdUVTexture.outputs:rgb)
                        tex_shader = None
                        uvmap_shader = None
                        for nc in ng_children:
                            ca = UsdShade.ConnectableAPI(nc)
                            for out in ca.GetOutputs():
                                if out.GetBaseName() == src_name:
                                    tex_shader = nc
                        if tex_shader is None:
                            continue
                        # 提升: 在 Material 下创建独立的 UsdUVTexture / PrimvarReader (避免与 NodeGraph 同名冲突)
                        new_tex_name = src_prim.GetName() + "_flattened"
                        new_tex_prim = prim.GetChild(new_tex_name)
                        if not new_tex_prim or not new_tex_prim.IsValid():
                            new_tex_prim = stage.DefinePrim(prim.GetPath().AppendChild(new_tex_name), "Shader")
                        else:
                            # 已存在则清空旧属性
                            for a in list(new_tex_prim.GetAuthoredAttributes()):
                                a.Clear()
                        # 复制 tex_shader 的属性到新 prim (跳过有连接/输出的属性, 连接后面重连)
                        for attr in tex_shader.GetAttributes():
                            if attr.GetName().startswith("outputs:"):
                                continue
                            if attr.GetConnections():
                                continue
                            a = new_tex_prim.GetAttribute(attr.GetName())
                            if not a or not a.IsValid():
                                a = new_tex_prim.CreateAttribute(attr.GetName(), attr.GetTypeName())
                            a.Set(attr.Get())
                        # 复制 uvmap (UsdPrimvarReader) - 找到 PrimvarReader
                        for nc in ng_children:
                            sh2 = UsdShade.Shader(nc)
                            if sh2 and sh2.GetIdAttr().Get() == "UsdPrimvarReader_float2":
                                uvmap_shader = nc
                        if uvmap_shader is not None:
                            uv_name = new_tex_name + "_uvmap"
                            uv_prim = prim.GetChild(uv_name)
                            if not uv_prim or not uv_prim.IsValid():
                                uv_prim = stage.DefinePrim(prim.GetPath().AppendChild(uv_name), "Shader")
                            for attr in uvmap_shader.GetAttributes():
                                if attr.GetName().startswith("outputs:"):
                                    continue
                                if attr.GetConnections():
                                    continue
                                a = uv_prim.GetAttribute(attr.GetName())
                                if not a or not a.IsValid():
                                    a = uv_prim.CreateAttribute(attr.GetName(), attr.GetTypeName())
                                a.Set(attr.Get())
                            # 连接 UsdUVTexture.inputs:st <- uvmap.outputs:result
                            tex_ca = UsdShade.ConnectableAPI(new_tex_prim)
                            uv_ca = UsdShade.ConnectableAPI(uv_prim)
                            st_in = tex_ca.GetInput("st")
                            if st_in:
                                st_in.ConnectToSource(uv_ca, "result")
                        # 确保 new_tex_prim 有 outputs:<src_name> (如 outputs:rgb)
                        new_ca = UsdShade.ConnectableAPI(new_tex_prim)
                        try:
                            new_out = new_ca.CreateOutput(src_name, Sdf.ValueTypeNames.Float3)
                        except Exception:
                            new_out = new_ca.GetOutput(src_name)
                        # 重连 shader.diffuseColor <- new_tex.outputs:rgb (直接用 SetConnections 覆盖)
                        new_conn_path = Sdf.Path(new_tex_prim.GetPath().pathString + '.outputs:' + src_name)
                        inp_attr = inp.GetAttr()
                        if inp_attr:
                            try:
                                inp_attr.SetConnections([new_conn_path])
                            except Exception as e:
                                print(f"[NODEGRAPH FLATTEN WARN] {path}: SetConnections failed: {e}")
                        fixed_count += 1
                        print(f"[NODEGRAPH FLATTEN] {path}: diffuseColor {src_path} -> {new_tex_prim.GetPath()}")
                        # 删除旧 NodeGraph, 避免导出器仍读其 .jpg 引用
                        try:
                            omni.kit.commands.execute('DeletePrims',
                                paths=[src_prim.GetPath().pathString],
                                destructive=False)
                            print(f"[NODEGRAPH FLATTEN] removed old NodeGraph: {src_prim.GetPath()}")
                        except Exception as e:
                            print(f"[NODEGRAPH FLATTEN WARN] failed to remove NodeGraph {src_prim.GetPath()}: {e}")
                        break
    print(f"[NODEGRAPH FLATTEN] fixed {fixed_count} materials")


def deduplicate_texture_filenames(state, stage):
    """
    检测同 basename 但不同源路径的纹理文件(如多个 image0.png),
    创建唯一命名副本并更新 USD 引用, 防止 glTF 导出时纹理被覆盖。
    """
    import hashlib

    usd_dir = os.path.dirname(os.path.abspath(state.usd_file))
    tex_root = os.path.join(usd_dir, "textures")
    os.makedirs(tex_root, exist_ok=True)

    basename_sources = defaultdict(lambda: defaultdict(list))

    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        for attr in prim.GetAttributes():
            val = attr.Get()
            if val is None:
                continue
            if isinstance(val, Sdf.AssetPath):
                asset_paths = [val]
                is_array = False
            else:
                try:
                    asset_paths = list(val)
                    if len(asset_paths) == 0 or not isinstance(asset_paths[0], Sdf.AssetPath):
                        continue
                    is_array = True
                except TypeError:
                    continue

            for idx, asset_path in enumerate(asset_paths):
                authored = asset_path.path
                if not authored or "<UDIM>" in authored:
                    continue
                resolved = asset_path.resolvedPath
                check_path = resolved if resolved else authored
                if not check_path:
                    continue
                check_path = os.path.normpath(check_path)
                if not os.path.exists(check_path):
                    continue
                basename = os.path.basename(check_path)
                basename_sources[basename][check_path].append(
                    (attr, idx, is_array, authored)
                )

    renamed_count = 0
    copied_count = 0

    for basename, sources in basename_sources.items():
        if len(sources) <= 1:
            continue

        log_tag("TEX DEDUP", f"'{basename}' has {len(sources)} unique source files, deduplicating...")

        source_to_newname = {}
        for src_path in sources:
            rel = os.path.relpath(src_path, usd_dir)
            parts = rel.replace('\\', '/').split('/')
            meaningful_parts = [
                _safe_name(p) for p in parts[:-1]
                if p.lower() not in ('texture', 'textures', 'assets', 'fixtures', 'objects')
            ]
            if meaningful_parts:
                unique_name = '_'.join(meaningful_parts) + '_' + basename
            else:
                stem, ext = os.path.splitext(basename)
                unique_name = f"{stem}_{hashlib.md5(src_path.encode()).hexdigest()[:8]}{ext}"

            if len(unique_name) > 100:
                stem, ext = os.path.splitext(basename)
                unique_name = f"{stem}_{hashlib.md5(src_path.encode()).hexdigest()[:8]}{ext}"

            src_ext = os.path.splitext(src_path)[1].lower()
            if src_ext in ('.jpg', '.jpeg'):
                png_candidate = os.path.splitext(src_path)[0] + '.png'
                if os.path.exists(png_candidate):
                    src_path_copy = png_candidate
                    unique_name = os.path.splitext(unique_name)[0] + '.png'
                else:
                    src_path_copy = src_path
            else:
                src_path_copy = src_path

            dest_path = os.path.join(tex_root, unique_name)
            if not os.path.exists(dest_path):
                shutil.copy2(src_path_copy, dest_path)
                copied_count += 1
                log_tag("TEX DEDUP COPY", f"{src_path_copy} -> {dest_path}")
            source_to_newname[src_path] = unique_name

        for src_path, entries in sources.items():
            new_name = source_to_newname.get(src_path)
            if not new_name:
                continue
            new_authored = os.path.join("textures", new_name)
            for attr, idx, is_array, old_authored in entries:
                if is_array:
                    current_val = list(attr.Get())
                    current_val[idx] = Sdf.AssetPath(new_authored)
                    attr.Set(current_val)
                else:
                    attr.Set(Sdf.AssetPath(new_authored))
                renamed_count += 1
                log_tag("TEX DEDUP", f"{attr.GetPath()}: {old_authored} -> {new_authored}")

    log_tag("TEX DEDUP", f"renamed {renamed_count} references, copied {copied_count} files")


def fix_texture_asset_paths(state, stage):
    """
    修正 USD 中纹理资源路径与磁盘文件扩展名不匹配的问题。
    策略1: 遍历所有 prim 的所有属性，修正 asset path 并创建文件副本
    策略2: 扫描 USD 文件父目录下的 .jpg 文件，创建 .png 副本（兜底）
    """
    fixed_count = 0
    checked_count = 0
    copied_count = 0
    copied_files = set()
    usd_dir = os.path.dirname(os.path.abspath(state.usd_file))

    # ---- 策略0: .jpg -> .png 引用改写 (导出器对 .jpg 支持不佳, 利用已有同名 .png) ----
    # 把 .png 拷贝到 scene.usd 同级的 textures/ 目录, 引用改为 ./textures/<name>.png,
    # 与导出器已成功处理的其他纹理(如 T_White001_BC.png)保持一致
    tex_root = os.path.join(usd_dir, "textures")
    os.makedirs(tex_root, exist_ok=True)
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue
        for attr in prim.GetAttributes():
            val = attr.Get()
            if val is None:
                continue
            if isinstance(val, Sdf.AssetPath):
                asset_paths = [val]
                is_array = False
            else:
                try:
                    asset_paths = list(val)
                    if len(asset_paths) == 0 or not isinstance(asset_paths[0], Sdf.AssetPath):
                        continue
                    is_array = True
                except TypeError:
                    continue
            modified = False
            for idx, asset_path in enumerate(asset_paths):
                authored = asset_path.path
                if not authored or "<UDIM>" in authored:
                    continue
                if not authored.lower().endswith(('.jpg', '.jpeg')):
                    continue
                resolved = asset_path.resolvedPath
                check_path = resolved if resolved else authored
                if not check_path:
                    continue
                check_path = os.path.normpath(check_path)
                if not os.path.exists(check_path):
                    continue  # .jpg 不存在, 交给策略1处理
                png_candidate = os.path.splitext(check_path)[0] + '.png'
                if not os.path.exists(png_candidate):
                    continue  # 无同名 .png, 保持原样
                # 拷贝到 scene.usd 同级 textures/ 目录
                png_name = os.path.basename(png_candidate)
                dest_png = os.path.join(tex_root, png_name)
                if not os.path.exists(dest_png):
                    try:
                        shutil.copy2(png_candidate, dest_png)
                        copied_count += 1
                        print(f"[TEXTURE JPG2PNG COPY] {png_candidate} -> {dest_png}")
                    except Exception as e:
                        print(f"[TEXTURE JPG2PNG COPY WARN] {png_candidate}: {e}")
                # 引用改为 ./textures/<name>.png (与导出器正常处理的纹理格式一致)
                new_authored = os.path.join("textures", png_name)
                if is_array:
                    asset_paths[idx] = Sdf.AssetPath(new_authored)
                    modified = True
                else:
                    attr.Set(Sdf.AssetPath(new_authored))
                fixed_count += 1
                print(f"[TEXTURE JPG2PNG] {attr.GetPath()}: {authored} -> {new_authored}")
            if modified and is_array:
                attr.Set(asset_paths)

    # ---- 策略1: 遍历 stage 所有属性 ----
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        if path.startswith('/Render') or path.startswith('/Omni'):
            continue

        for attr in prim.GetAttributes():
            val = attr.Get()
            if val is None:
                continue

            if isinstance(val, Sdf.AssetPath):
                asset_paths = [val]
                is_array = False
            else:
                try:
                    asset_paths = list(val)
                    if len(asset_paths) == 0 or not isinstance(asset_paths[0], Sdf.AssetPath):
                        continue
                    is_array = True
                except TypeError:
                    continue

            modified = False
            for idx, asset_path in enumerate(asset_paths):
                authored = asset_path.path
                if not authored or "<UDIM>" in authored:
                    continue

                checked_count += 1
                resolved = asset_path.resolvedPath
                check_path = resolved if resolved else authored
                if not check_path:
                    continue
                check_path = os.path.normpath(check_path)
                if os.path.exists(check_path):
                    continue

                base, ext = os.path.splitext(check_path)
                if not ext:
                    # 无扩展名的 asset path(如 ./textures/3d66-falloff-xxx): 无替代可找,
                    # 若是 Shader 纹理输入则直接清空, 避免导出器尝试拷贝报错
                    if prim.GetTypeName() == "Shader" and attr.GetName().startswith("inputs:"):
                        if is_array:
                            asset_paths[idx] = Sdf.AssetPath("")
                            modified = True
                        else:
                            attr.Set(Sdf.AssetPath(""))
                        fixed_count += 1
                        log_tag("TEXTURE CLEAR", f"{attr.GetPath()}: missing {check_path} cleared")
                    continue

                found = False
                for new_ext in COMMON_TEXTURE_EXTENSIONS:
                    if new_ext.lower() == ext.lower():
                        continue
                    candidate = base + new_ext
                    if os.path.exists(candidate):
                        authored_base, _ = os.path.splitext(authored)
                        new_authored = authored_base + new_ext
                        if is_array:
                            asset_paths[idx] = Sdf.AssetPath(new_authored)
                            modified = True
                        else:
                            attr.Set(Sdf.AssetPath(new_authored))
                        fixed_count += 1
                        found = True
                        log_tag("TEXTURE FIX", f"{attr.GetPath()}: {authored} -> {new_authored}")

                        copy_key = (candidate, check_path)
                        if copy_key not in copied_files:
                            copied_files.add(copy_key)
                            try:
                                shutil.copy2(candidate, check_path)
                                copied_count += 1
                                log_tag("TEXTURE COPY", f"{candidate} -> {check_path}")
                            except Exception as e:
                                log_tag("TEXTURE COPY WARN", f"{candidate} -> {check_path}: {e}")
                        break

                if not found:
                    # 纹理文件确认缺失: 若是 Shader 的纹理输入属性(inputs:file 等),
                    # 清空 asset path 让 shader 回退默认值, 避免导出器尝试拷贝报错。
                    if prim.GetTypeName() == "Shader" and attr.GetName().startswith("inputs:"):
                        if is_array:
                            asset_paths[idx] = Sdf.AssetPath("")
                            modified = True
                        else:
                            attr.Set(Sdf.AssetPath(""))
                        fixed_count += 1
                        log_tag("TEXTURE CLEAR", f"{attr.GetPath()}: missing {check_path} cleared")
                    else:
                        log_tag("TEXTURE WARNING", f"{attr.GetPath()}: cannot find {check_path}")

            if modified and is_array:
                attr.Set(asset_paths)

    # ---- 策略2: 扫描文件系统兜底 ----
    log_tag("TEXTURE FIX", f"scanning {usd_dir} for .jpg files to create .png copies...")
    for root, dirs, files in os.walk(usd_dir):
        for f in files:
            if not f.lower().endswith('.jpg'):
                continue
            jpg_path = os.path.join(root, f)
            png_path = os.path.splitext(jpg_path)[0] + '.png'
            if os.path.exists(png_path):
                continue
            copy_key = (jpg_path, png_path)
            if copy_key in copied_files:
                continue
            copied_files.add(copy_key)
            try:
                shutil.copy2(jpg_path, png_path)
                copied_count += 1
                log_tag("TEXTURE COPY", f"{jpg_path} -> {png_path}")
            except Exception as e:
                log_tag("TEXTURE COPY WARN", f"{jpg_path} -> {png_path}: {e}")

    log_tag("TEXTURE FIX", f"checked={checked_count}, fixed={fixed_count}, copied={copied_count}")

    # ---- 策略3: 在 textures/ 和 texture/ 目录中创建 .jpg 副本 ----
    # 导出器从 Fabric 层读取旧 .jpg 引用 (如 ./texture/xxx.jpg), 绕过 USD 属性改写。
    # 确保 scene.usd 同级的 textures/ 和 texture/ 目录里都有 .jpg 文件可供导出器拷贝。
    jpg_copy_count = 0
    tex_dirs = [tex_root, os.path.join(usd_dir, "texture")]
    for tex_dir in tex_dirs:
        os.makedirs(tex_dir, exist_ok=True)
    # 从 tex_root 的 .png 文件出发, 在两个目录都创建 .jpg 副本
    for f in os.listdir(tex_root):
        if not f.lower().endswith('.png'):
            continue
        base_name = os.path.splitext(f)[0]
        # 从源目录查找同名 .jpg (内容与 .png 一致)
        src_jpg = None
        for root, dirs, files in os.walk(usd_dir):
            for sf in files:
                if sf == base_name + '.jpg':
                    src_jpg = os.path.join(root, sf)
                    break
            if src_jpg:
                break
        if not src_jpg or not os.path.exists(src_jpg):
            # 无源 .jpg, 用 .png 作为源
            src_jpg = os.path.join(tex_root, f)
        for tex_dir in tex_dirs:
            jpg_path = os.path.join(tex_dir, base_name + '.jpg')
            if os.path.exists(jpg_path):
                continue
            try:
                shutil.copy2(src_jpg, jpg_path)
                jpg_copy_count += 1
                print(f"[TEXTURE JPG COPY] {src_jpg} -> {jpg_path}")
            except Exception as e:
                print(f"[TEXTURE JPG COPY WARN] {src_jpg}: {e}")
    print(f"[TEXTURE JPG COPY] created {jpg_copy_count} .jpg copies")


# 主程序, 转换入口
def convert_task():
    print("\n\n\n\n\n\n")
    print("=" * 136)

    # 解析参数并校验路径
    args = parse_args()
    usd_file = os.path.abspath(args.input)
    output_folder = os.path.abspath(args.output)
    if not os.path.isfile(usd_file):
        error_exit("USD_FILE_NOT_FOUND", f"USD 文件不存在: {usd_file}")
    try:
        os.makedirs(output_folder, exist_ok=True)
    except Exception as e:
        error_exit("OUTPUT_DIR_INVALID", f"创建输出目录失败: {e}")

    # 创建状态容器
    state = ConverterState(usd_file, output_folder)

    log_info(f"Start the gltf file export task.")
    log_info(f"Input file: {state.usd_file}")
    log_info(f"Output directory: {state.output_folder}")

    start_time = time.time()
    import_status = import_usd(state.usd_file)
    end_time = time.time()
    elapsed_time = end_time - start_time
    log_info(f"ImportUsd程序耗时: {elapsed_time:.4f} 秒")
    if import_status:
        log_info("File loaded successfully.")
    else:
        error_exit("USD_OPEN_FAILED", f"加载失败: {state.usd_file}")
    # 程序开始时间
    start_time = time.time()

    stage = omni.usd.get_context().get_stage()

    disable_runtime_graphs_for_static_export(stage)
    # 收集当前打开usd非reference直接定于的material
    collect_non_instance_material(state, stage)
    # 将所有引用usd文件改为直接导入, 可修改编辑
    cancel_all_instance(stage)
    # 修复悬空的材质绑定, 确保GeomSubset级别的材质绑定能被正确导出
    fix_dangling_material_bindings(stage)
    # 收集所有mesh和使用了UDIM纹理的material
    mesh_prims, contain_refmesh = collect_allUdimMat_allMeshes(state, stage)

    if len(state.udim_material_prims) == 0:
        log_info("The USD file does not contain materials that use UDIM textures. No operation is required. Just export directly...")
    else:
        # 遍历处理
        for mesh_prim in reversed(mesh_prims):
            processed = process_geosubset(state, stage, mesh_prim)
            if not processed:
                if contain_refmesh:
                    process_refmesh(state, stage, mesh_prim)
                else:
                    process_mesh(state, stage, mesh_prim)
        remove_all_udimMat(state, stage)  # mesh和material处理完成, 移除使用udim的材质, 防止导出时报错

    clear_empty_meshes(stage)
    cleanup_stage_for_gltf_export(state, stage, deactivate_invalid=False)
    # 展开 Material 接口模式: 将 Material inputs 上的值拷贝到 Shader inputs
    flatten_material_interface(stage)
    # 展开 Material 内的 NodeGraph, 使材质结构与导出器可正常处理的格式一致
    flatten_material_nodegraphs(stage)
    # 纹理文件名去重, 防止同 basename 不同源文件在导出时互相覆盖
    deduplicate_texture_filenames(state, stage)
    fix_texture_asset_paths(state, stage)

    # 修复 OmniPBR project_uvw=True 材质: glTF 导出器不支持三平面投影纹理坐标,
    # 需在导出前根据世界空间位置重新生成 UV, 恢复纹理平铺密度和方向
    fix_project_uvw_materials(stage)

    # 导出为gltf
    # standalone/headless 下不能只 ensure_future 后立刻让脚本结束；
    # 把 task 返回给入口，由 SimulationApp update loop 驱动到真正完成。
    export_task = asyncio.ensure_future(convert_USD_to_gltf(state, stage))
    return start_time, export_task


if __name__ == "__main__":
    result = convert_task()

    try:
        if result:
            start_time, export_task = result

            # Script Editor 模式下 GUI/Kit 自己一直在跑 frame；
            # standalone headless 模式下需要我们主动 update，直到异步导出完成。
            while not export_task.done() and simulation_app.is_running():
                simulation_app.update()

            # 如果 coroutine 内部抛异常，这里重新抛出来，避免"看起来成功但没有产物"。
            export_task.result()

            end_time = time.time()
            elapsed_time = end_time - start_time
            log_info(f"程序耗时: {elapsed_time:.4f} 秒")
    finally:
        # 你通过 omni.usd context 打开的 stage，在 Kit 退出前主动关闭。
        try:
            if omni.usd.get_context().get_stage() is not None:
                omni.usd.get_context().close_stage()
        except Exception as error:
            log_tag("cleanup", f"close stage warning: {error}")

        simulation_app.close()
