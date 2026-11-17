"""
report.py -- 把一次 harness 运行目录渲染成技术报告用的 Markdown：
  * 预算消耗与效率（评估次数 / 费用 / token / 时间）
  * 候选表（id、父代、阶段、状态、总分、稳健分、各算例分、假设）
  * 得分曲线（第 k 次评估时的最优分）
  * 血统树
  * 各算例 基线 vs 最优 的 WNS/TNS 表
用法:  python tools/report.py logs/run_partition_placement_YYYYMMDD_HHMMSS [-o report.md]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))


def load(run: Path):
    cands = []
    for mp in sorted((run / "candidates").glob("c*/meta.json")):
        cands.append(json.loads(mp.read_text(encoding="utf-8")))
    cands.sort(key=lambda m: m["created_at"])
    budget = json.loads((run / "budget.json").read_text(encoding="utf-8")) if (run / "budget.json").exists() else {}
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8")) if (run / "summary.json").exists() else {}
    base = json.loads((run / "candidates" / "baseline" / "eval.json").read_text(encoding="utf-8")) if (run / "candidates" / "baseline" / "eval.json").exists() else None
    tree = (run / "lineage_tree.txt").read_text(encoding="utf-8") if (run / "lineage_tree.txt").exists() else ""
    return cands, budget, summary, base, tree


def fmt(x, nd=3):
    return "—" if x is None else f"{x:+.{nd}f}"


def render(run: Path) -> str:
    cands, budget, summary, base, tree = load(run)
    out = [f"# Harness run report — `{run.name}`\n"]
    # ---- 预算 ----
    u = budget.get("usage", {})
    out.append("## 预算消耗\n")
    out.append("| 评估次数 | API 费用 (CNY) | LLM 调用 | prompt tok (cache hit) | completion tok | 候选总数 | 有效评估 | gate 拦截 | 无效 |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    out.append(f"| {budget.get('evaluations', 0)} | {budget.get('cost_cny', 0):.3f} | {budget.get('llm_calls', 0)} | "
               f"{u.get('prompt_tokens', 0)} ({u.get('cache_hit_tokens', 0)}) | {u.get('completion_tokens', 0)} | "
               f"{len(cands)} | {summary.get('n_evaluated', '—')} | {summary.get('n_gate_failed', '—')} | {summary.get('n_invalid', '—')} |\n")
    # ---- 得分曲线 ----
    out.append("## 演化效率：第 k 次评估后的最优稳健分 / 总分\n")
    out.append("| k | 候选 | 本轮总分 | 本轮稳健分 | 累计最优总分 | 累计最优稳健分 |")
    out.append("|---|---|---|---|---|---|")
    best_t = best_r = None
    k = 0
    for m in cands:
        if m["status"] not in ("evaluated", "invalid"):
            continue
        k += 1
        t, r = m.get("score_total"), m.get("score_robust")
        if m["status"] == "evaluated" and m.get("all_valid"):
            best_t = t if best_t is None else max(best_t, t)
            best_r = r if best_r is None else max(best_r, r)
        out.append(f"| {k} | {m['id']} | {fmt(t)} | {fmt(r)} | {fmt(best_t)} | {fmt(best_r)} |")
    out.append("")
    # ---- 候选表 ----
    cases = sorted({c for m in cands for c in (m.get("per_case") or {})})
    out.append("## 候选明细\n")
    out.append("| id | 父代 | 阶段 | 状态 | 总分 | 稳健分 | " + " | ".join(cases) + " | 改动文件 | 假设 |")
    out.append("|---|---|---|---|---|---|" + "---|" * len(cases) + "---|---|")
    for m in cands:
        pc = m.get("per_case") or {}
        row = [m["id"], m.get("parent") or "base", m["phase"], m["status"], fmt(m.get("score_total")), fmt(m.get("score_robust"))]
        row += [fmt(pc.get(c)) for c in cases]
        row += [", ".join(Path(f).name for f in m.get("files_changed", []))[:60], (m.get("hypothesis") or "")[:110].replace("|", "/")]
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    # ---- 基线 vs 最优 ----
    best = summary.get("best")
    if base and best:
        be = json.loads((run / "candidates" / best["id"] / "eval.json").read_text(encoding="utf-8"))
        out.append(f"## 基线 vs 最优候选 `{best['id']}`（每算例每种子）\n")
        out.append("| case/seed | base WNS | best WNS | ΔWNS% | base TNS | best TNS | ΔTNS% | overflow |")
        out.append("|---|---|---|---|---|---|---|---|")
        for key, b in sorted(base["runs"].items()):
            c = be["runs"].get(key, {})
            bw, bt = abs(min(b["wns"], 0)), abs(min(b["tns"], 0))
            cw, ct = abs(min(c.get("wns", 0), 0)), abs(min(c.get("tns", 0), 0))
            dw = (bw - cw) / bw * 100 if bw else 0
            dt = (bt - ct) / bt * 100 if bt else 0
            out.append(f"| {key} | {b['wns']:.3f} | {c.get('wns', float('nan')):.3f} | {dw:+.1f} | {b['tns']:.1f} | {c.get('tns', float('nan')):.1f} | {dt:+.1f} | {c.get('overflow', 0):.0f} |")
        out.append("")
    # ---- 失败原因统计 ----
    reasons = {}
    for m in cands:
        for r in m.get("fail_reasons", []):
            key = r.split(":", 1)[-1].strip()[:50]
            reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        out.append("## 失败原因分布\n")
        out.append("| 原因 | 次数 |\n|---|---|")
        for r, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            out.append(f"| {r} | {n} |")
        out.append("")
    # ---- 血统树 ----
    if tree:
        out.append("## 血统树\n")
        out.append("```\n" + tree + "\n```\n")
    # ---- 分析摘录 ----
    out.append("## Analyzer 摘录（最优链）\n")
    if best:
        chain, cur = [], best["id"]
        by_id = {m["id"]: m for m in cands}
        while cur:
            chain.append(cur)
            cur = by_id[cur].get("parent") if cur in by_id else None
        for cid in reversed(chain):
            m = by_id[cid]
            out.append(f"- **{cid}** ({m['phase']}, {fmt(m.get('score_total'))}): {m.get('hypothesis', '')}")
            if m.get("analysis"):
                out.append(f"  - {m['analysis']}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()
    run = Path(a.run_dir)
    if not run.is_absolute():
        run = WS / run
    md = render(run)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print("written", a.out)
    else:
        print(md)


if __name__ == "__main__":
    main()
