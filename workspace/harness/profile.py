"""
profile.py -- 把一次评估（基线或候选）的日志 / 报告压成"问题画像"，给 Proposer 当上下文。

输入：每个 (case, seed) 的若干文本：sta 报告、repair 日志、grt 日志、partition 统计。
输出：≤ ~60 行的纯文本（控制 prompt 长度），以及结构化 dict（存 eval.json 里，复盘用）。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .openroad_parse import (
    CongestionReport, RepairStats, TimingPath, parse_congestion, parse_kv_numbers,
    parse_repair_stats, parse_timing_paths, parse_wns_tns, detect_time_unit, to_ns,
)

SLACK_BINS = [(-1e9, -0.5), (-0.5, -0.2), (-0.2, -0.05), (-0.05, 0.0)]


@dataclass
class RunProfile:
    case: str
    seed: int
    wns_ns: Optional[float] = None
    tns_ns: Optional[float] = None
    time_unit: str = "ns"
    n_paths: int = 0
    slack_hist: Dict[str, int] = field(default_factory=dict)
    top_paths: List[TimingPath] = field(default_factory=list)
    cross_tier_ratio: float = 0.0          # top 路径中跨层比例
    avg_wire_ratio: float = 0.0            # 线延迟占比
    avg_stages: float = 0.0
    repair: Optional[RepairStats] = None
    congestion: Optional[CongestionReport] = None
    partition_stats: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "case": self.case, "seed": self.seed, "wns_ns": self.wns_ns, "tns_ns": self.tns_ns,
            "time_unit": self.time_unit, "n_paths": self.n_paths, "slack_hist": self.slack_hist,
            "cross_tier_ratio": self.cross_tier_ratio, "avg_wire_ratio": self.avg_wire_ratio,
            "avg_stages": self.avg_stages, "partition_stats": self.partition_stats,
            "top_paths": [vars(p) for p in self.top_paths[:10]],
        }
        if self.repair:
            d["repair"] = vars(self.repair)
        if self.congestion:
            d["congestion"] = {"total_overflow": self.congestion.total_overflow, "per_layer": self.congestion.per_layer}
        return d


def build_run_profile(case: str, seed: int, sta_text: str = "", repair_text: str = "",
                      grt_text: str = "", partition_text: str = "", lib_hint: str = "") -> RunProfile:
    rp = RunProfile(case, seed)
    unit = detect_time_unit(lib_hint + "\n" + sta_text + "\n" + case)
    rp.time_unit = unit
    wns, tns = parse_wns_tns(sta_text)
    rp.wns_ns, rp.tns_ns = to_ns(wns, unit), to_ns(tns, unit)

    paths = parse_timing_paths(sta_text)
    rp.n_paths = len(paths)
    if paths:
        paths.sort(key=lambda p: p.slack)
        rp.top_paths = paths[:10]
        hist = Counter()
        for p in paths:
            s = to_ns(p.slack, unit)
            for lo, hi in SLACK_BINS:
                if lo <= s < hi:
                    hist[f"[{lo if lo > -1e8 else '-inf'},{hi})"] += 1
                    break
        rp.slack_hist = dict(hist)
        rp.cross_tier_ratio = sum(p.crosses_tier for p in rp.top_paths) / len(rp.top_paths)
        rp.avg_wire_ratio = sum(p.wire_ratio for p in rp.top_paths) / len(rp.top_paths)
        rp.avg_stages = sum(p.stages for p in rp.top_paths) / len(rp.top_paths)
    if repair_text:
        rp.repair = parse_repair_stats(repair_text)
    if grt_text:
        rp.congestion = parse_congestion(grt_text)
    if partition_text:
        rp.partition_stats = parse_kv_numbers(partition_text)
    return rp


def render_profile(profiles: List[RunProfile], max_lines: int = 60) -> str:
    """跨 (case, seed) 汇总成给 LLM 的文本。按算例聚合种子。"""
    by_case: Dict[str, List[RunProfile]] = {}
    for p in profiles:
        by_case.setdefault(p.case, []).append(p)
    lines: List[str] = ["Baseline problem profile (avg over seeds):"]
    for case, ps in by_case.items():
        n = len(ps)
        w = _avg([p.wns_ns for p in ps]); t = _avg([p.tns_ns for p in ps])
        lines.append(f"- {case}: WNS={_f(w)} ns  TNS={_f(t)} ns  paths_reported={ps[0].n_paths}")
        hist = Counter()
        for p in ps:
            hist.update(p.slack_hist)
        if hist:
            lines.append("    slack histogram (ns): " + ", ".join(f"{k}:{v // n}" for k, v in sorted(hist.items())))
        if ps[0].top_paths:
            lines.append(f"    top paths: cross-tier {ps[0].cross_tier_ratio:.0%}, wire-delay share {ps[0].avg_wire_ratio:.0%}, avg stages {ps[0].avg_stages:.1f}")
            for tp in ps[0].top_paths[:3]:
                lines.append(f"      {tp.startpoint} -> {tp.endpoint}: slack={tp.slack:.3f} stages={tp.stages} wire%={tp.wire_ratio:.0%}{' XTIER' if tp.crosses_tier else ''}")
        if ps[0].repair:
            r = ps[0].repair
            tail = []
            if r.final_viol_endpoints is not None:
                tail.append(f"{r.final_viol_endpoints} endpoints still violating")
            if r.unfixable:
                tail.append("UNFIXABLE")
            if r.max_utilization_hit:
                tail.append("MAX-UTIL HIT")
            if r.max_buffer_hit:
                tail.append("MAX-BUF HIT")
            lines.append(f"    repair_timing: +{r.inserted_buffers} buffers, {r.resized} resized, {r.cloned} cloned, {r.pin_swaps} pin-swaps" + (", " + ", ".join(tail) if tail else ""))
        if ps[0].congestion:
            c = ps[0].congestion
            worst = sorted(c.per_layer.items(), key=lambda kv: -kv[1]["overflow"])[:2]
            lines.append(f"    routing: total overflow {c.total_overflow:.0f}" + (f", worst layers " + ", ".join(f"{k}:{v['overflow']:.0f}" for k, v in worst) if worst else ""))
        if ps[0].partition_stats:
            ks = list(ps[0].partition_stats.items())[:6]
            lines.append("    partition: " + ", ".join(f"{k}={v:g}" for k, v in ks))
    lines.append("Scoring reminder: TNS weight 0.6, WNS 0.4; improvements must generalize across all cases.")
    return "\n".join(lines[:max_lines])


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _f(x):
    return "n/a" if x is None else f"{x:.3f}"
