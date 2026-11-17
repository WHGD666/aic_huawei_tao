"""
collect_m2.py -- M2 跑完后，把 Open3DBench 产出的真实日志 / json / 报告收进 workspace，供解析器校正与 LogDirEvaluator 试跑。

做什么：
  1. 找 third_party/Open3DBench/OpenROAD-3D/flow/logs/nangate45_3D/bp_fe/<method>_<variant>/ 与 results/.../bp_fe/lol/
  2. 复制 *.log / *.json / *.rpt 到 workspace/tests/samples/real_bp_fe/（给测试用）
     并按 LogDirEvaluator 的布局放一份到 workspace/baseline/results/partition_placement/bp_fe-ng45/1001/
  3. 立刻用 openroad_parse 跑一遍，打印提取结果与每阶段耗时（从 runtime.log / 各 log 的时间戳）
  4. 列出所有出现过的 ORFS 指标 key，方便确认任务一/二的采样 key
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
ROOT = WS.parent
sys.path.insert(0, str(WS))

from harness.openroad_parse import (  # noqa: E402
    parse_congestion, parse_orfs_metrics, parse_repair_stats, parse_stage_status, parse_wns_tns, parse_timing_paths,
)

O3 = ROOT / "third_party" / "Open3DBench" / "OpenROAD-3D" / "flow"


def find_run_dirs():
    out = []
    for base in (O3 / "logs" / "nangate45_3D", O3 / "results" / "nangate45_3D"):
        if base.exists():
            for d in base.iterdir():
                if d.is_dir():
                    for sub in d.iterdir():
                        if sub.is_dir():
                            out.append(sub)
    return out


def main():
    dirs = find_run_dirs()
    if not dirs:
        print("no run dirs under", O3 / "logs", "- did 07_run_lol_bpfe.bat finish?")
        return 1
    dst_samples = WS / "tests" / "samples" / "real_bp_fe"
    dst_eval = WS / "baseline" / "results" / "partition_placement" / "bp_fe-ng45" / "1001"
    for d in (dst_samples, dst_eval):
        d.mkdir(parents=True, exist_ok=True)
    copied = []
    for d in dirs:
        for p in d.rglob("*"):
            if p.suffix in (".log", ".json", ".rpt", ".txt") and p.stat().st_size < 50_000_000:
                rel = f"{d.parent.name}__{d.name}__{p.name}"
                shutil.copy2(p, dst_samples / rel)
                shutil.copy2(p, dst_eval / p.name)
                copied.append(p)
    print(f"copied {len(copied)} files -> {dst_samples}")

    # ---- 耗时 ----
    rt = [p for p in copied if p.name == "runtime.log"]
    for p in rt:
        print("\n== runtime.log ==")
        print(p.read_text(encoding="utf-8", errors="ignore")[-800:])
    # 各阶段 log 的 elapsed（ORFS 的 TIME_CMD 会在 log 末尾写 "Elapsed time: ..."）
    print("\n== per-stage elapsed ==")
    for p in sorted(copied):
        if p.suffix == ".log":
            m = re.findall(r"Elapsed time:\s*([\d:.]+)\[h:\]min:sec", p.read_text(encoding="utf-8", errors="ignore"))
            if m:
                print(f"  {p.name:<32} {m[-1]}")

    # ---- 指标 key ----
    print("\n== ORFS metric keys (timing / route / design) ==")
    keys = set()
    for p in copied:
        if p.suffix == ".json":
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for k in d:
                if any(t in k for t in ("timing", "route", "design__instance", "power")):
                    keys.add(k)
            om = parse_orfs_metrics(p.read_text(encoding="utf-8"))
            if om:
                print(f"  {p.name:<28} stage={om.stage} ws={om.ws} wns={om.wns} tns={om.tns} unit={om.time_unit}")
    for k in sorted(keys):
        print("   ", k)

    # ---- 文本解析 ----
    print("\n== text parsers ==")
    for p in sorted(copied):
        if p.suffix == ".log":
            t = p.read_text(encoding="utf-8", errors="ignore")
            w, tn = parse_wns_tns(t)
            cg = parse_congestion(t)
            rs = parse_repair_stats(t)
            st = parse_stage_status(t)
            paths = parse_timing_paths(t)
            bits = []
            if w is not None or tn is not None:
                bits.append(f"wns/tns={w}/{tn}")
            if cg:
                bits.append(f"overflow={cg.total_overflow} layers={len(cg.per_layer)}")
            if rs.inserted_buffers or rs.resized:
                bits.append(f"repair: +{rs.inserted_buffers}buf {rs.resized}resized final_tns={rs.final_tns}")
            if paths:
                bits.append(f"{len(paths)} paths")
            if not st.ok:
                bits.append(f"STATUS: {st.fail_reason}")
            if bits:
                print(f"  {p.name:<32} " + " | ".join(bits))
    print("\nNext: compare with docs/open3dbench_notes.md §2, then run")
    print("  python -m harness.loop --task partition_placement --evaluator logdir --llm-mode replay --max-rounds 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
