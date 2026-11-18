#!/usr/bin/env python3
"""
evaluate_open3dbench.py -- 候选评估适配器：把 harness 的候选工作目录变成 Open3DBench 真实流程运行，
输出 LogDirEvaluator 能读的目录（<out>/<case>/<seed>/{<stage>.json, *.log, ...}）。

这是 harness.yaml 里 `official_cmd` 的一个候选实现（官方容器/接口到手后可能被替换）：
    official_cmd: "python3 tools/evaluate_open3dbench.py --workdir {workdir} --out {out} --task partition_placement"

流程（每个 case × seed）：
  1. 把 third_party/Open3DBench 复制一份到临时目录（或用 --inplace 直接用；复制保证候选之间互不污染）
  2. 把候选 workdir（稀疏覆盖：只含被改的白名单文件）覆盖到副本的对应路径上
  3. 切分 + 布局（Place-LoL / DREAMPlace）：**待官方基线确定入口**；当前假设 Place-LoL 的 DREAMPlace 流程产出
     merged DEF 到 <o3>/Place-LoL/binaries/converted_output/default/<method>/<design>.def。--placer-cmd 可覆盖。
  4. OpenROAD-3D 后端：在原 Makefile 的配置环境中加载本地 open3dbench_eval.mk。
     两个任务都从 LoL 的 read_def + 分层合法化开始，再按题面截断到一次修复或 GRT。
     上游阶段默认不写 WNS/TNS；另做只读 STA 采样，输出 3_4_sampled.json 或 5_1_sampled.json。
  5. 把 LOG_DIR 下的 json/log 复制到 <out>/<case>/<seed>/，LogDirEvaluator 负责解析。

种子：题面每算例用 1001/1002/1003。多种子必须提供 --placer-cmd，逐次重建 DEF；
DREAMPlace 侧通过 params json 的 random_seed 注入（--seed-json-key）；
OpenROAD 侧的随机性由 ORFS 固定（无显式种子）。

本脚本能在没有容器的机器上 --dry-run：打印将要执行的命令并校验路径，不跑任何 EDA 工具。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional


HERE = Path(__file__).resolve().parent
WS = HERE.parent
ROOT = WS.parent
O3_DEFAULT = ROOT / "third_party" / "Open3DBench"
sys.path.insert(0, str(WS))

from harness.openroad_parse import parse_stage_status  # noqa: E402

# 算例名（题面）→ Open3DBench 目录名 / 昵称
CASE_MAP = {
    "bp_fe-ng45":        {"design": "bp_fe_top",     "nick": "bp_fe",         "platform": "nangate45_3D"},
    "bp-ng45":           {"design": "black_parrot",  "nick": "bp",            "platform": "nangate45_3D"},
    "ariane133-ng45":    {"design": "ariane133",     "nick": "ariane133",     "platform": "nangate45_3D"},
    "swerv_wrapper-ng45": {"design": "swerv_wrapper", "nick": "swerv_wrapper", "platform": "nangate45_3D"},
    "bp_fe-asap7":       {"design": "bp_fe_top",     "nick": "bp_fe",         "platform": "asap7_3D"},   # 平台名待官方确认
}


def log(msg: str) -> None:
    print(f"[eval {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def overlay(workdir: Path, o3: Path) -> List[str]:
    """把候选工作目录（相对 baseline/ 的路径）覆盖到 Open3DBench 副本。baseline/ 的目录结构与 Open3DBench 一致。"""
    copied = []
    for p in workdir.rglob("*"):
        if not p.is_file() or p.name == "candidate.patch":
            continue
        rel = p.relative_to(workdir)
        dst = o3 / rel
        if not (o3 / rel.parts[0]).exists():
            log(f"WARNING overlay target top-level '{rel.parts[0]}' not in Open3DBench; skipping {rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        copied.append(str(rel).replace("\\", "/"))
    return copied


def set_seed_in_json(path: Path, key: str, seed: int) -> None:
    d = json.loads(path.read_text(encoding="utf-8"))
    d[key] = seed
    path.write_text(json.dumps(d, indent=2), encoding="utf-8")


def run(cmd: List[str], cwd: Path, env: Dict[str, str], logfile: Path, dry: bool, timeout: int) -> int:
    log(("DRY " if dry else "") + " ".join(cmd) + f"   (cwd={cwd})")
    if dry:
        return 0
    with open(logfile, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(cmd)}\n")
        f.flush()
        try:
            r = subprocess.run(cmd, cwd=str(cwd), env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
            return r.returncode
        except subprocess.TimeoutExpired:
            f.write(f"\n[TIMEOUT after {timeout}s]\n")
            return 124


def run_placer(o3: Path, case: str, seed: int, method: str, variant: str, placer_cmd: Optional[str],
               env: Dict[str, str], logfile: Path, dry: bool, timeout: int,
               seed_json_key: str = "random_seed") -> Optional[Path]:
    """切分 + 布局 → merged DEF。返回 DEF 路径；None 表示失败。"""
    info = CASE_MAP[case]
    nick = info["nick"]
    params = o3 / "Place-LoL" / "test" / f"3D_input_{variant}" / f"{nick}.json"
    out_def = o3 / "Place-LoL" / "binaries" / "converted_output" / variant / method / f"{nick}.def"
    if placer_cmd:
        before = out_def.stat().st_mtime_ns if out_def.exists() and not dry else None
        original_params = params.read_bytes() if params.exists() and not dry else None
        try:
            if original_params is not None:
                set_seed_in_json(params, seed_json_key, seed)
            cmd = placer_cmd.format(o3=str(o3), case=case, design=info["design"], nick=nick, seed=seed, method=method,
                                    variant=variant, params=str(params), out_def=str(out_def))
            rc = run(["bash", "-lc", cmd], o3 / "Place-LoL", env, logfile, dry, timeout)
        finally:
            if original_params is not None:
                params.write_bytes(original_params)
        if rc != 0:
            log(f"placer failed rc={rc}")
            return None
        if not dry and (not out_def.exists() or (before is not None and out_def.stat().st_mtime_ns == before)):
            log(f"placer returned success without a fresh DEF: {out_def}")
            return None
    else:
        # 没有指定布局命令：假设 DEF 已存在（例如用仓库自带的 tcad25 结果做基线/冒烟）
        if not dry and not out_def.exists():
            log(f"no placer_cmd and DEF missing: {out_def}")
            return None
    return out_def


def run_backend(o3: Path, case: str, seed: int, task: str, method: str, variant: str, def_path: Optional[Path],
                env: Dict[str, str], logfile: Path, dry: bool, timeout: int, work_home: Path) -> Dict[str, str]:
    """在上游 Makefile 的设计环境中执行诊断后端，并检查被 tee 掩盖的错误。"""
    info = CASE_MAP[case]
    flow = o3 / "OpenROAD-3D" / "flow"
    cfg = f"designs/{info['platform']}/{info['design']}/config_lol.mk"
    helper = HERE / "open3dbench_eval.mk"
    e = dict(env)
    e.update({
        "WORK_HOME": str(work_home),
        "METHOD": method,
        "DEF_VARIANT": variant,
        "PLACE_LOL_ROOT": str(o3 / "Place-LoL"),
        "OPENROAD_EXE": e.get("OPENROAD_EXE") or shutil.which("openroad") or "openroad",
    })
    log_dir = work_home / "logs" / info["platform"] / info["nick"] / "lol"
    results_dir = work_home / "results" / info["platform"] / info["nick"] / "lol"
    reports_dir = work_home / "reports" / info["platform"] / info["nick"] / "lol"
    stage_json = "3_4b_task1_repaired.json" if e.get("TASK1_REPAIR_TIMING") == "1" else "3_4_sampled.json"
    target = "harness-task1-repair-timing" if e.get("TASK1_REPAIR_TIMING") == "1" else "harness-task1"
    if task == "full_flow":
        target, stage_json = "harness-task2", "5_1_sampled.json"
    cmd = ["make", "-f", "Makefile", "-f", str(helper), f"DESIGN_CONFIG={cfg}", "harness-prefix", target]
    rc = run(cmd, flow, e, logfile, dry, timeout)
    if not dry and rc == 0:
        for p in sorted(log_dir.glob("*.log")):
            status = parse_stage_status(p.read_text(encoding="utf-8", errors="ignore"))
            if not status.ok:
                log(f"{case}/{seed}: {p.name}: {status.fail_reason}")
                rc = 1
                break
    return {"rc": str(rc), "log_dir": str(log_dir), "results_dir": str(results_dir),
            "reports_dir": str(reports_dir), "stage_json": stage_json}


def collect(log_dir: Path, results_dir: Path, out_case_seed: Path, stage_json: str, rc: int, dry: bool,
            reports_dir: Optional[Path] = None) -> None:
    out_case_seed.mkdir(parents=True, exist_ok=True)
    if dry:
        (out_case_seed / "DRY_RUN").write_text("no flow executed\n")
        return
    report_files = list(reports_dir.glob("*.rpt")) if reports_dir else []
    for p in list(log_dir.glob("*.json")) + list(log_dir.glob("*.log")) + report_files:
        shutil.copy2(p, out_case_seed / p.name)
    # LogDirEvaluator 以 <metric_stage>.json 为首选：给采样点 json 一个固定别名
    src = log_dir / stage_json
    if src.exists():
        alias = "globalroute.json" if stage_json.startswith("5_1") else "placeopt.json"
        shutil.copy2(src, out_case_seed / alias)
    (out_case_seed / "flow.log").write_text(f"exit code {rc}\n" + ("" if rc == 0 else "[ERROR] flow failed\n"), encoding="utf-8")
    # 物理设计结果（提交要求：切分方案、上下层布局）
    for name in ("name_die_map.pkl", "upper_legalized.def", "bottom_legalized.def", "4_1_cts.def"):
        p = results_dir / name
        if p.exists():
            shutil.copy2(p, out_case_seed / name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True, help="候选工作目录（harness 传 {workdir}）")
    ap.add_argument("--out", required=True, help="输出目录（harness 传 {out}；布局 <out>/<case>/<seed>/）")
    ap.add_argument("--task", default="partition_placement", choices=["partition_placement", "full_flow"])
    ap.add_argument("--cases", default="bp_fe-ng45,bp-ng45,ariane133-ng45")
    ap.add_argument("--seeds", default="1001,1002,1003")
    ap.add_argument("--o3", default=str(O3_DEFAULT), help="Open3DBench 根目录")
    ap.add_argument("--inplace", action="store_true", help="直接在 --o3 上跑（不复制）。多候选并行时勿用")
    ap.add_argument("--method", default="harness", help="Place-LoL converted_output 下的方法名（输出 DEF 的子目录）")
    ap.add_argument("--variant", default="default", choices=["default", "inflated"])
    ap.add_argument("--placer-cmd", default=None,
                    help="切分+布局命令模板（在 Place-LoL 下以 bash -lc 执行；占位 {o3} {case} {design} {nick} {seed} {method} {variant} {params} {out_def}）。"
                         "不给则假设 DEF 已存在")
    ap.add_argument("--seed-json-key", default="random_seed", help="布局参数 JSON 中的随机种子字段")
    ap.add_argument("--timeout", type=int, default=3600, help="单次 run 的秒数上限")
    ap.add_argument("--task1-repair-timing", action="store_true",
                    help="任务一采样点改为 repair_design 之后再做一次 repair_timing -setup（官方口径待确认，见 docs/open3dbench_notes.md §2）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep", action="store_true", help="保留临时副本")
    a = ap.parse_args(argv)

    workdir, out, o3src = Path(a.workdir), Path(a.out), Path(a.o3)
    if not workdir.is_dir():
        print(f"candidate workdir not found at {workdir}", file=sys.stderr)
        return 2
    if not o3src.exists():
        print(f"Open3DBench not found at {o3src}", file=sys.stderr)
        return 2
    if a.inplace and not a.dry_run and any(p.is_file() and p.name != "candidate.patch" for p in workdir.rglob("*")):
        print("--inplace is only safe for an empty candidate; use the isolated copy for edited candidates", file=sys.stderr)
        return 2
    cases = [c.strip() for c in a.cases.split(",") if c.strip()]
    seeds = [int(s) for s in a.seeds.split(",") if s.strip()]
    if not a.dry_run and len(seeds) > 1 and not a.placer_cmd:
        print("multiple seeds require --placer-cmd; a prebuilt DEF would be reused for every seed", file=sys.stderr)
        return 2
    for c in cases:
        if c not in CASE_MAP:
            print(f"unknown case {c}; known: {list(CASE_MAP)}", file=sys.stderr)
            return 2

    tmp = None
    if a.inplace:
        o3 = o3src
    else:
        tmp = Path(tempfile.mkdtemp(prefix="o3eval_"))
        o3 = tmp / "Open3DBench"
        log(f"copying Open3DBench -> {o3} (excluding benchmarks/binaries which are symlinked)")
        if not a.dry_run:
            shutil.copytree(o3src, o3, symlinks=True,
                            ignore=shutil.ignore_patterns("benchmarks", "binaries", "evaluation_pack", "logs", "results", "objects", ".git"))
            for big in ("Place-LoL/benchmarks", "Place-LoL/binaries", "OpenROAD-3D/flow/evaluation_pack"):
                s, d = o3src / big, o3 / big
                if s.exists() and not d.exists():
                    os.symlink(s, d)
    copied = overlay(workdir, o3) if (workdir.exists() and not a.dry_run) else [str(p.relative_to(workdir)) for p in workdir.rglob("*") if p.is_file()]
    log(f"overlayed {len(copied)} candidate files: {copied[:6]}{' ...' if len(copied) > 6 else ''}")

    env = dict(os.environ)
    if a.task1_repair_timing:
        env["TASK1_REPAIR_TIMING"] = "1"
    summary = {"task": a.task, "cases": {}, "started": time.time()}
    any_failed = False
    for case in cases:
        for seed in seeds:
            key = f"{case}/{seed}"
            ocs = out / case / str(seed)
            ocs.mkdir(parents=True, exist_ok=True)
            logfile = ocs / "eval_driver.log"
            work_root = (tmp or o3src) / "work" / case / str(seed)
            work_root.mkdir(parents=True, exist_ok=True)
            work_home = Path(tempfile.mkdtemp(prefix="run_", dir=work_root))
            t0 = time.time()
            log(f"=== {key} ===")
            def_path = run_placer(o3, case, seed, a.method, a.variant, a.placer_cmd, env, logfile, a.dry_run,
                                  a.timeout, a.seed_json_key)
            if def_path is None and not a.dry_run:
                collect(Path("/nonexistent"), Path("/nonexistent"), ocs, "", 1, False)
                (ocs / "flow.log").write_text("[ERROR] placer stage failed\n", encoding="utf-8")
                summary["cases"][key] = {"rc": 1, "stage": "placer", "wall_s": time.time() - t0}
                any_failed = True
                continue
            r = run_backend(o3, case, seed, a.task, a.method, a.variant, def_path, env, logfile, a.dry_run, a.timeout, work_home)
            collect(Path(r["log_dir"]), Path(r["results_dir"]), ocs, r["stage_json"], int(r["rc"]), a.dry_run,
                    Path(r["reports_dir"]))
            summary["cases"][key] = {"rc": int(r["rc"]), "wall_s": time.time() - t0, "log_dir": r["log_dir"]}
            any_failed |= int(r["rc"]) != 0
            log(f"{key} done rc={r['rc']} in {time.time() - t0:.0f}s")
    summary["finished"] = time.time()
    (out / "eval_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if tmp and not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
