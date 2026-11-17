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
        self._auto = 0

    def chat(self, messages, temperature=0.7, max_tokens=2048, tag=""):
        self.calls.append((tag, messages))
        if tag == "analyze":
            return ANALYSIS, Usage(400, 60, 0)
        if not self.script:
            return self._auto_proposal(messages), self.usage
        return self.script.pop(0), self.usage

    def _auto_proposal(self, messages) -> str:
        """脚本用尽时：像一个"会读代码"的模型一样，从 prompt 展示的片段里找出当前 TIMING_WEIGHT 值并提出新值。
        这样建立在父代之上的子代也能通过 SEARCH 校验。"""
        import re
        shown = "\n".join(m["content"] for m in messages if m["role"] == "user")
        # 只看"Relevant code chunks"之后的部分（历史/父代 patch 里也会出现旧值）
        i = shown.find("## Relevant code chunks")
        region = shown[i:] if i >= 0 else shown
        m = re.search(r"^TIMING_WEIGHT = ([0-9.]+)$", region, re.M)
        cur = m.group(1) if m else "0.0"
        self._auto += 1
        new = f"0.{50 + self._auto}"
        # 先换 REPLACE 行（0.5）再换 SEARCH 行（0.0），避免二次替换吃掉刚写入的值
        return GOOD_PROPOSAL.replace("TIMING_WEIGHT = 0.5\n", f"TIMING_WEIGHT = {new}\n").replace("TIMING_WEIGHT = 0.0\n", f"TIMING_WEIGHT = {cur}\n")


def varied(n: int) -> List[str]:
    """n 个互不相同的**从基线出发**的合法提案（probe 阶段用）。structural 阶段请让脚本用尽，走 _auto_proposal。"""
    return [GOOD_PROPOSAL.replace("TIMING_WEIGHT = 0.5", f"TIMING_WEIGHT = 0.{i + 1}") for i in range(n)]
