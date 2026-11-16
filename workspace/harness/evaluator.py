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
        for key, b in base.items():
            if rng.random() < self.fail_rate:
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
