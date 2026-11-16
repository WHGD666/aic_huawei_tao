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
    if re.search(r"\basap7\b", text, re.I):
        return "ps"
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


# 形态 A（GRT-0096 表格）:  "metal2    123456   98765   80.00%   12 / 9 / 356"
_RE_CONG_ROW = re.compile(
    r"^\s*(?P<layer>[A-Za-z]\w*)\s+(?P<res>\d+)\s+(?P<dem>\d+)\s+(?P<usage>\d+(?:\.\d+)?)%\s+"
    r"(?P<mh>\d+)\s*/\s*(?P<mv>\d+)\s*/\s*(?P<ov>\d+)\s*$", re.M)
# 形态 B（旧版 / 简式）: "[INFO GRT-0xxx] Total overflow: 356"  或 "Overflow: 356"
_RE_TOTAL_OV = re.compile(r"total\s+overflow\s*[:=]?\s*(\d+)", re.I)
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
# ---------------------------------------------------------------------------
@dataclass
class RepairStats:
    inserted_buffers: int = 0
    resized: int = 0
    cloned: int = 0
    pin_swaps: int = 0
    split_loads: int = 0
    unfixable_endpoints: int = 0
    max_utilization_hit: bool = False
    repair_iterations: int = 0


_RE_REPAIR = [
    ("inserted_buffers", re.compile(r"[Ii]nserted\s+(\d+)\s+(?:input |output )?buffers?")),
    ("resized", re.compile(r"[Rr]esized\s+(\d+)\s+instances?")),
    ("cloned", re.compile(r"[Cc]loned\s+(\d+)\s+(?:instances?|gates?)")),
    ("pin_swaps", re.compile(r"[Ss]wapped\s+pins?\s+(?:on|of)\s+(\d+)\s+instances?")),
    ("split_loads", re.compile(r"[Ss]plit\s+(\d+)\s+loads?")),
    ("unfixable_endpoints", re.compile(r"[Uu]nable to repair\s+(\d+)\s+(?:of\s+\d+\s+)?(?:paths|endpoints)")),
]
_RE_MAXUTIL = re.compile(r"max utilization reached", re.I)
# repair_timing 的迭代表："Iteration |  WNS | ..." 表头后面是 "      20  | -0.280 | ..." 这样的行
_RE_REPAIR_ITER = re.compile(r"^\s*(\d+)\s*\|\s*-?\d+(?:\.\d+)?\s*\|", re.M)


def parse_repair_stats(text: str) -> RepairStats:
    st = RepairStats()
    for attr, pat in _RE_REPAIR:
        tot = sum(int(x) for x in pat.findall(text))
        setattr(st, attr, tot)
    st.max_utilization_hit = bool(_RE_MAXUTIL.search(text))
    it = _RE_REPAIR_ITER.findall(text)
    st.repair_iterations = int(it[-1]) if it else 0
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
