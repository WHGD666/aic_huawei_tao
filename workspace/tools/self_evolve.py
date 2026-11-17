"""
self_evolve.py -- harness 自演化原型（提示词层）。题面 §五(二)："Harness 可由智能体修改 harness 自身的代码、提示模板和执行逻辑"。

外层循环：
  让基座模型修改 harness/prompts/*.md（白名单只含提示词，不碰 harness 代码），
  每个提示词版本用内层 harness（真实 LLM × mock 评估器，短预算）跑一次，
  以**行为指标**为元目标（有效评估数 ↑、门禁失败率 ↓、探针覆盖文件数 ↑、费用 ↓；mock 分数只做次要参考），
  保留改进版本，记录提示词版本血统（答辩"Harness 设计与方法创新"素材）。

限制（刻意）：
  * 只演化提示词：代码层自演化风险高（可能悄悄绕过门禁），且题面只要求提交最终版本
  * 元目标用行为指标而非 mock 分数：mock 分数无物理意义，用它当目标会把提示词调成"迎合 mock 关键字"
  * 每个版本的内层运行固定种子，可比

用法：
    python tools/self_evolve.py --generations 4 --inner-evals 6 --out logs/self_evolve
每代 ≈ 1 次外层 LLM 调用 + 内层 (inner-evals×2) 次调用 ≈ 0.03–0.05 元。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from harness import loop as L  # noqa: E402
from harness.llm import LLMClient  # noqa: E402
from harness.proposer import parse_proposal  # noqa: E402

PROMPTS = WS / "harness" / "prompts"
EDITABLE = ["system.md", "propose.md", "fix.md", "phase_probe.md", "phase_structural.md", "phase_combine.md", "phase_final.md", "knowledge.md"]

META_SYSTEM = """You are improving the PROMPT TEMPLATES of an LLM-driven code-evolution harness for 3D-IC physical design.
You may ONLY edit the markdown prompt files shown below (never code). Keep every placeholder token like {TASK}, {OUTLINE}, {KNOWLEDGE}, {PHASE}, {PHASE_GUIDE}, {PROFILE}, {HISTORY}, {PARENT_PATCH}, {CODE}, {FEEDBACK} intact.
Goal: make the inner model (deepseek-flash) produce proposals that (1) pass the local gate on the first try more often (SEARCH blocks copied verbatim from shown code; one coherent change; valid syntax), (2) spread probes across different code regions, (3) build on parents correctly, (4) stay cheap.
Make ONE focused change per generation and explain why. Output protocol:
HYPOTHESIS: <what you change in the prompts and why it should improve the metrics>
EXPECTED: <which metric moves>
KEYWORDS: prompts
<<<EDIT file=<filename.md>
<<<SEARCH
<exact lines from the shown file>
===
<replacement>
>>>
>>>
"""


def metrics_of(run_dir: Path) -> Dict[str, float]:
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in (run_dir / "candidates").glob("c*/meta.json")]
    n = len(metas)
    gate = sum(1 for m in metas if m["status"] == "gate_failed")
    valid = sum(1 for m in metas if m["status"] == "evaluated")
    evals = sum(1 for m in metas if m["status"] in ("evaluated", "invalid"))
    probe_files = {f for m in metas if m["phase"] == "probe" for f in m["files_changed"]}
    fixes = sum(1 for m in metas if (run_dir / "candidates" / m["id"] / "llm" / "fix.json").exists())
    b = json.loads((run_dir / "budget.json").read_text(encoding="utf-8"))
    best = max([m["score_robust"] for m in metas if m.get("score_robust") is not None] or [float("nan")])
    return {"rounds": n, "gate_rate": gate / max(n, 1), "valid": valid, "evals": evals, "probe_files": len(probe_files),
            "fix_rounds": fixes, "cost": b["cost_cny"], "best_robust": best}


def meta_score(m: Dict[str, float], target_evals: int) -> float:
    """行为元目标：有效评估占比 + 探针覆盖 − 门禁失败率 − 修复轮占比 − 费用（归一化）。mock 分数不进目标。"""
    return (m["valid"] / max(target_evals, 1)) * 1.0 + min(m["probe_files"], 4) / 4 * 0.5 - m["gate_rate"] * 1.0 - (m["fix_rounds"] / max(m["rounds"], 1)) * 0.5 - m["cost"] * 2.0


def run_inner(prompt_dir: Path, task: str, evals: int, seed: int, out: Path, llm_mode: str) -> Path:
    """把 prompt_dir 临时换成 harness/prompts，跑一次内层 harness。"""
    backup = PROMPTS.parent / "prompts_backup_selfevolve"
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(PROMPTS, backup)
    try:
        for f in prompt_dir.glob("*.md"):
            shutil.copy2(f, PROMPTS / f.name)
        cfg = L.load_config(WS / "config" / "harness.yaml")
        cfg["task"] = task
        cfg["gate"]["whitelist_file"] = f"config/whitelist_{task}.yaml"
        cfg["budget"].update(max_evaluations=evals, reserve_evaluations=0, max_cost_cny=2.0)
        probe = max(2, evals // 3)
        cfg["search"]["phase_plan"] = {"probe": probe, "structural": max(1, evals - probe - 1), "combine": 1}
        cfg["evaluator"]["mode"] = "mock"
        cfg["seed"] = seed
        h = L.Harness(cfg, llm_mode=llm_mode, run_dir=out)
        h.evaluator.sporadic_rate = 0.0
        h.evaluator.fail_rate = 0.15
        h.run(max_rounds=3 * evals)
        return out
    finally:
        for f in backup.glob("*.md"):
            shutil.copy2(f, PROMPTS / f.name)
        shutil.rmtree(backup)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="partition_placement")
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--inner-evals", type=int, default=6)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(WS / "logs" / "self_evolve"))
    ap.add_argument("--llm-mode", default="live", choices=["live", "record", "replay"])
    ap.add_argument("--apply-best", action="store_true", help="结束后把最优提示词版本写回 harness/prompts/（否则只留在 out/）")
    a = ap.parse_args(argv)
    L.load_dotenv(WS / ".env")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    meta_llm = LLMClient(thinking=False)
    lineage: List[dict] = []

    # gen 0 = 当前提示词
    cur = out / "v0"; cur.mkdir(exist_ok=True)
    for f in EDITABLE:
        if (PROMPTS / f).exists():
            shutil.copy2(PROMPTS / f, cur / f)
    run_inner(cur, a.task, a.inner_evals, a.seed, out / "run_v0", a.llm_mode)
    m0 = metrics_of(out / "run_v0"); s0 = meta_score(m0, a.inner_evals)
    best_v, best_s, best_m = "v0", s0, m0
    lineage.append({"version": "v0", "parent": None, "hypothesis": "(original prompts)", "metrics": m0, "meta_score": s0, "kept": True})
    print(json.dumps(lineage[-1], ensure_ascii=False)); sys.stdout.flush()

    for g in range(1, a.generations + 1):
        parent_dir = out / best_v
        shown = "\n\n".join(f"### {f}\n```\n{(parent_dir / f).read_text(encoding='utf-8')}\n```" for f in EDITABLE if (parent_dir / f).exists())
        hist = "\n".join(f"- {l['version']} (parent={l['parent']}): meta_score={l['meta_score']:+.3f} gate_rate={l['metrics']['gate_rate']:.2f} valid={l['metrics']['valid']} probe_files={l['metrics']['probe_files']} fix={l['metrics']['fix_rounds']} cost={l['metrics']['cost']:.3f} {'KEPT' if l['kept'] else 'rejected'}: {l['hypothesis'][:100]}" for l in lineage)
        user = f"## Inner-run metrics of the current best prompts ({best_v})\n{json.dumps(best_m)}\n\n## History\n{hist}\n\n## Current prompt files\n{shown}\n\nPropose ONE change now."
        text, u = meta_llm.chat([{"role": "system", "content": META_SYSTEM}, {"role": "user", "content": user}], temperature=0.7, max_tokens=2500, tag=f"selfevolve_g{g}")
        prop = parse_proposal(text)
        new_v = f"v{g}"; new_dir = out / new_v
        if new_dir.exists():
            shutil.rmtree(new_dir)
        shutil.copytree(parent_dir, new_dir)
        (new_dir / "_meta_response.txt").write_text(text, encoding="utf-8")
        ok = not prop.parse_errors
        applied = []
        if ok:
            for e in prop.edits:
                fn = Path(e.file).name
                if fn not in EDITABLE or not (new_dir / fn).exists():
                    ok = False; break
                src = (new_dir / fn).read_text(encoding="utf-8")
                if src.count(e.search) != 1:
                    ok = False; break
                new = src.replace(e.search, e.replace, 1)
                # 占位符必须保留
                for ph in re.findall(r"\{[A-Z_]+\}", src):
                    if ph not in new:
                        ok = False; break
                if not ok:
                    break
                (new_dir / fn).write_text(new, encoding="utf-8")
                applied.append(fn)
        if not ok:
            lineage.append({"version": new_v, "parent": best_v, "hypothesis": prop.hypothesis or "(unparsed)", "metrics": None, "meta_score": None, "kept": False, "reason": "edit did not apply / placeholder lost"})
            print(json.dumps(lineage[-1], ensure_ascii=False)); sys.stdout.flush()
            continue
        run_inner(new_dir, a.task, a.inner_evals, a.seed, out / f"run_{new_v}", a.llm_mode)
        m = metrics_of(out / f"run_{new_v}"); s = meta_score(m, a.inner_evals)
        kept = s > best_s
        lineage.append({"version": new_v, "parent": best_v, "hypothesis": prop.hypothesis, "files": applied, "metrics": m, "meta_score": s, "kept": kept})
        print(json.dumps(lineage[-1], ensure_ascii=False)); sys.stdout.flush()
        if kept:
            best_v, best_s, best_m = new_v, s, m
        (out / "lineage.json").write_text(json.dumps(lineage, indent=1, ensure_ascii=False), encoding="utf-8")

    (out / "lineage.json").write_text(json.dumps(lineage, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nbest prompts version: {best_v} meta_score={best_s:+.3f} metrics={best_m}")
    if a.apply_best and best_v != "v0":
        for f in (out / best_v).glob("*.md"):
            shutil.copy2(f, PROMPTS / f.name)
        print(f"applied {best_v} to harness/prompts/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
