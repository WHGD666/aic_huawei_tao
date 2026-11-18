"""
scoring.py -- 把题面第六节的计分公式写成代码，作为本地 selector 的唯一打分口径。

切分布局得分 = 0.4*WNS改善 + 0.6*TNS改善
全流程得分   = 0.4*WNS改善 + 0.6*TNS改善 - 溢出惩罚
改善比例     = (base_viol - cand_viol) / base_viol        (违例量 = |负 slack|)
溢出惩罚     = 0            若 overflow <= 300
             = 0.1*(overflow-300)/300   否则（无上限）

先每算例每种子打分 -> 种子平均 -> 算例等权加和。
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional

OVERFLOW_THRESHOLD = 300.0
W_WNS, W_TNS = 0.4, 0.6


@dataclass
class RunMetrics:
    """一个 (算例, 种子) 的结果。wns/tns 为负 slack（ns），overflow 为两层溢出之和。"""
    case: str
    seed: int
    wns: float
    tns: float
    overflow: float = 0.0
    valid: bool = True
    fail_reason: str = ""

    @property
    def wns_viol(self) -> float:
        return abs(min(self.wns, 0.0))

    @property
    def tns_viol(self) -> float:
        return abs(min(self.tns, 0.0))


def improvement(base_viol: float, cand_viol: float) -> float:
    """
    原文公式在 base_viol == 0 时分母为 0、未定义。本地约定（已在 PLAN §3.5 标注、需向组委会确认）：
    基线无违例时，候选也无违例记 0，候选出现违例记 -1。
    """
    if base_viol <= 0:
        return 0.0 if cand_viol <= 0 else -1.0
    return (base_viol - cand_viol) / base_viol


def overflow_penalty(overflow: float) -> float:
    if overflow <= OVERFLOW_THRESHOLD:
        return 0.0
    return 0.1 * (overflow - OVERFLOW_THRESHOLD) / OVERFLOW_THRESHOLD


def run_score(base: RunMetrics, cand: RunMetrics, full_flow: bool) -> Optional[float]:
    if (not base.valid or not cand.valid
            or not all(math.isfinite(v) for v in (base.wns, base.tns, cand.wns, cand.tns))
            or (full_flow and (not math.isfinite(cand.overflow) or cand.overflow < 0))):
        return None  # 无效结果：题面规定无效，交给上层判定整个候选失效
    s = W_WNS * improvement(base.wns_viol, cand.wns_viol) + W_TNS * improvement(base.tns_viol, cand.tns_viol)
    if full_flow:
        s -= overflow_penalty(cand.overflow)
    return s


@dataclass
class CandidateScore:
    total: float                      # 所有算例等权加和（官方口径）
    per_case: Dict[str, float]        # 每算例（种子平均）
    per_run: Dict[str, Optional[float]]  # "case/seed" -> score 或 None
    all_valid: bool
    robust_mean: float                # 算例均值
    robust_min: float                 # 最差算例
    robust_mean_minus_std: float      # 均值 - 标准差


def candidate_score(
    baseline: Dict[str, RunMetrics],
    candidate: Dict[str, RunMetrics],
    full_flow: bool,
) -> CandidateScore:
    """baseline / candidate 都是 {"case/seed": RunMetrics}。"""
    per_run: Dict[str, Optional[float]] = {}
    by_case: Dict[str, List[float]] = {}
    all_valid = True
    for key, b in baseline.items():
        c = candidate.get(key)
        if c is None:
            per_run[key] = None
            all_valid = False
            continue
        s = run_score(b, c, full_flow)
        per_run[key] = s
        if s is None:
            all_valid = False
            continue
        by_case.setdefault(b.case, []).append(s)

    per_case = {c: (sum(v) / len(v)) for c, v in by_case.items()}
    vals = list(per_case.values())
    # 题面：最终候选的必测算例与种子必须全部有效 -> 任一 run 无效，整个候选不参与排序
    if not all_valid or not vals:
        return CandidateScore(
            total=-math.inf, per_case=per_case, per_run=per_run, all_valid=False,
            robust_mean=-math.inf, robust_min=-math.inf, robust_mean_minus_std=-math.inf,
        )
    total = sum(vals)
    mean = statistics.fmean(vals)
    mn = min(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return CandidateScore(
        total=total,
        per_case=per_case,
        per_run=per_run,
        all_valid=all_valid,
        robust_mean=mean,
        robust_min=mn,
        robust_mean_minus_std=mean - std,
    )
