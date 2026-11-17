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
    r"<<<EDIT\s+file=(?P<file>[^\n]+?)\s*\n<<<SEARCH[ \t]*\n(?P<search>.*?)\n===[ \t]*\n(?P<replace>.*?)\n>>>[ \t]*(?:\n>>>[ \t]*)?(?=\n|$)",
    re.DOTALL,
)
# 宽松版：模型常见错误 —— 用 ``` 包住、漏掉第二个 >>>、=== 后带空格、file= 带引号/反引号
_FENCE_RE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)\n```", re.DOTALL)


def _strip_fences(text: str) -> str:
    """若整段 EDIT 协议被塞进 ``` 代码块里，把围栏去掉（保留内部内容）。"""
    if "<<<EDIT" not in text:
        return text
    out = text
    for m in _FENCE_RE.finditer(text):
        inner = m.group(1)
        if "<<<EDIT" in inner or "<<<SEARCH" in inner:
            out = out.replace(m.group(0), inner)
    return out


@dataclass
class Proposal:
    hypothesis: str
    expected: str
    keywords: List[str]
    edits: List[Edit]
    raw: str
    parse_errors: List[str] = field(default_factory=list)


def parse_proposal(text: str) -> Proposal:
    text = _strip_fences(text)

    def grab(tag: str) -> str:
        m = re.search(rf"^\**{tag}\**\s*[:：]\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip().strip("*") if m else ""

    hyp = grab("HYPOTHESIS")
    exp = grab("EXPECTED")
    kws = [k.strip().strip("`") for k in grab("KEYWORDS").split(",") if k.strip()]
    edits, errs = [], []
    for m in EDIT_RE.finditer(text):
        f = m.group("file").strip().strip("`'\"")
        edits.append(Edit(f, m.group("search"), m.group("replace")))
    if not edits:
        hint = ""
        if "<<<EDIT" in text or "<<<SEARCH" in text:
            hint = " (EDIT markers present but malformed: check 'file=' header line, a '<<<SEARCH' line, a '===' separator line, and a closing '>>>' line)"
        errs.append("no EDIT blocks parsed" + hint)
    if not hyp:
        errs.append("missing HYPOTHESIS")
    return Proposal(hyp, exp, kws, edits, text, errs)


def _load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


class Proposer:
    def __init__(self, codebase: Codebase, task: str):
        self.cb = codebase
        self.task = task
        self.last_chunks = []
        self.use_knowledge = True
        self.rebuild_system()

    def rebuild_system(self) -> None:
        know = _load("knowledge.md") if (self.use_knowledge and (PROMPTS_DIR / "knowledge.md").exists()) else ""
        self.system = (
            _load("system.md")
            .replace("{TASK}", self.task)
            .replace("{KNOWLEDGE}", know)
            .replace("{OUTLINE}", self.cb.outline())
        )

    def _phase_guide(self, phase: str) -> str:
        """优先任务专属指引 phase_<phase>_<task>.md，否则通用 phase_<phase>.md。"""
        for name in (f"phase_{phase}_{self.task}.md", f"phase_{phase}.md"):
            if (PROMPTS_DIR / name).exists():
                return _load(name)
        return ""

    def precheck(self, prop: Proposal, chunks=None) -> List[str]:
        """免费的本地预检：SEARCH 必须出现在本轮展示过的代码片段里（否则多半是模型凭记忆编的）。
        chunks 显式传入（build_messages 的返回值）；不传则用 last_chunks。"""
        errs = []
        chunks = self.last_chunks if chunks is None else chunks
        by_file: Dict[str, str] = {}
        for c in chunks:
            by_file[c.file] = by_file.get(c.file, "") + "\n" + c.source
        for e in prop.edits:
            if e.file.startswith(("/", "\\")) or ".." in e.file.split("/") or ":" in e.file[:3]:
                errs.append(f"{e.file}: illegal path (absolute or parent traversal)")
                continue
            # "必须来自展示过的代码"是为了拦住凭记忆编造的 SEARCH。若 SEARCH 逐字存在于**当前真实代码**（含父代改动），
            # 即使该片段这轮没展示，也证明不是编造 —— 放行。只有既没展示又不在真实代码里的才拒绝。
            if e.file in self.cb.files:
                try:
                    real = self.cb.read(e.file)
                except OSError:
                    real = ""
                if e.search and real.count(e.search) == 1:
                    continue
            if e.file not in by_file:
                if e.file in self.cb.files:
                    errs.append(f"{e.file}: SEARCH block not found in the current file (it may have been changed by the parent candidate); "
                                f"this file was not shown this round — list its symbols in KEYWORDS or edit the shown chunks instead")
                else:
                    errs.append(f"{e.file}: this file was NOT shown in the code chunks and is not an editable file; shown files: {sorted(by_file)}")
            elif e.search not in by_file[e.file]:
                # 给一点线索：找相似的首行
                first = e.search.strip().splitlines()[0].strip() if e.search.strip() else ""
                hint = ""
                for ln in by_file[e.file].splitlines():
                    if first and first[:30] in ln:
                        hint = f" Closest shown line: {ln.strip()[:100]!r}"
                        break
                errs.append(f"{e.file}: SEARCH block does not appear verbatim in the shown chunks (copy it exactly, same indentation).{hint}")
        return errs

    def build_messages(
        self,
        phase: str,
        problem_profile: str,
        history_digest: str,
        parent_patch: Optional[str],
        focus_keywords: List[str],
        feedback_for_fix: Optional[str] = None,
        previous_response: Optional[str] = None,
        code_chars: int = 6000,
        boost_lines: Optional[Dict[str, List[int]]] = None,
    ):
        """返回 (messages, shown_chunks)。shown_chunks 要原样传给 precheck。"""
        chunks = self.cb.search(focus_keywords, k=6, max_chars=code_chars, boost_lines=boost_lines)
        self.last_chunks = chunks
        code = self.cb.render(chunks) if chunks else "(no matching code chunks; ask for other keywords)"
        if parent_patch and len(parent_patch) > 4000:
            parent_patch = parent_patch[:4000] + "\n... (patch truncated; full patch applied in working copy)"
        user = (
            _load("propose.md")
            .replace("{PHASE}", phase)
            .replace("{PHASE_GUIDE}", self._phase_guide(phase))
            .replace("{PROFILE}", problem_profile)
            .replace("{HISTORY}", history_digest)
            .replace("{PARENT_PATCH}", parent_patch or "(starting from official baseline)")
            .replace("{CODE}", code)
        )
        msgs = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        if feedback_for_fix and previous_response:
            msgs.append({"role": "assistant", "content": previous_response})
            msgs.append({"role": "user", "content": _load("fix.md").replace("{FEEDBACK}", feedback_for_fix)})
        return msgs, chunks
