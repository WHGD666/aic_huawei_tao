"""
selector.py -- 选择下一轮父代、维护精英集、决定阶段与停止。

设计要点（对应 PLAN.md §2.5 / §3.4）：
  * 稳健性指标：默认 mean - std（跨训练算例），惩罚"只在某个算例上好"的候选
  * 血统淘汰：同一父代连续 N 个子代失败/退化 -> 该父代不再被选
  * 阶段计划：probe -> structural -> combine，按已用评估次数推进
  * 精英集：top-k（按稳健指标），父代从精英集中按 softmax 采样，温度随阶段降低
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional

from .store import CandidateMeta, CandidateStore


class Selector:
    def __init__(
        self,
        store: CandidateStore,
        elite_size: int = 4,
        robustness: str = "mean_minus_std",
        max_consecutive_failures: int = 2,
        phase_plan: Optional[Dict[str, int]] = None,
        seed: int = 0,
        overflow_hard_limit: Optional[float] = None,   # full_flow：任一 run 溢出超过即不入精英集
    ):
        self.store = store
        self.elite_size = elite_size
        self.robustness = robustness
        self.max_fail = max_consecutive_failures
        self.phase_plan = phase_plan or {"probe": 6, "structural": 14, "combine": 8}
        self.rng = random.Random(seed)
        self.overflow_hard_limit = overflow_hard_limit
        self._fail_streak: Dict[str, int] = {}

    # ---------- 阶段 ----------
    def phase_for(self, evaluations_done: int) -> str:
        acc = 0
        for name, n in self.phase_plan.items():
            acc += n
            if evaluations_done < acc:
                return name
        return "final"

    # ---------- 精英 ----------
    def robust_value(self, m: CandidateMeta) -> float:
        return m.score_robust if m.score_robust is not None else -math.inf

    def elites(self) -> List[CandidateMeta]:
        ev = [m for m in self.store.evaluated() if m.all_valid]
        if self.overflow_hard_limit is not None:
            ev = [m for m in ev if (m.max_overflow is None or m.max_overflow <= self.overflow_hard_limit)]
        ev.sort(key=self.robust_value, reverse=True)
        return ev[: self.elite_size]

    def best(self) -> Optional[CandidateMeta]:
        e = self.elites()
        return e[0] if e else None

    # ---------- 父代选择 ----------
    def choose_parent(self, phase: str) -> Optional[str]:
        """返回父代 id；None 表示从基线出发。"""
        elites = [m for m in self.elites() if self._fail_streak.get(m.id, 0) < self.max_fail]
        if not elites:
            return None
        if phase == "probe":
            # 探针阶段只从基线出发，保证单旋钮可比
            return None
        if phase == "final":
            # 收尾阶段确定性地选最优
            return elites[0].id
        temp = {"structural": 1.0, "combine": 0.4, "final": 0.1}.get(phase, 1.0)
        vals = [self.robust_value(m) for m in elites]
        mx = max(vals)
        ws = [math.exp((v - mx) / max(temp, 1e-6)) for v in vals]
        tot = sum(ws)
        r = self.rng.random() * tot
        acc = 0.0
        for m, w in zip(elites, ws):
            acc += w
            if r <= acc:
                return m.id
        return elites[0].id

    # ---------- 反馈 ----------
    def record_outcome(self, child: CandidateMeta) -> None:
        """child 评估后调用：更新父代的失败计数。"""
        if child.parent is None:
            return
        parent = self.store.get(child.parent)
        improved = (
            child.status == "evaluated" and child.all_valid
            and child.score_robust is not None
            and (parent.score_robust is None or child.score_robust > parent.score_robust)
        )
        if improved:
            self._fail_streak[child.parent] = 0
        else:
            self._fail_streak[child.parent] = self._fail_streak.get(child.parent, 0) + 1

    # ---------- 摘要（给 Proposer 的历史上下文，控制长度） ----------
    def history_digest(self, max_items: int = 8, with_analysis: bool = True) -> str:
        """最近 max_items 个候选 + 当前精英（若不在最近里）。每条带一句 Analyzer 结论，让模型知道'为什么'。"""
        recent = sorted(self.store.all(), key=lambda m: m.created_at)[-max_items:]
        ids = {m.id for m in recent}
        items = list(recent)
        for e in self.elites()[:2]:
            if e.id not in ids:
                items.insert(0, e)
        lines = []
        for m in items:
            s = f"{m.score_total:+.3f}" if m.score_total is not None else "n/a"
            robust = f"{m.score_robust:+.3f}" if m.score_robust is not None else "n/a"
            if m.status == "gate_failed":
                # 门禁失败是 harness/格式层面的事，不要让模型以为"这个假设不行"
                fr = " (not evaluated: local check failed; the idea itself is untested)"
            else:
                fr = f" FAIL:{'; '.join(m.fail_reasons)[:80]}" if m.fail_reasons else ""
            pc = ""
            if m.per_case:
                pc = " [" + " ".join(f"{k.split('-')[0]}:{v:+.2f}" for k, v in m.per_case.items()) + "]"
            lines.append(f"- {m.id} (parent={m.parent or 'base'}, {m.phase}) [{m.status}] score={s} robust={robust}{pc}: {m.hypothesis[:100]}{fr}")
            if with_analysis and m.analysis and m.status != "gate_failed":
                lines.append(f"    analysis: {m.analysis[:220]}")
        return "\n".join(lines) if lines else "(no history yet)"
