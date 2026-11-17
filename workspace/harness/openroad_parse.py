"""
openroad_parse.py -- 从 OpenROAD / OpenSTA 文本日志与报告里提取指标。

覆盖（格式按常见输出写，拿到官方容器后用真实日志校正 —— 每个函数都有对应测试样例）：
  * report_wns / report_tns / report_worst_slack
  * report_checks  关键路径块（startpoint / endpoint / slack / 级数 / 线延迟占比）
  * global_route   拥塞表（每层 + Total Overflow）
  * repair_timing  动作统计（inserted N buffers, resized M instances ...）
  * check_placement 结果
  * 错误 / 阶段失败探测（ERROR 行、Traceback、timeout、exit code）

设计原则：
  * 所有函数输入都是 str，返回 dataclass / dict，不做 IO —— 方便测试和在容器外使用
  * 容错：找不到就返回 None / 空，不抛异常；调用方决定是否判无效
  * 单位：返回原始数字 + 推断的单位（ns/ps），换算交给 profile.py
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 基本时序指标
# ---------------------------------------------------------------------------
_RE_WNS = re.compile(r"^\s*wns\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$", re.M | re.I)
_RE_TNS = re.compile(r"^\s*tns\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$", re.M | re.I)
_RE_WORST = re.compile(r"^\s*worst slack\s+(?:max\s+)?(-?\d+(?:\.\d+)?)\s*$", re.M | re.I)
# 也兼容 "wns: -0.12" / "WNS = -0.12" / json 风格
_RE_WNS_KV = re.compile(r"\bwns\b\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.I)
_RE_TNS_KV = re.compile(r"\btns\b\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.I)


def _last_float(pattern: re.Pattern, text: str) -> Optional[float]:
    ms = pattern.findall(text)
    return float(ms[-1]) if ms else None


def parse_wns_tns(text: str) -> Tuple[Optional[float], Optional[float]]:
    """返回最后一次出现的 (wns, tns)。评估器通常在流程末尾打印一次，取最后一次最稳。"""
    wns = _last_float(_RE_WNS, text)
    if wns is None:
        wns = _last_float(_RE_WNS_KV, text)
    if wns is None:
        wns = _last_float(_RE_WORST, text)
    tns = _last_float(_RE_TNS, text)
    if tns is None:
        tns = _last_float(_RE_TNS_KV, text)
    return wns, tns


_RE_TIME_UNIT = re.compile(r"time_unit\s*:\s*\"?\s*1\s*(ns|ps)\s*\"?", re.I)
_RE_CMD_UNITS = re.compile(r"set_cmd_units\s+.*?-time\s+(ns|ps)", re.I)


def detect_time_unit(text: str, default: str = "ns") -> str:
    """从 Liberty 片段 / 脚本 / 报告头推断时间单位。asap7 是 ps。"""
    m = _RE_CMD_UNITS.search(text) or _RE_TIME_UNIT.search(text)
    if m:
        return m.group(1).lower()
    # 不按工艺名猜单位（题面禁止按算例/工艺特判，而且 PDK 的单位应从 Liberty 读）。读不到就用 default。
    return default


def to_ns(value: Optional[float], unit: str) -> Optional[float]:
    if value is None:
        return None
    return value / 1000.0 if unit == "ps" else value


# ---------------------------------------------------------------------------
# report_checks 路径块
# ---------------------------------------------------------------------------
@dataclass
class TimingPath:
    startpoint: str
    endpoint: str
    slack: float
    required: Optional[float] = None
    arrival: Optional[float] = None
    stages: int = 0                    # 经过的单元数（含起点寄存器）
    wire_delay: float = 0.0            # 线延迟（net 行）之和
    cell_delay: float = 0.0            # 单元延迟之和
    max_net_delay: float = 0.0
    crosses_tier: bool = False         # 路径里出现 HBT / bond / tier 关键字
    cells: List[str] = field(default_factory=list)

    @property
    def wire_ratio(self) -> float:
        tot = self.wire_delay + self.cell_delay
        return self.wire_delay / tot if tot > 0 else 0.0


_RE_START = re.compile(r"^\s*Startpoint:\s*(\S+)", re.M)
_RE_END = re.compile(r"^\s*Endpoint:\s*(\S+)", re.M)
_RE_SLACK = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+slack\s*\((?:VIOLATED|MET)\)", re.M)
_RE_REQ = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+(?:data )?required time", re.M)
_RE_ARR = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+data arrival time", re.M)
# report_checks -format full_clock_expanded 的行：  "  0.05   0.30 v  u1/ZN (NAND2_X1)"  或 net 行 "  0.02   0.32   net1 (net)"
_RE_STAGE = re.compile(r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+[v^]?\s*(\S+)\s+\(([^)]+)\)", re.M)
_RE_NET = re.compile(r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\S+)\s+\(net\)", re.M)
_HBT_KW = re.compile(r"hbt|bond|tier|tsv|via3d|cross", re.I)


def parse_timing_paths(text: str) -> List[TimingPath]:
    """把 report_checks 输出切成路径块并解析。块以 'Startpoint:' 开始。"""
    paths: List[TimingPath] = []
    starts = [m.start() for m in _RE_START.finditer(text)]
    for i, s in enumerate(starts):
        block = text[s: starts[i + 1] if i + 1 < len(starts) else len(text)]
        sp = _RE_START.search(block)
        ep = _RE_END.search(block)
        sl = _RE_SLACK.search(block)
        if not (sp and ep and sl):
            continue
        p = TimingPath(sp.group(1), ep.group(1), float(sl.group(1)))
        r = _RE_REQ.search(block); a = _RE_ARR.search(block)
        p.required = float(r.group(1)) if r else None
        p.arrival = float(a.group(1)) if a else None
        # 只统计 arrival 段（required 段之前）
        arr_end = block.find("data arrival time")
        seg = block[:arr_end] if arr_end > 0 else block
        for m in _RE_STAGE.finditer(seg):
            incr, name, kind = float(m.group(1)), m.group(3), m.group(4)
            if kind == "net":
                p.wire_delay += incr
                p.max_net_delay = max(p.max_net_delay, incr)
                if _HBT_KW.search(name):
                    p.crosses_tier = True
            elif kind in ("clock", "input port", "output port") or "clock network delay" in kind:
                continue
            else:
                # 起点寄存器的 CK 引脚行（延迟 0、名字以 /CK /CLK /CP 结尾）不算一级
                if incr == 0.0 and re.search(r"/(CK|CLK|CP|C|CKB)$", name):
                    continue
                p.cell_delay += incr
                p.stages += 1
                p.cells.append(kind)
                if _HBT_KW.search(name) or _HBT_KW.search(kind):
                    p.crosses_tier = True
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# global_route 拥塞表
# ---------------------------------------------------------------------------
@dataclass
class CongestionReport:
    per_layer: Dict[str, Dict[str, float]]   # layer -> {resource, demand, usage, max_h, max_v, overflow}
    total_overflow: float
    total_usage_pct: Optional[float] = None


# 真实格式（OpenROAD grt/src/GlobalRouter.cpp::reportCongestion，2026-11）：
#   [INFO GRT-0096] Final congestion report:
#   Layer         Resource        Demand        Usage (%)    Max H / Max V / Total Congestion
#   ---------------------------------------------------------------------------------------
#   metal2          540000        431200           79.85%             6 /  0 / 120
#   Total          1890000       1142200           60.43%             6 /  4 / 220
# "Total Congestion" 列 == 每层 overflow 之和（源码变量名 total_overflow）。
_RE_CONG_ROW = re.compile(
    r"^\s*(?P<layer>[A-Za-z]\w*)\s+(?P<res>\d+)\s+(?P<dem>\d+)\s+(?P<usage>\d+(?:\.\d+)?)%\s+"
    r"(?P<mh>\d+)\s*/\s*(?P<mv>\d+)\s*/\s*(?P<ov>\d+)\s*$", re.M)
# 简式兜底: "Total overflow: 356" / "overflow: 356"
_RE_TOTAL_OV = re.compile(r"total\s+(?:overflow|congestion)\s*[:=]?\s*(\d+)", re.I)
_RE_OV_SIMPLE = re.compile(r"^\s*(?:\[INFO GRT-\d+\]\s*)?overflow\s*[:=]\s*(\d+)", re.I | re.M)


def parse_congestion(text: str) -> Optional[CongestionReport]:
    rows = list(_RE_CONG_ROW.finditer(text))
    if rows:
        per: Dict[str, Dict[str, float]] = {}
        total = None
        for m in rows:
            d = dict(resource=float(m["res"]), demand=float(m["dem"]), usage=float(m["usage"]),
                     max_h=float(m["mh"]), max_v=float(m["mv"]), overflow=float(m["ov"]))
            if m["layer"].lower() == "total":
                total = d
            else:
                per[m["layer"]] = d
        if total is not None:
            return CongestionReport(per, total["overflow"], total["usage"])
        return CongestionReport(per, sum(v["overflow"] for v in per.values()))
    m = _RE_TOTAL_OV.findall(text)
    if m:
        return CongestionReport({}, float(m[-1]))
    m = _RE_OV_SIMPLE.findall(text)
    if m:
        return CongestionReport({}, float(m[-1]))
    return None


# ---------------------------------------------------------------------------
# repair_timing / repair_design 动作统计
# 真实格式（OpenROAD rsz，2026-11）：
#   [INFO RSZ-0038] Inserted 2 buffers in 2 nets.                 (repair_design)
#   [INFO RSZ-00xx] Inserted N buffers, M to split loads.         (repair_timing)
#   [INFO RSZ-00xx] Inserted N buffers.
#   [INFO RSZ-00xx] Removed N buffers.
#   [INFO RSZ-00xx] Resized N instances: a up, b up match, c down, d VT
#   [INFO RSZ-00xx] Resized N instances.
#   [INFO RSZ-00xx] Swapped pins on N instances.
#   [INFO RSZ-00xx] Cloned N instances.
#   [WARNING RSZ-00xx] Unable to repair all setup violations.
#   [ERROR RSZ-00xx] Max utilization reached.   /  Max buffer count reached.
#   进度表（repair_timing -setup，policy/ProgressReporter.cc）：
#      Iter   | Removed | Resized | Inserted | Cloned |  Pin  |   Area   |    WNS   |    TNS     |  Viol  |  Worst
#             | Buffers |  Gates  | Buffers  |  Gates | Swaps |          |          |            | Endpts | Endpoint
#      final |       0 |     410 |      230 |     12 |    55 |   +3.2% |   -0.280 |      -77.0 |    301 | u_x/y
# ---------------------------------------------------------------------------
@dataclass
class RepairStats:
    inserted_buffers: int = 0
    removed_buffers: int = 0
    resized: int = 0
    cloned: int = 0
    pin_swaps: int = 0
    split_loads: int = 0
    unfixable: bool = False               # "Unable to repair all setup violations"
    max_utilization_hit: bool = False
    max_buffer_hit: bool = False
    final_wns: Optional[float] = None     # 进度表 final 行
    final_tns: Optional[float] = None
    final_viol_endpoints: Optional[int] = None
    area_growth_pct: Optional[float] = None


_RE_REPAIR = [
    ("inserted_buffers", re.compile(r"[Ii]nserted\s+(\d+)\s+buffers?\b")),
    ("removed_buffers", re.compile(r"[Rr]emoved\s+(\d+)\s+buffers?\b")),
    ("resized", re.compile(r"[Rr]esized\s+(\d+)\s+instances?\b")),
    ("cloned", re.compile(r"[Cc]loned\s+(\d+)\s+(?:instances?|gates?)")),
    ("pin_swaps", re.compile(r"[Ss]wapped\s+pins?\s+on\s+(\d+)\s+instances?")),
    ("split_loads", re.compile(r"(\d+)\s+to\s+split\s+loads?")),
]
_RE_UNFIXABLE = re.compile(r"Unable to repair all setup violations", re.I)
_RE_MAXUTIL = re.compile(r"Max utilization reached", re.I)
_RE_MAXBUF = re.compile(r"Max buffer count reached", re.I)
# 进度表 final 行：label | removed | resized | inserted | cloned | swaps | area% | WNS | [StTNS |] TNS | viol | worst
_RE_PROGRESS_FINAL = re.compile(
    r"^\s*final\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([+-]?\d+(?:\.\d+)?)%\s*\|"
    r"\s*(-?\d+(?:\.\d+)?)\s*\|(?:\s*-?\d+(?:\.\d+)?\s*\|)?\s*(-?\d+(?:\.\d+)?)\s*\|\s*(\d+)\s*\|", re.M)


def parse_repair_stats(text: str) -> RepairStats:
    st = RepairStats()
    for attr, pat in _RE_REPAIR:
        setattr(st, attr, sum(int(x) for x in pat.findall(text)))
    st.unfixable = bool(_RE_UNFIXABLE.search(text))
    st.max_utilization_hit = bool(_RE_MAXUTIL.search(text))
    st.max_buffer_hit = bool(_RE_MAXBUF.search(text))
    rows = _RE_PROGRESS_FINAL.findall(text)
    if rows:
        r = rows[-1]
        st.area_growth_pct = float(r[5])
        st.final_wns = float(r[6])
        st.final_tns = float(r[7])
        st.final_viol_endpoints = int(r[8])
    return st


# ---------------------------------------------------------------------------
# check_placement / 错误探测
# ---------------------------------------------------------------------------
_RE_PLACE_OK = re.compile(r"\[INFO DPL-\d+\]\s*Placement\s+(?:is\s+)?(?:valid|legal|OK)", re.I)
_RE_PLACE_FAIL = re.compile(r"(overlap|not aligned|off[- ]site|outside (?:the )?core|placement failed|placement (?:is )?invalid)", re.I)
_RE_ERROR = re.compile(r"^\s*\[?ERROR\b.*$", re.I | re.M)
_RE_TRACEBACK = re.compile(r"Traceback \(most recent call last\)")
_RE_TIMEOUT = re.compile(r"\b(timed? ?out|TimeoutExpired|killed by signal)\b", re.I)
_RE_OOM = re.compile(r"(out of memory|OutOfMemoryError|CUDA error: out of memory|Killed)", re.I)


@dataclass
class StageStatus:
    ok: bool
    placement_legal: Optional[bool]
    errors: List[str]
    timeout: bool
    oom: bool
    exception: bool

    @property
    def fail_reason(self) -> str:
        if self.timeout:
            return "timeout"
        if self.oom:
            return "out of memory"
        if self.exception:
            return "python exception"
        if self.placement_legal is False:
            return "illegal placement"
        if self.errors:
            return self.errors[0][:120]
        return ""


def parse_stage_status(text: str) -> StageStatus:
    errs = [m.group(0).strip() for m in _RE_ERROR.finditer(text)]
    legal: Optional[bool] = None
    if _RE_PLACE_FAIL.search(text):
        legal = False
    elif _RE_PLACE_OK.search(text):
        legal = True
    to = bool(_RE_TIMEOUT.search(text))
    oom = bool(_RE_OOM.search(text))
    exc = bool(_RE_TRACEBACK.search(text))
    ok = not (errs or to or oom or exc or legal is False)
    return StageStatus(ok, legal, errs, to, oom, exc)


# ---------------------------------------------------------------------------
# ORFS 风格指标 json（OpenROAD `-metrics <stage>.json`，src/Metrics.tcl）
#   key = "<stage>__<metric>"，stage 由 utl::set_metrics_stage 决定（placeopt__{} / cts__{} / globalroute__{} / finish__{}）
#   timing__setup__ws   worst slack（可正）         timing__setup__wns  worst negative slack（≤0）
#   timing__setup__tns  total negative slack        timing__drv__setup_violation_count
#   timing__drv__max_slew / max_cap / max_fanout    power__total        design__instance__area 等
#   单位跟随 report_units_metric（run__flow__platform 下 "time 1ns" 之类，若有）
# ---------------------------------------------------------------------------
import json as _json


@dataclass
class OrfsMetrics:
    stage: str
    ws: Optional[float] = None
    wns: Optional[float] = None
    tns: Optional[float] = None
    setup_violation_count: Optional[int] = None
    max_slew_viol: Optional[int] = None
    max_cap_viol: Optional[int] = None
    max_fanout_viol: Optional[int] = None
    power_total: Optional[float] = None
    instance_count: Optional[int] = None
    time_unit: Optional[str] = None
    raw: Dict[str, object] = field(default_factory=dict)

    @property
    def wns_effective(self) -> Optional[float]:
        """评分用的 WNS：优先 wns，否则 min(ws, 0)。"""
        if self.wns is not None:
            return self.wns
        if self.ws is not None:
            return min(self.ws, 0.0)
        return None


def parse_orfs_metrics(text: str, stage: Optional[str] = None) -> Optional[OrfsMetrics]:
    """解析 <stage>.json。stage=None 时自动取 json 里出现的第一个 '<stage>__timing__' 前缀。"""
    try:
        d = _json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not d:
        return None
    if stage is None:
        for k in d:
            if "__timing__" in k:
                stage = k.split("__timing__")[0]
                break
        if stage is None:
            for k in d:
                if "__" in k:
                    stage = k.split("__")[0]
                    break
    if stage is None:
        return None
    p = stage + "__"

    def g(name, cast=float):
        v = d.get(p + name)
        if v is None:
            return None
        try:
            return cast(v)
        except (ValueError, TypeError):
            return None

    m = OrfsMetrics(stage=stage, raw=d)
    m.ws = g("timing__setup__ws")
    m.wns = g("timing__setup__wns")
    m.tns = g("timing__setup__tns")
    m.setup_violation_count = g("timing__drv__setup_violation_count", int)
    m.max_slew_viol = g("timing__drv__max_slew", int)
    m.max_cap_viol = g("timing__drv__max_cap", int)
    m.max_fanout_viol = g("timing__drv__max_fanout", int)
    m.power_total = g("power__total")
    m.instance_count = g("design__instance__count", int)
    # 单位：report_units_metric 写 "<stage>__time" = "1ns" 之类；也兼容顶层 "run__flow__..."
    for k, v in d.items():
        if k.endswith("__time") and isinstance(v, str):
            mm = re.search(r"1\s*(ns|ps)", v)
            if mm:
                m.time_unit = mm.group(1)
                break
    return m


# ---------------------------------------------------------------------------
# 切分 / HBT 统计（基线的文本输出，格式待定；先按 key: value 兜底）
# ---------------------------------------------------------------------------
_RE_KV_INT = re.compile(r"^\s*(?P<k>[A-Za-z_][\w ]*?)\s*[:=]\s*(?P<v>-?\d+(?:\.\d+)?)\s*$", re.M)


def parse_kv_numbers(text: str) -> Dict[str, float]:
    """把 'cut_nets: 1234' / 'top_area = 0.51' 这类行收成 dict（key 小写、空格转下划线）。"""
    out: Dict[str, float] = {}
    for m in _RE_KV_INT.finditer(text):
        k = m["k"].strip().lower().replace(" ", "_")
        try:
            out[k] = float(m["v"])
        except ValueError:
            pass
    return out
