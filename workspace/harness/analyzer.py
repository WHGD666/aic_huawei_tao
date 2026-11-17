"""
analyzer.py -- 评估结果 -> 结构化摘要。先用规则提取（免费），再可选调 LLM 做 ≤300 token 的因果解读。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .evaluator import EvalResult
from .scoring import CandidateScore, RunMetrics


def rule_digest(base: EvalResult, cand: EvalResult, score: CandidateScore) -> str:
    lines: List[str] = []
    lines.append(f"total={score.total:+.3f} robust(mean-std)={score.robust_mean_minus_std:+.3f} min_case={score.robust_min:+.3f} all_valid={score.all_valid}")
    for case, s in sorted(score.per_case.items()):
        # 该算例三种子的 WNS/TNS 相对变化
        bw = [base.runs[k].wns_viol for k in base.runs if base.runs[k].case == case]
        cw = [cand.runs[k].wns_viol for k in cand.runs if cand.runs[k].case == case and cand.runs[k].valid]
        bt = [base.runs[k].tns_viol for k in base.runs if base.runs[k].case == case]
        ct = [cand.runs[k].tns_viol for k in cand.runs if cand.runs[k].case == case and cand.runs[k].valid]
        if cw and ct:
            dw = (sum(bw) / len(bw) - sum(cw) / len(cw)) / max(sum(bw) / len(bw), 1e-9)
            dt = (sum(bt) / len(bt) - sum(ct) / len(ct)) / max(sum(bt) / len(bt), 1e-9)
            ov = [cand.runs[k].overflow for k in cand.runs if cand.runs[k].case == case and cand.runs[k].valid]
            lines.append(f"  {case}: score={s:+.3f} dWNS={dw:+.1%} dTNS={dt:+.1%} overflow~{sum(ov)/len(ov):.0f}")
        else:
            lines.append(f"  {case}: score=n/a (all seeds invalid)")
    fails = [(k, r.fail_reason) for k, r in cand.runs.items() if not r.valid]
    if fails:
        lines.append("  failures: " + "; ".join(f"{k}:{r}" for k, r in fails[:6]))
    return "\n".join(lines)


def failure_feedback(cand: EvalResult, max_chars: int = 1500) -> str:
    """给 LLM 修复用的失败摘要：只取每个失败 run 日志的尾部。"""
    parts = []
    for k, r in cand.runs.items():
        if not r.valid:
            tail = cand.stage_logs.get(k, "")[-400:]
            parts.append(f"[{k}] {r.fail_reason}\n{tail}")
    s = "\n".join(parts)
    return s[-max_chars:]


ANALYZE_PROMPT = """You are analyzing the evaluation of one candidate change to a 3D-IC physical design flow.
Hypothesis of the change: {hyp}
Rule-based digest:
{digest}
Write <=120 words. If some runs are INVALID (failures listed), first say what kind of failure it is (legality / crash / timeout) and whether it looks systematic (all seeds of a case) or sporadic - an invalid candidate says nothing about the hypothesis itself; suggest how to make the same idea legal. Otherwise: (1) did the hypothesis hold? (2) which case/metric moved and plausible mechanism, (3) one concrete next-step suggestion. No preamble."""


def build_analyze_messages(hypothesis: str, digest: str) -> List[Dict[str, str]]:
    return [{"role": "user", "content": ANALYZE_PROMPT.format(hyp=hypothesis, digest=digest)}]
