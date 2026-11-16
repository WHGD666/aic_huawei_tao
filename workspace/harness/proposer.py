"""
proposer.py -- 组织提示词、调用 LLM、解析成 Edit 列表。

输出协议（对小模型最稳的格式）：
  HYPOTHESIS: <一句话>
  EXPECTED: <预期对 WNS/TNS/溢出的影响>
  KEYWORDS: a, b, c            （可选；用于下一轮检索）
  <<<EDIT file=relative/path.py
  <<<SEARCH
  ...exact lines...
  ===
  ...replacement...
  >>>
  >>>
  (可多个 EDIT)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .codebase import Codebase, Edit

PROMPTS_DIR = Path(__file__).parent / "prompts"

EDIT_RE = re.compile(
    r"<<<EDIT\s+file=(?P<file>[^\n]+?)\s*\n<<<SEARCH\n(?P<search>.*?)\n===\n(?P<replace>.*?)\n>>>\s*\n>>>",
    re.DOTALL,
)


@dataclass
class Proposal:
    hypothesis: str
    expected: str
    keywords: List[str]
    edits: List[Edit]
    raw: str
    parse_errors: List[str] = field(default_factory=list)


def parse_proposal(text: str) -> Proposal:
    def grab(tag: str) -> str:
        m = re.search(rf"^{tag}:\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    hyp = grab("HYPOTHESIS")
    exp = grab("EXPECTED")
    kws = [k.strip() for k in grab("KEYWORDS").split(",") if k.strip()]
    edits, errs = [], []
    for m in EDIT_RE.finditer(text):
        f = m.group("file").strip().strip("`'\"")
        edits.append(Edit(f, m.group("search"), m.group("replace")))
    if not edits:
        errs.append("no EDIT blocks parsed")
    if not hyp:
        errs.append("missing HYPOTHESIS")
    return Proposal(hyp, exp, kws, edits, text, errs)


def _load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


class Proposer:
    def __init__(self, codebase: Codebase, task: str):
        self.cb = codebase
        self.task = task
        self.system = (
            _load("system.md")
            .replace("{TASK}", task)
            .replace("{KNOWLEDGE}", _load("knowledge.md") if (PROMPTS_DIR / "knowledge.md").exists() else "")
            .replace("{OUTLINE}", codebase.outline())
        )

    def build_messages(
        self,
        phase: str,
        problem_profile: str,
        history_digest: str,
        parent_patch: Optional[str],
        focus_keywords: List[str],
        feedback_for_fix: Optional[str] = None,
        previous_response: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        chunks = self.cb.search(focus_keywords, k=6)
        code = self.cb.render(chunks) if chunks else "(no matching code chunks; ask for other keywords)"
        user = (
            _load("propose.md")
            .replace("{PHASE}", phase)
            .replace("{PHASE_GUIDE}", _load(f"phase_{phase}.md") if (PROMPTS_DIR / f"phase_{phase}.md").exists() else "")
            .replace("{PROFILE}", problem_profile)
            .replace("{HISTORY}", history_digest)
            .replace("{PARENT_PATCH}", parent_patch or "(starting from official baseline)")
            .replace("{CODE}", code)
        )
        msgs = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        if feedback_for_fix and previous_response:
            msgs.append({"role": "assistant", "content": previous_response})
            msgs.append({"role": "user", "content": _load("fix.md").replace("{FEEDBACK}", feedback_for_fix)})
        return msgs
