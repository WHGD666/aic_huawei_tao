"""
evaluator.py -- 候选评估接口的抽象 + 两种实现。

* OfficialEvaluator : 调官方评估接口（命令行形式待确认），解析其返回的指标 JSON
* MockEvaluator     : 本地开发用。对候选 patch 做确定性哈希，生成"看起来合理"的
                      WNS/TNS/溢出，并按一定概率制造失败，用来把整个演化循环跑满 30 轮。
"""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .scoring import RunMetrics


@dataclass
class EvalResult:
    candidate_id: str
    runs: Dict[str, RunMetrics]          # "case/seed" -> metrics
    stage_logs: Dict[str, str] = field(default_factory=dict)   # "case/seed" -> log tail
    wall_s: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def all_valid(self) -> bool:
        return all(r.valid for r in self.runs.values())

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "wall_s": self.wall_s,
            "runs": {k: vars(v) for k, v in self.runs.items()},
            "stage_logs": self.stage_logs,
        }


class Evaluator(ABC):
    def __init__(self, cases: List[str], seeds: List[int]):
        self.cases = cases
        self.seeds = seeds

    @abstractmethod
    def evaluate(self, candidate_id: str, workdir: Path) -> EvalResult: ...

    @abstractmethod
    def baseline(self) -> EvalResult: ...


# ---------------------------------------------------------------------------
class MockEvaluator(Evaluator):
    """
    确定性伪评估器：
      * 基线：每算例固定 WNS/TNS
      * 候选：从 workdir 内的 candidate.patch 取哈希 -> 伪随机改善/退化
      * 失败率 fail_rate（模拟非法布局 / 阶段失败 / 超时）
      * 若 patch 内含 "TIMING_WEIGHT" 之类关键字，略微提升期望（让 mock 对"方向"有响应，便于调 selector）
    """

    BASE = {
        "bp_fe-ng45": (-0.35, -120.0),
        "bp-ng45": (-0.55, -480.0),
        "ariane133-ng45": (-0.80, -950.0),
    }

    def __init__(self, cases, seeds, fail_rate: float = 0.25, full_flow: bool = False, latency_s: float = 0.0):
        super().__init__(cases, seeds)
        self.fail_rate = fail_rate
        self.full_flow = full_flow
        self.latency_s = latency_s

    def baseline(self) -> EvalResult:
        runs = {}
        for c in self.cases:
            w, t = self.BASE.get(c, (-0.5, -300.0))
            for s in self.seeds:
                jitter = 1 + 0.03 * ((s % 3) - 1)
                runs[f"{c}/{s}"] = RunMetrics(c, s, w * jitter, t * jitter, overflow=120.0)
        return EvalResult("baseline", runs)

    def evaluate(self, candidate_id: str, workdir: Path) -> EvalResult:
        t0 = time.time()
        patch = (workdir / "candidate.patch")
        blob = patch.read_bytes() if patch.exists() else candidate_id.encode()
        h = int(hashlib.sha256(blob).hexdigest(), 16)
        rng = random.Random(h)
        text = blob.decode("utf-8", "ignore")
        bias = 0.0
        for kw, b in (("timing", 0.06), ("slack", 0.06), ("critical", 0.05), ("weight", 0.03), ("density", 0.02)):
            if kw in text.lower():
                bias += b
        base = self.baseline().runs
        runs, logs = {}, {}
        # 失败按"候选"而非按"run"抽样：真实世界里一个改动要么在所有算例上都跑得通，要么系统性地炸（偶发单 run 失败另算小概率）
        cand_fails = rng.random() < self.fail_rate
        for key, b in base.items():
            if cand_fails or rng.random() < 0.005:
                reason = rng.choice(["legalization overlap", "stage timeout", "HBT min-spacing violation", "KeyError in partition"])
                runs[key] = RunMetrics(b.case, b.seed, 0, 0, valid=False, fail_reason=reason)
                logs[key] = f"[ERROR] {reason}\nTraceback (most recent call last): ...\n"
                continue
            dw = rng.gauss(bias, 0.12)
            dt = rng.gauss(bias * 1.5, 0.15)
            wns = b.wns * (1 - dw)
            tns = b.tns * (1 - dt)
            ov = max(0.0, b.overflow + rng.gauss(0, 40) + (80 if "buffer" in text.lower() else 0))
            runs[key] = RunMetrics(b.case, b.seed, wns, tns, overflow=ov)
            logs[key] = f"[INFO] wns={wns:.3f} tns={tns:.1f} overflow={ov:.0f}\n"
        if self.latency_s:
            time.sleep(self.latency_s)
        return EvalResult(candidate_id, runs, logs, wall_s=time.time() - t0)


# ---------------------------------------------------------------------------
class OfficialEvaluator(Evaluator):
    """
    官方评估接口适配器 —— 具体命令/返回格式待官方容器确认。
    约定（待改）：
        <cmd> --candidate <workdir> --out <json>
    返回 json 形如 {"runs": {"case/seed": {"wns":..,"tns":..,"overflow":..,"valid":..,"log":..}}}
    """

    def __init__(self, cases, seeds, cmd: str, baseline_json: Optional[Path] = None):
        super().__init__(cases, seeds)
        self.cmd = cmd
        self.baseline_json = baseline_json

    def baseline(self) -> EvalResult:
        if self.baseline_json and self.baseline_json.exists():
            return self._parse("baseline", json.load(open(self.baseline_json, encoding="utf-8")))
        raise RuntimeError("baseline json not provided; run baseline once and save it")

    def evaluate(self, candidate_id: str, workdir: Path) -> EvalResult:
        out = workdir / "eval_result.json"
        t0 = time.time()
        subprocess.run(
            self.cmd.format(workdir=str(workdir), out=str(out)),
            shell=True, check=False,
        )
        if not out.exists():
            runs = {f"{c}/{s}": RunMetrics(c, s, 0, 0, valid=False, fail_reason="no result file") for c in self.cases for s in self.seeds}
            return EvalResult(candidate_id, runs, wall_s=time.time() - t0)
        res = self._parse(candidate_id, json.load(open(out, encoding="utf-8")))
        res.wall_s = time.time() - t0
        return res

    def _parse(self, cid: str, d: dict) -> EvalResult:
        runs, logs = {}, {}
        for key, r in d.get("runs", {}).items():
            case, seed = key.split("/")
            runs[key] = RunMetrics(case, int(seed), float(r.get("wns", 0)), float(r.get("tns", 0)),
                                   overflow=float(r.get("overflow", 0)), valid=bool(r.get("valid", True)),
                                   fail_reason=str(r.get("fail_reason", "")))
            logs[key] = str(r.get("log", ""))
        return EvalResult(cid, runs, logs, raw=d)


# ---------------------------------------------------------------------------
class LogDirEvaluator(Evaluator):
    """
    备用适配器：官方接口若只留下"每个 case/seed 一个目录的原始日志/报告"，用 openroad_parse 直接提取指标。
    约定目录布局（可用 layout 参数改）：
        <root>/<case>/<seed>/{<stage>.json, sta.rpt|*.rpt, repair.log|3_4_place_resized.log, grt.log|5_1_grt.log, partition.log, flow.log}
    指标优先级：ORFS json（metric_stage 指定阶段，如 placeopt / globalroute）> 文本 report_wns/tns。
    从日志推断 valid / fail_reason；wns/tns 自动按 Liberty 单位换算成 ns。
    """

    def __init__(self, cases, seeds, cmd: str, results_root: Path, baseline_root: Optional[Path] = None,
                 full_flow: bool = False, metric_stage: Optional[str] = None):
        super().__init__(cases, seeds)
        self.cmd = cmd
        self.results_root = results_root
        self.baseline_root = baseline_root
        self.full_flow = full_flow
        # 任务一采样点 = 一次时序优化后（ORFS 阶段名 placeopt）；任务二 = 全局布线后（globalroute）
        self.metric_stage = metric_stage or ("globalroute" if full_flow else "placeopt")

    def baseline(self) -> EvalResult:
        if not self.baseline_root or not self.baseline_root.exists():
            raise RuntimeError("baseline results root not found; run the official baseline once")
        return self._collect("baseline", self.baseline_root)

    def evaluate(self, candidate_id: str, workdir: Path) -> EvalResult:
        t0 = time.time()
        out_root = self.results_root / candidate_id
        subprocess.run(self.cmd.format(workdir=str(workdir), out=str(out_root)), shell=True, check=False)
        res = self._collect(candidate_id, out_root)
        res.wall_s = time.time() - t0
        return res

    def _collect(self, cid: str, root: Path) -> EvalResult:
        from .openroad_parse import detect_time_unit, parse_congestion, parse_orfs_metrics, parse_stage_status, parse_wns_tns, to_ns
        from .profile import build_run_profile

        runs, logs, profiles = {}, {}, {}
        for c in self.cases:
            for s in self.seeds:
                d = root / c / str(s)
                key = f"{c}/{s}"
                if not d.exists():
                    runs[key] = RunMetrics(c, s, 0, 0, valid=False, fail_reason="missing result dir")
                    continue
                sta = _read_any(d, ["sta.rpt", "timing.rpt", "*.rpt"])
                flow = _read_any(d, ["flow.log", "*.log"])
                rep = _read_any(d, ["repair.log", "rsz.log", "3_4_place_resized.log", "4_1_cts.log"])
                grt = _read_any(d, ["grt.log", "route.log", "5_1_grt.log"])
                part = _read_any(d, ["partition.log", "partition.txt", "partition.json"])
                status = parse_stage_status(flow + "\n" + sta)
                # ---- 指标：json 优先 ----
                js = _read_any(d, [f"{self.metric_stage}.json", f"*{self.metric_stage}*.json", "metrics.json"])
                om = parse_orfs_metrics(js, None) if js else None
                src = "text"
                if om and om.tns is not None and om.wns_effective is not None:
                    unit = om.time_unit or detect_time_unit(sta + "\n" + c)
                    wns, tns = om.wns_effective, om.tns
                    src = f"json:{om.stage}"
                else:
                    unit = detect_time_unit(sta + "\n" + c)
                    wns, tns = parse_wns_tns(sta)
                ov = 0.0
                if self.full_flow:
                    cg = parse_congestion(grt)
                    ov = cg.total_overflow if cg else float("nan")
                valid = status.ok and wns is not None and tns is not None and not (self.full_flow and ov != ov)
                reason = status.fail_reason or ("missing wns/tns" if (wns is None or tns is None) else "") or ("missing overflow" if (self.full_flow and ov != ov) else "")
                runs[key] = RunMetrics(c, s, to_ns(wns, unit) or 0.0, to_ns(tns, unit) or 0.0,
                                       overflow=0.0 if ov != ov else ov, valid=valid, fail_reason=reason)
                logs[key] = f"[metrics from {src}]\n" + (flow or sta)[-1500:]
                prof = build_run_profile(c, s, sta, rep, grt, part).to_dict()
                if om:
                    prof["orfs"] = {k: v for k, v in vars(om).items() if k != "raw"}
                profiles[key] = prof
        return EvalResult(cid, runs, logs, raw={"profiles": profiles})


def _read_any(d: Path, patterns: List[str]) -> str:
    for pat in patterns:
        for p in sorted(d.glob(pat)):
            try:
                return p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
    return ""
