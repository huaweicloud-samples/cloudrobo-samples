"""固定种子评测驱动：把基础包的固定输入 wrapper 接到已运行的 RoboTwin 桥接层。"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import shutil
import sys
import time
import types
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

WRAPPER_NAME = "eval_policy_xpolicylab.py"
PROFILE_ARGS = {
    "clean_seen": ("demo_clean", "seen"),
    "random_unseen": ("demo_randomized", "unseen"),
}
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5695


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _env_file() -> Path:
    return Path(__file__).resolve().parent / "fixed_eval.env"


def _resolve_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    env = os.environ.get("ROBOTWIN_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    cfg = _read_env_file(_env_file())
    if cfg.get("ROBOTWIN_ROOT"):
        return Path(cfg["ROBOTWIN_ROOT"]).expanduser().resolve()
    raise SystemExit("缺少 ROBOTWIN_ROOT：请用 --root 指定，或在 bridge_seed_eval/fixed_eval.env 中设置")


def _resolve_output(value: str | None, root: Path) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    env = os.environ.get("OUT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    cfg = _read_env_file(_env_file())
    if cfg.get("OUT_ROOT"):
        return Path(cfg["OUT_ROOT"]).expanduser().resolve()
    return (root / "fixed_out").resolve()


def _load_task_names(cache: Path) -> list[str]:
    manifest = cache / "evaluation_10000.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"缺少缓存清单 evaluation_10000.json，完整路径: {manifest}")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    tasks = value.get("task_names") or []
    if len(tasks) != 50 or len(set(tasks)) != 50:
        raise ValueError("缓存清单 evaluation_10000.json 的 task_names 必须是 50 个唯一任务")
    return tasks


def _install_test_render_stub() -> None:
    module = types.ModuleType("test_render")
    module.Sapien_TEST = lambda *args, **kwargs: None
    sys.modules["test_render"] = module


def _load_wrapper(eval_dir: Path):
    wrapper_py = eval_dir / WRAPPER_NAME
    if not wrapper_py.is_file():
        raise FileNotFoundError(f"缺少固定评测脚本 eval_policy_xpolicylab.py，完整路径: {wrapper_py}")
    spec = importlib.util.spec_from_file_location("_fixed_eval_wrapper", str(wrapper_py))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载固定评测脚本，完整路径: {wrapper_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backup_path(task_output: Path, task: str) -> Path:
    backup = task_output.parent / (task + ".old")
    index = 1
    while backup.exists():
        backup = task_output.parent / f"{task}.old.{index}"
        index += 1
    return backup


def _prepare_task_dir(task_output: Path, task: str, resume_from: int) -> None:
    if resume_from:
        if task_output.exists():
            shutil.move(str(task_output), str(_backup_path(task_output, task)))
    elif task_output.exists():
        shutil.rmtree(task_output)
    task_output.mkdir(parents=True, exist_ok=True)


def _run_one_task(job: dict) -> dict:
    profile = job["profile"]
    task = job["task"]
    root = Path(job["root"])
    cache = Path(job["cache"])
    eval_dir = Path(job["eval_dir"])
    output = Path(job["output"])
    task_config, instruction_type = PROFILE_ARGS[profile]
    task_output = output / profile / task
    single_case = job["single_case"]
    cases = job["cases"]
    resume_from = job["resume_from"] or 0
    prev_success = job["prev_success"] or 0
    selecting = single_case is not None or bool(cases)
    result_file = task_output / "FIXED_RESULT.json"
    old_env = dict(os.environ)
    old_argv = list(sys.argv)
    old_cwd = os.getcwd()
    old_sys_path = list(sys.path)
    started = time.time()
    error = None
    try:
        if selecting and result_file.is_file():
            try:
                trials = json.loads(result_file.read_text(encoding="utf-8")).get("trials")
            except Exception:
                trials = None
            if trials == 100:
                raise RuntimeError(
                    f"{task_output} 已含正式 trials=100 结果，拒绝被单 case/批量 case 覆盖；请换独立 --output"
                )
        _prepare_task_dir(task_output, task, resume_from)
        env = dict(old_env)
        env.update(
            {
                "CLOUDROBO_ROBOTWIN_ROOT": str(root),
                "CLOUDROBO_FIXED_CACHE_ROOT": str(cache),
                "CLOUDROBO_FIXED_OUTPUT": str(task_output),
                "CLOUDROBO_FIXED_RESUME_FROM": str(resume_from),
                "CLOUDROBO_FIXED_PREV_SUCCESS": str(prev_success),
                "CLOUDROBO_FIXED_SINGLE_CASE": str(single_case if single_case is not None else -1),
                "CLOUDROBO_FIXED_CASES": cases or "-",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        os.environ.clear()
        os.environ.update(env)
        sys.argv = [
            "_fixed_eval_wrapper",
            "--task_name", task,
            "--task_config", task_config,
            "--env_cfg_type", "arx_x5",
            "--policy_name", "bridge",
            "--instruction_type", instruction_type,
            "--host", job["host"],
            "--port", str(job["port"]),
            "--protocol", "ws",
            "--seed", "0",
            "--test_num", "100",
            "--expert_check", "false",
            "--root_dir", str(root),
        ]
        os.chdir(root)
        if str(eval_dir) not in sys.path:
            sys.path.insert(0, str(eval_dir))
        _install_test_render_stub()
        wrapper = _load_wrapper(eval_dir)
        wrapper.main()
    except BaseException as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_sys_path
        os.environ.clear()
        os.environ.update(old_env)
        sys.argv = old_argv
    result = {
        "profile": profile,
        "task": task,
        "ok": error is None,
        "error": error,
        "wall_sec": round(time.time() - started, 1),
        "result_file": str(result_file),
    }
    if error is None:
        if result_file.is_file():
            detail = json.loads(result_file.read_text(encoding="utf-8"))
            result["trials"] = detail.get("trials")
            result["successes"] = detail.get("successes")
        else:
            result["ok"] = False
            result["error"] = "wrapper 正常返回但未生成 FIXED_RESULT.json"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="固定种子评测驱动（RoboTwin + 桥接层）")
    parser.add_argument("--profile", choices=tuple(PROFILE_ARGS), default="clean_seen")
    parser.add_argument("--task", default="all", help="任务名、逗号分隔列表或 all")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--root", help="RoboTwin 根目录")
    parser.add_argument("--cache", help="固定输入缓存目录")
    parser.add_argument("--eval-dir", help="基础包目录（含 wrapper 与 fixed_runtime）")
    parser.add_argument("--output", help="输出根目录")
    parser.add_argument("--single-case", type=int, default=None)
    parser.add_argument("--cases", default=None, help="逗号分隔的 case 序号列表")
    parser.add_argument("--resume-from", type=int, default=0)
    parser.add_argument("--prev-success", type=int, default=0)
    parser.add_argument("--parallel", type=int, default=1)
    args = parser.parse_args()
    if args.single_case is not None and args.cases:
        parser.error("--single-case 与 --cases 不能同时使用")
    if args.single_case is not None and not 0 <= args.single_case < 100:
        parser.error("--single-case 必须在 0..99")
    if not 0 <= args.resume_from <= 100:
        parser.error("--resume-from 必须在 0..100")
    if args.parallel < 1:
        parser.error("--parallel 必须 >= 1")
    root = _resolve_root(args.root)
    cache = (
        Path(args.cache).expanduser().resolve()
        if args.cache
        else (Path(__file__).resolve().parent.parent / "robotwin_cache").resolve()
    )
    eval_dir = (
        Path(args.eval_dir).expanduser().resolve()
        if args.eval_dir
        else (root / "XPolicyLab" / "policy" / "CloudRobo_WM" / "evaluation").resolve()
    )
    output = _resolve_output(args.output, root)
    all_tasks = _load_task_names(cache)
    if args.task.strip().lower() == "all":
        tasks = list(all_tasks)
    else:
        tasks = [item.strip() for item in args.task.split(",") if item.strip()]
        if not tasks:
            parser.error("--task 不能为空")
        unknown = [item for item in tasks if item not in all_tasks]
        if unknown:
            parser.error("未知任务: " + ", ".join(unknown))
    output.mkdir(parents=True, exist_ok=True)
    jobs = [
        {
            "profile": args.profile,
            "task": task,
            "host": args.host,
            "port": args.port,
            "root": str(root),
            "cache": str(cache),
            "eval_dir": str(eval_dir),
            "output": str(output),
            "single_case": args.single_case,
            "cases": args.cases,
            "resume_from": args.resume_from,
            "prev_success": args.prev_success,
        }
        for task in tasks
    ]
    print(
        f"[driver] profile={args.profile} tasks={len(tasks)} host={args.host} "
        f"port={args.port} parallel={args.parallel}"
    )
    started = time.time()
    if args.parallel > 1:
        with ProcessPoolExecutor(max_workers=args.parallel) as pool:
            results = list(pool.map(_run_one_task, jobs))
    else:
        results = [_run_one_task(job) for job in jobs]
    wall = round(time.time() - started, 1)
    rows = []
    for result in results:
        row = dict(result)
        result_file = Path(row["result_file"])
        if row.get("ok") and result_file.is_file():
            detail = json.loads(result_file.read_text(encoding="utf-8"))
            row["trials"] = detail.get("trials")
            row["successes"] = detail.get("successes")
        rows.append(row)
    summary = {"profile": args.profile, "wall_sec": wall, "tasks": len(rows), "rows": rows}
    (output / f"FIXED_SUMMARY_{args.profile}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    csv_path = output / f"fixed_seed_results_{args.profile}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["profile", "task", "ok", "trials", "successes", "wall_sec", "error"])
        for row in rows:
            writer.writerow(
                [
                    row["profile"],
                    row["task"],
                    row["ok"],
                    row.get("trials", ""),
                    row.get("successes", ""),
                    row["wall_sec"],
                    row.get("error", ""),
                ]
            )
    for row in rows:
        if row.get("successes") is not None:
            print(f"[done] {row['task']:<24} {row['successes']}/{row['trials']} wall={row['wall_sec']}s")
        else:
            print(f"[FAIL] {row['task']:<24} {row.get('error', '无结果')}")
    total_success = sum(row.get("successes") or 0 for row in rows)
    total_trials = sum(row.get("trials") or 0 for row in rows)
    percent = (total_success * 100 / total_trials) if total_trials else 0.0
    print(
        f"[summary] profile={args.profile} 完成={sum(1 for row in rows if row['ok'])}/{len(rows)} "
        f"成功={total_success}/{total_trials} = {percent:.1f}% 墙钟={wall:.0f}s"
    )
    print(f"[output] {output}")
    return 0 if all(row["ok"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
