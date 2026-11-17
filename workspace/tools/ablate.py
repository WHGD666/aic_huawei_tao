"""
ablate.py -- 部件消融：对每个开关"关掉 vs 全开"各跑一次短预算 harness（真实 LLM × mock 评估器），
输出门禁失败率 / 有效评估数 / 最优稳健分 / 费用 的对比表（技术报告 §3.4 素材）。

mock 评估器的分数无物理意义，所以这里只看 **harness 行为指标**（门禁失败率、有效评估数、探针覆盖面、费用），
"最优稳健分"仅作参考。真实消融（分数维度）要等官方评估接口。

用法：
    python tools/ablate.py --task partition_placement --evals 8 --arms precheck,boost_parent_lines,probe_rotation,analysis_in_history,problem_profile,knowledge_block,fix_round
    python tools/ablate.py --arms all --repeats 2 --out logs/ablation
每个 arm ≈ (evals+few) 次 LLM 调用 ≈ 0.03–0.06 元。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from harness import loop as L  # noqa: E402

ARMS = ["precheck", "boost_parent_lines", "probe_rotation", "analysis_in_history", "problem_profile", "knowledge_block", "fix_round"]


def run_arm(name: str, off: list, task: str, evals: int, seed: int, out: Path, llm_mode: str) -> dict:
    cfg = L.load_config(WS / "config" / "harness.yaml")
    cfg["task"] = task
    cfg["gate"]["whitelist_file"] = f"config/whitelist_{task}.yaml"
    cfg["budget"].update(max_evaluations=evals, reserve_evaluations=1 if evals >= 6 else 0, max_cost_cny=2.0)
    probe = max(2, evals // 3)
    cfg["search"]["phase_plan"] = {"probe": probe, "structural": max(1, evals - probe - 2 - cfg["budget"]["reserve_evaluations"]), "combine": 2}
    cfg["evaluator"]["mode"] = "mock"
    cfg["seed"] = seed
    cfg.setdefault("ablation", {})
    for k in off:
        cfg["ablation"][k] = False
    run_dir = out / f"{name}_s{seed}"
    t0 = time.time()
    h = L.Harness(cfg, llm_mode=llm_mode, run_dir=run_dir)
    h.evaluator.sporadic_rate = 0.0
    h.evaluator.fail_rate = 0.15
    best = h.run(max_rounds=3 * evals)
    metas = h.store.all()
    n = len(metas)
    gate = sum(1 for m in metas if m.status == "gate_failed")
    ev = sum(1 for m in metas if m.status in ("evaluated", "invalid"))
    valid = sum(1 for m in metas if m.status == "evaluated")
    fixes = sum(1 for m in metas if (h.store.dir(m.id) / "llm" / "fix.json").exists())
    files = {f for m in metas for f in m.files_changed}
    probe_files = {f for m in metas if m.phase == "probe" for f in m.files_changed}
    return {
        "arm": name, "off": off, "seed": seed, "rounds": n, "gate_failed": gate, "gate_rate": round(gate / max(n, 1), 3),
        "evaluations": ev, "valid": valid, "fix_rounds": fixes,
        "best_robust": (best.score_robust if best else None), "best_total": (best.score_total if best else None),
        "distinct_files": len(files), "probe_distinct_files": len(probe_files),
        "cost_cny": round(h.budget.state.cost_cny, 4), "llm_calls": h.budget.state.llm_calls,
        "wall_s": round(time.time() - t0, 1), "run_dir": str(run_dir),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="partition_placement")
    ap.add_argument("--evals", type=int, default=8)
    ap.add_argument("--arms", default="all", help="逗号分隔的开关名，或 all")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--out", default=str(WS / "logs" / "ablation"))
    ap.add_argument("--llm-mode", default="live", choices=["live", "record", "replay"])
    ap.add_argument("--skip-full", action="store_true", help="不跑全开基线（已有结果时）")
    a = ap.parse_args(argv)
    L.load_dotenv(WS / ".env")
    arms = ARMS if a.arms == "all" else [x.strip() for x in a.arms.split(",") if x.strip()]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in range(a.repeats):
        seed = a.seed0 + r
        if not a.skip_full:
            rows.append(run_arm("full", [], a.task, a.evals, seed, out, a.llm_mode))
            print(json.dumps(rows[-1], ensure_ascii=False)); sys.stdout.flush()
        for arm in arms:
            rows.append(run_arm(f"no_{arm}", [arm], a.task, a.evals, seed, out, a.llm_mode))
            print(json.dumps(rows[-1], ensure_ascii=False)); sys.stdout.flush()
    (out / "ablation_results.jsonl").open("a", encoding="utf-8").write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    # 表
    md = ["| arm | seed | rounds | gate_failed | gate_rate | evals | valid | fix_rounds | probe_files | best_robust | cost |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        br = "—" if r["best_robust"] is None else f"{r['best_robust']:+.3f}"
        md.append(f"| {r['arm']} | {r['seed']} | {r['rounds']} | {r['gate_failed']} | {r['gate_rate']} | {r['evaluations']} | {r['valid']} | {r['fix_rounds']} | {r['probe_distinct_files']} | {br} | {r['cost_cny']} |")
    (out / "ablation_table.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
