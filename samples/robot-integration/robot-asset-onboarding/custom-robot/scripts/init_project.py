import os
import shutil
import sys

# ================= 配置区域 =================
# 默认机器人名称
DEFAULT_ROBOT_NAME = "myrobot"

# 源工程目录名称
SOURCE_DIR_NAME = "robot_ur5e"

# 需要完全排除（不复制）的路径列表
# 注意：这里只放真正不需要保留目录结构的文件夹
EXCLUDE_PATHS = [
    "Assets/robots/robot_ur5e/ur5e_description/config",
]

# 需要完全清空的目录列表 (相对于 SOURCE_DIR)
# 这些目录只保留空壳，删除下面所有子目录和文件
CLEAR_DIRS = [
    "Assets/UrdfImporter",
    "Assets/robots/robot_ur5e/ur5e_config",
    "Assets/robots/robot_ur5e/ur5e_description/meshes",
    "Assets/robots/robot_ur5e/ur5e_description/urdf",
]

# 需要清空文件内容的目录列表 (相对于 SOURCE_DIR)
# 这些目录保留文件结构，但文件内容变成空文件
EMPTY_CONTENT_DIRS = [
    "Assets/robots/robot_ur5e"
]

# 例外保留内容的文件列表 (相对于 SOURCE_DIR)
# 这些文件虽然在 EMPTY_CONTENT_DIRS 目录下，但需要保留原内容，
# 仅做文件名和文件内容中的 ur5e -> robot_name 替换
PRESERVE_CONTENT_FILES = [
    "Assets/robots/robot_ur5e/ur5e_description/package.xml",
    "Assets/robots/robot_ur5e/ur5e_description/CMakeLists.txt",
    "Assets/robots/robot_ur5e/ur5e_description/rviz/urdf.rviz",
    "Assets/robots/robot_ur5e/ur5e_description/launch/display.launch.py",
    "Assets/robots/robot_ur5e/ur5e_gripper_controller/package.xml",
    "Assets/robots/robot_ur5e/ur5e_gripper_controller/CMakeLists.txt",
    "Assets/robots/robot_ur5e/ur5e_transformer/pyproject.toml",
]

# 特殊文件处理：ur5e_gripper_controller.cpp
GRIPPER_CONTROLLER_FILENAME = ["ur5e_gripper_controller.cpp", "my_parallel_gripper_transformers.py"]
GRIPPER_CONTROLLER_CONTENT = "// define code\n"

# 需要替换文件内容的扩展名
TEXT_REPLACE_EXTENSIONS = ['.urdf', '.xacro', '.yaml', '.yml', '.json', '.txt', '.cfg', '.py', '.sh', '.xml']


# ============================================

def normalize_path(path):
    """统一路径分隔符为正斜杠，便于比较"""
    return path.replace(os.sep, '/')


def should_exclude(rel_path, exclude_paths):
    """检查相对路径是否在排除列表中"""
    rel_path_norm = normalize_path(rel_path)
    for exclude in exclude_paths:
        exclude_norm = normalize_path(exclude)
        # 如果相对路径等于排除路径，或者以排除路径开头，则排除
        if rel_path_norm == exclude_norm or rel_path_norm.startswith(exclude_norm + '/'):
            return True
    return False


def is_in_empty_dir(rel_path, empty_dirs):
    """
    检查文件相对路径是否在需要清空内容的目录中
    rel_path: 例如 "Assets/robots/robot_ur5e/some_file.urdf"
    empty_dirs: 例如 ["Assets/robots/robot_ur5e"]
    """
    rel_path_norm = normalize_path(rel_path)
    for empty_dir in empty_dirs:
        empty_dir_norm = normalize_path(empty_dir)
        # 如果文件路径以空目录路径开头，或者文件就在该目录下
        if rel_path_norm.startswith(empty_dir_norm + '/') or rel_path_norm == empty_dir_norm:
            return True
    return False


def is_in_preserve_list(rel_path, preserve_files):
    """
    检查文件相对路径是否在例外保留内容列表中
    """
    rel_path_norm = normalize_path(rel_path)
    for preserve in preserve_files:
        if rel_path_norm == normalize_path(preserve):
            return True
    return False


def is_in_clear_dir(rel_path, clear_dirs):
    """
    检查路径是否在需要完全清空的目录中（只保留空壳目录）
    """
    rel_path_norm = normalize_path(rel_path)
    for clear_dir in clear_dirs:
        clear_dir_norm = normalize_path(clear_dir)
        if rel_path_norm == clear_dir_norm or rel_path_norm.startswith(clear_dir_norm + '/'):
            return True
    return False


def replace_text_in_file(file_path, old_name, new_name):
    """替换文件内容中的字符串"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        if old_name in content:
            new_content = content.replace(old_name, new_name)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True
    except Exception as e:
        print(f"[警告] 无法读取或写入文件 {file_path}: {e}")
    return False


def create_new_project(source_dir, target_dir, robot_name):
    """
    创建新工程目录，复制文件，排除指定路径，并重命名
    """
    if not os.path.exists(source_dir):
        print(f"[错误] 源目录不存在: {source_dir}")
        sys.exit(1)

    if os.path.exists(target_dir):
        print(f"[警告] 目标目录已存在: {target_dir}，将被删除并重建")
        shutil.rmtree(target_dir)

    print(f"创建新工程目录: {target_dir}")
    os.makedirs(target_dir)

    # 遍历源目录
    # topdown=False 确保先处理子目录，再处理父目录，避免重命名冲突
    for dirpath, dirnames, filenames in os.walk(source_dir, topdown=False):
        # 计算当前目录相对于源目录的相对路径
        rel_dirpath = normalize_path(os.path.relpath(dirpath, source_dir))

        # 1. 检查当前目录是否完全排除
        if should_exclude(rel_dirpath, EXCLUDE_PATHS):
            # 清空 dirnames 以防止 os.walk 进入子目录
            dirnames[:] = []
            continue

        # 2. 检查当前目录或其父目录是否在 CLEAR_DIRS 中
        # 如果是 CLEAR_DIRS 中目录的子目录/文件，全部跳过
        skip_this_dir = False
        for clear_dir in CLEAR_DIRS:
            clear_dir_norm = normalize_path(clear_dir)
            # 如果当前目录是 CLEAR_DIR 的子目录（不包括 CLEAR_DIR 本身）
            if rel_dirpath.startswith(clear_dir_norm + '/'):
                skip_this_dir = True
                break
        
        if skip_this_dir:
            dirnames[:] = []
            continue

        # 3. 创建目标目录结构
        target_subdir = os.path.join(target_dir, rel_dirpath)
        if not os.path.exists(target_subdir):
            os.makedirs(target_subdir)

        # 4. 处理当前目录下的文件
        for filename in filenames:
            src_file = os.path.join(dirpath, filename)

            # 计算文件的相对路径 (用于判断是否清空)
            rel_file_path = normalize_path(os.path.join(rel_dirpath, filename))

            # 检查文件是否完全排除
            if should_exclude(rel_file_path, EXCLUDE_PATHS):
                continue

            # 检查文件是否在 CLEAR_DIRS 中（跳过不复制）
            skip_file = False
            for clear_dir in CLEAR_DIRS:
                clear_dir_norm = normalize_path(clear_dir)
                if rel_file_path.startswith(clear_dir_norm + '/'):
                    skip_file = True
                    break
            if skip_file:
                continue

            # 生成新的文件名 (替换 ur5e -> robot_name)
            new_filename = filename.replace("ur5e", robot_name)
            target_file = os.path.join(target_subdir, new_filename)

            # 判断是否需要清空内容
            is_empty_content = False

            # A. 检查是否在需要清空文件内容的目录中
            #    例外：PRESERVE_CONTENT_FILES 中的文件保留原内容，仅做 ur5e -> robot_name 替换
            if is_in_empty_dir(rel_file_path, EMPTY_CONTENT_DIRS):
                if not is_in_preserve_list(rel_file_path, PRESERVE_CONTENT_FILES):
                    is_empty_content = True

            # B. 检查是否是特定的需要清空内容的文件 (如 gripper controller)
            if filename in GRIPPER_CONTROLLER_FILENAME:
                is_empty_content = True

            if is_empty_content:
                if filename in GRIPPER_CONTROLLER_FILENAME:
                    # 特殊文件：写入占位内容
                    with open(target_file, 'w', encoding='utf-8') as f:
                        f.write(GRIPPER_CONTROLLER_CONTENT)
                    print(f"[占位] {rel_file_path} -> {os.path.join(rel_dirpath, new_filename)}")
                else:
                    # 创建空文件（保留文件名）
                    with open(target_file, 'w', encoding='utf-8') as f:
                        pass
                    print(f"[空文件] {rel_file_path} -> {os.path.join(rel_dirpath, new_filename)}")
            else:
                # 正常复制
                shutil.copy2(src_file, target_file)
                print(f"[复制] {rel_file_path} -> {os.path.join(rel_dirpath, new_filename)}")

                # 替换文件内容 (除了已清空的)
                if any(filename.endswith(ext) for ext in TEXT_REPLACE_EXTENSIONS):
                    if replace_text_in_file(target_file, "ur5e", robot_name):
                        print(f"  [内容替换] 已更新文件内容: {new_filename}")

        # 5. 处理子目录重命名
        new_dirnames = []
        for dirname in dirnames:
            rel_subdir_path = normalize_path(os.path.join(rel_dirpath, dirname))

            # 检查子目录是否完全排除
            if should_exclude(rel_subdir_path, EXCLUDE_PATHS):
                continue

            # 检查子目录是否在 CLEAR_DIRS 中（跳过子目录，但顶层目录需要重命名）
            skip_subdir = False
            for clear_dir in CLEAR_DIRS:
                clear_dir_norm = normalize_path(clear_dir)
                if rel_subdir_path.startswith(clear_dir_norm + '/'):
                    skip_subdir = True
                    break
            if skip_subdir:
                continue

            # 生成新的目录名
            new_dirname = dirname.replace("ur5e", robot_name)
            new_dirnames.append(new_dirname)

            # 检查子目录是否是 CLEAR_DIRS 中的顶层目录
            is_clear_top = rel_subdir_path in [normalize_path(d) for d in CLEAR_DIRS]

            # 重命名目标目录
            if new_dirname != dirname:
                old_target_subdir = os.path.join(target_dir, rel_dirpath, dirname)
                new_target_subdir = os.path.join(target_dir, rel_dirpath, new_dirname)
                if os.path.exists(old_target_subdir):
                    os.rename(old_target_subdir, new_target_subdir)
                    print(
                        f"[重命名目录] {os.path.join(rel_dirpath, dirname)} -> {os.path.join(rel_dirpath, new_dirname)}")
                elif is_clear_top:
                    # CLEAR_DIRS 中的顶层目录，创建空目录
                    parent_dir = os.path.join(target_dir, rel_dirpath)
                    if os.path.exists(parent_dir):
                        clear_target = os.path.join(parent_dir, new_dirname)
                        if not os.path.exists(clear_target):
                            os.makedirs(clear_target)
                            print(f"[创建空目录] {rel_subdir_path} -> {os.path.join(rel_dirpath, new_dirname)}")

        # 更新 dirnames 以反映重命名后的目录名，并移除被排除的目录
        dirnames[:] = new_dirnames


def main():
    # 1. 获取机器人名称
    if len(sys.argv) > 1:
        robot_name = sys.argv[1]
    else:
        robot_name = DEFAULT_ROBOT_NAME
        print(f"未指定机器人名称，使用默认值: {robot_name}")

    # 2. 动态定位路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    source_dir = os.path.join(project_root, SOURCE_DIR_NAME)
    target_dir = os.path.join(project_root, f"robot_{robot_name}")

    print(f"脚本目录: {script_dir}")
    print(f"项目根目录: {project_root}")
    print(f"源目录: {source_dir}")
    print(f"目标目录: {target_dir}")
    print(f"机器人名称: {robot_name}")
    print("-" * 50)

    if not os.path.exists(source_dir):
        print(f"[错误] 源目录不存在: {source_dir}")
        print(f"请确保 '{SOURCE_DIR_NAME}' 文件夹位于项目根目录: {project_root}")
        sys.exit(1)

    # 3. 执行创建
    create_new_project(source_dir, target_dir, robot_name)

    print("-" * 50)
    print("新工程创建完成！")


if __name__ == "__main__":
    main()