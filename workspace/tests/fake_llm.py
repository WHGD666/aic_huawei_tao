"""
fake_llm.py -- 测试用的脚本化 LLM：不联网、零费用。
按调用顺序返回预置响应；可注入"格式错 / SEARCH 不匹配 / 动禁区文件"等故障，验证 gate 与修复逻辑。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from harness.budget import Usage

GOOD_PROPOSAL = """HYPOTHESIS: Add timing criticality weight to partition features so critical cells co-locate.
EXPECTED: TNS -5%..-15% across cases, WNS slight, overflow unchanged.
KEYWORDS: timing, slack, partition, net_weights
<<<EDIT file=partition/gnn_partition.py
<<<SEARCH
TIMING_WEIGHT = 0.0
===
TIMING_WEIGHT = 0.5
>>>
>>>
"""

BAD_SEARCH = """HYPOTHESIS: Raise net weight for critical nets.
EXPECTED: TNS down.
KEYWORDS: weight
<<<EDIT file=placement/place3d.py
<<<SEARCH
NET_WEIGHT_DEFAULT = 42.0
===
NET_WEIGHT_DEFAULT = 2.0
>>>
>>>
"""

FIXED_SEARCH = """HYPOTHESIS: Raise net weight for critical nets.
EXPECTED: TNS down.
KEYWORDS: weight
<<<EDIT file=placement/place3d.py
<<<SEARCH
NET_WEIGHT_DEFAULT = 1.0
===
NET_WEIGHT_DEFAULT = 2.0
>>>
>>>
"""

# 建立在 GOOD_PROPOSAL 之上的第二步：同一文件、另一旋钮（structural 阶段用；KEYWORDS 与 GOOD 一致以便检索同一文件）
CHILD_PROPOSAL = """HYPOTHESIS: Relax area balance tolerance so the critical-weighted partition is less constrained.
EXPECTED: TNS down slightly on all cases.
KEYWORDS: timing, slack, partition, net_weights
<<<EDIT file=partition/gnn_partition.py
<<<SEARCH
AREA_BALANCE_TOL = 0.05
===
AREA_BALANCE_TOL = 0.08
>>>
>>>
"""

TOUCH_EVAL = """HYPOTHESIS: cheat
EXPECTED: n/a
KEYWORDS: x
<<<EDIT file=eval/evaluate.py
<<<SEARCH
def score(): return 0
===
def score(): return 1
>>>
>>>
"""

SYNTAX_ERR = """HYPOTHESIS: density tweak
EXPECTED: n/a
KEYWORDS: density
<<<EDIT file=placement/place3d.py
<<<SEARCH
DENSITY_WEIGHT = 1.0
===
DENSITY_WEIGHT = (1.0
>>>
>>>
"""

ANALYSIS = "Hypothesis partially held: TNS improved on two cases. Next: try 0.8."


class FakeLLM:
    def __init__(self, script: List[str], usage: Usage = Usage(2000, 300, 1500)):
        self.script = list(script)
        self.usage = usage
        self.calls: List[Tuple[str, List[Dict[str, str]]]] = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, tag=""):
        self.calls.append((tag, messages))
        if tag == "analyze":
            return ANALYSIS, Usage(400, 60, 0)
        if not self.script:
            return GOOD_PROPOSAL, self.usage
        return self.script.pop(0), self.usage
