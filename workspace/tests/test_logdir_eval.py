"""LogDirEvaluator：从原始日志目录提取指标，含无效判定与 ps→ns 换算。"""
import shutil
import sys
from pathlib import Path

import pytest

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from harness.evaluator import LogDirEvaluator  # noqa: E402

S = WS / "tests" / "samples"


def _mk(root: Path, case: str, seed: int, sta="sta_ng45.rpt", flow=None, grt="grt_ng45.log", rep="repair_ng45.log"):
    d = root / case / str(seed)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(S / sta, d / "sta.rpt")
    if flow:
        shutil.copy(S / flow, d / "flow.log")
    if grt:
        shutil.copy(S / grt, d / "grt.log")
    if rep:
        shutil.copy(S / rep, d / "repair.log")
    (d / "partition.log").write_text("cut_nets: 1234\nhbt_count: 980\n", encoding="utf-8")


def test_logdir_baseline_and_validity(tmp_path):
    base = tmp_path / "base"
    _mk(base, "bp_fe-ng45", 1001)
    _mk(base, "bp_fe-ng45", 1002, flow="fail_overlap.log")      # 非法布局
    _mk(base, "bp_fe-asap7", 1001, sta="sta_asap7_head.txt", grt=None)    # ps 单位；无 grt.log
    # bp_fe-asap7/1002 缺失
    ev = LogDirEvaluator(["bp_fe-ng45", "bp_fe-asap7"], [1001, 1002], cmd="true", results_root=tmp_path / "res",
                         baseline_root=base, full_flow=True)
    r = ev.baseline()
    assert r.runs["bp_fe-ng45/1001"].valid
    assert r.runs["bp_fe-ng45/1001"].wns == pytest.approx(-0.35)
    assert r.runs["bp_fe-ng45/1001"].overflow == 220
    assert not r.runs["bp_fe-ng45/1002"].valid and r.runs["bp_fe-ng45/1002"].fail_reason == "illegal placement"
    assert r.runs["bp_fe-asap7/1001"].wns == pytest.approx(-0.2105)          # ps -> ns
    assert not r.runs["bp_fe-asap7/1001"].valid and "overflow" in r.runs["bp_fe-asap7/1001"].fail_reason  # 无 grt.log 时缺溢出
    assert not r.runs["bp_fe-asap7/1002"].valid and "missing" in r.runs["bp_fe-asap7/1002"].fail_reason
    assert r.raw["profiles"]["bp_fe-ng45/1001"]["partition_stats"]["cut_nets"] == 1234
    assert not r.all_valid


def test_logdir_task1_no_overflow_needed(tmp_path):
    base = tmp_path / "base"
    _mk(base, "bp-ng45", 1001, grt=None)
    ev = LogDirEvaluator(["bp-ng45"], [1001], cmd="true", results_root=tmp_path / "res", baseline_root=base, full_flow=False)
    r = ev.baseline()
    assert r.runs["bp-ng45/1001"].valid and r.runs["bp-ng45/1001"].overflow == 0.0
