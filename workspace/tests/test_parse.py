"""openroad_parse / profile 的测试。样例日志按 OpenROAD 常见格式手写；拿到真实日志后替换样例并保持测试通过。"""
import sys
from pathlib import Path

import pytest

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from harness.openroad_parse import (  # noqa: E402
    detect_time_unit, parse_congestion, parse_kv_numbers, parse_repair_stats,
    parse_stage_status, parse_timing_paths, parse_wns_tns, to_ns,
)
from harness.profile import build_run_profile, render_profile  # noqa: E402

S = WS / "tests" / "samples"


def _r(name):
    return (S / name).read_text(encoding="utf-8")


def test_wns_tns_last_occurrence():
    w, t = parse_wns_tns(_r("sta_ng45.rpt"))
    assert w == pytest.approx(-0.350) and t == pytest.approx(-120.45)
    w2, t2 = parse_wns_tns("wns: -1.5\nTNS = -30\nwns 0.1\ntns 0")
    assert w2 == pytest.approx(0.1) and t2 == pytest.approx(0)
    assert parse_wns_tns("nothing here") == (None, None)


def test_time_unit_detection_and_conversion():
    assert detect_time_unit(_r("sta_ng45.rpt")) == "ns"
    assert detect_time_unit(_r("sta_asap7_head.txt")) == "ps"
    assert detect_time_unit("set_cmd_units -time ps -capacitance fF") == "ps"
    assert detect_time_unit("bp_fe-asap7 run") == "ps"
    w, t = parse_wns_tns(_r("sta_asap7_head.txt"))
    assert to_ns(w, "ps") == pytest.approx(-0.2105) and to_ns(t, "ps") == pytest.approx(-15.4)


def test_timing_paths():
    paths = parse_timing_paths(_r("sta_ng45.rpt"))
    assert len(paths) == 2
    p0, p1 = paths
    assert p0.startpoint.startswith("u_core/pc_reg[3]") and p0.slack == pytest.approx(0.41)
    assert p0.crosses_tier is True                  # hbt_n_0412 net
    assert p0.stages == 4                           # DFF/Q, NAND2, MUX2, DFF/D
    assert p0.wire_delay == pytest.approx(0.03 + 0.21 + 0.02)
    assert p0.max_net_delay == pytest.approx(0.21)
    assert p1.slack == pytest.approx(-0.20) and p1.crosses_tier is False
    assert p1.wire_ratio > 0.6                      # 线延迟主导的路径
    assert p1.required == pytest.approx(0.96) and p1.arrival == pytest.approx(1.16)


def test_congestion_table():
    c = parse_congestion(_r("grt_ng45.log"))
    assert c is not None
    assert c.total_overflow == 220
    assert c.per_layer["metal2"]["overflow"] == 120 and c.per_layer["metal2"]["usage"] == pytest.approx(79.85)
    assert c.total_usage_pct == pytest.approx(60.43)
    assert "Total" not in c.per_layer
    # 简式
    c2 = parse_congestion("[INFO GRT-0123] Total overflow: 356")
    assert c2.total_overflow == 356 and c2.per_layer == {}
    assert parse_congestion("no routing here") is None


def test_repair_stats():
    r = parse_repair_stats(_r("repair_ng45.log"))
    assert r.inserted_buffers == 87 + 230
    assert r.removed_buffers == 8
    assert r.resized == 1203 + 410
    assert r.pin_swaps == 55 and r.cloned == 12 and r.split_loads == 18
    assert r.unfixable is True
    assert r.max_utilization_hit is True and r.max_buffer_hit is False
    # 进度表 final 行
    assert r.final_wns == pytest.approx(-0.280) and r.final_tns == pytest.approx(-77.0)
    assert r.final_viol_endpoints == 301 and r.area_growth_pct == pytest.approx(3.2)
    # 带 StTNS 列的变体也要能解析
    alt = "    final |       1 |       2 |        3 |      4 |     5 |    +1.0% |   -0.100 |      -20.0 |      -50.0 |     77 | x/y\n"
    r2 = parse_repair_stats(alt)
    assert r2.final_tns == pytest.approx(-50.0) and r2.final_viol_endpoints == 77


def test_stage_status():
    ok = parse_stage_status(_r("sta_ng45.rpt"))
    assert ok.ok and not ok.errors
    ov = parse_stage_status(_r("fail_overlap.log"))
    assert not ov.ok and ov.placement_legal is False and ov.fail_reason == "illegal placement"
    py = parse_stage_status(_r("fail_py.log"))
    assert not py.ok and py.exception and py.fail_reason == "python exception"
    to = parse_stage_status("stage grt ... TimeoutExpired after 3600s")
    assert to.timeout and to.fail_reason == "timeout"
    oom = parse_stage_status("RuntimeError: CUDA error: out of memory")
    assert oom.oom


def test_kv_numbers():
    d = parse_kv_numbers("cut nets: 1234\ntop_area = 0.51\nhbt_count: 980\nnot a number: abc")
    assert d == {"cut_nets": 1234.0, "top_area": 0.51, "hbt_count": 980.0}


def test_profile_build_and_render():
    rp = build_run_profile("bp_fe-ng45", 1001, sta_text=_r("sta_ng45.rpt"), repair_text=_r("repair_ng45.log"),
                           grt_text=_r("grt_ng45.log"), partition_text="cut_nets: 1234\nhbt_count: 980")
    assert rp.wns_ns == pytest.approx(-0.35) and rp.time_unit == "ns"
    assert rp.n_paths == 2 and rp.top_paths[0].slack == pytest.approx(-0.20)
    assert rp.slack_hist.get("[-0.5,-0.2)") is None and rp.slack_hist.get("[-0.2,-0.05)") == 1
    assert rp.cross_tier_ratio == pytest.approx(0.5)
    assert rp.repair.inserted_buffers == 317 and rp.congestion.total_overflow == 220
    d = rp.to_dict()
    assert d["partition_stats"]["cut_nets"] == 1234
    txt = render_profile([rp, build_run_profile("bp_fe-ng45", 1002, sta_text=_r("sta_ng45.rpt"))])
    assert "bp_fe-ng45" in txt and "WNS=-0.350" in txt and "overflow 220" in txt and "XTIER" in txt
    assert len(txt.splitlines()) <= 60

    # asap7：ps -> ns 换算后 profile 数值应在 ns 量级
    ra = build_run_profile("bp_fe-asap7", 1001, sta_text=_r("sta_asap7_head.txt"))
    assert ra.time_unit == "ps" and ra.wns_ns == pytest.approx(-0.2105)
