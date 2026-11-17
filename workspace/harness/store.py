"""
store.py -- 候选历史库。直接产出提交要求里 logs/ 需要的"父子关系 / 代码版本 / 评估结果 / 失败原因"。

目录结构：
  candidates/
    <id>/
      meta.json         parent, hypothesis, files_changed, created_at, status, score...
      candidate.patch   相对 baseline 的 git diff
      eval.json         评估结果（如果有）
      llm/              本候选相关的 prompt/response 存档（省钱复盘用）
  lineage.jsonl         每行一个事件，追加写，方便画演化树
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class CandidateMeta:
    id: str
    parent: Optional[str]
    hypothesis: str                       # LLM 提出的假设（一句话）
    expected_effect: str                  # 预期影响
    files_changed: List[str]
    phase: str                            # probe | structural | combine | final
    created_at: float = field(default_factory=time.time)
    status: str = "proposed"              # proposed | gate_failed | evaluated | invalid | discarded
    gate_report: str = ""
    score_total: Optional[float] = None
    score_robust: Optional[float] = None
    per_case: Dict[str, float] = field(default_factory=dict)
    all_valid: Optional[bool] = None
    fail_reasons: List[str] = field(default_factory=list)
    analysis: str = ""                    # Analyzer 的 ≤300 token 摘要
    llm_cost_cny: float = 0.0
    eval_wall_s: float = 0.0
    tags: List[str] = field(default_factory=list)
    max_overflow: Optional[float] = None  # full_flow：9 个 run 里最大的溢出（硬门槛用）
    prompt_chars: int = 0                 # 本轮 propose prompt 字符数（费用自适应用）


class CandidateStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lineage = root / "lineage.jsonl"
        self._metas: Dict[str, CandidateMeta] = {}
        self._load()

    # ---------- CRUD ----------
    def new_id(self) -> str:
        n = len(self._metas) + 1
        return f"c{n:03d}"

    def create(self, meta: CandidateMeta, patch_text: str) -> Path:
        d = self.root / meta.id
        (d / "llm").mkdir(parents=True, exist_ok=True)
        (d / "candidate.patch").write_text(patch_text, encoding="utf-8")
        self._metas[meta.id] = meta
        self._flush(meta)
        self._event("create", meta.id, parent=meta.parent, phase=meta.phase, hypothesis=meta.hypothesis)
        return d

    def update(self, meta: CandidateMeta, event: str = "update", **extra) -> None:
        self._metas[meta.id] = meta
        self._flush(meta)
        self._event(event, meta.id, status=meta.status, score=meta.score_total, **extra)

    def save_eval(self, cid: str, eval_dict: dict) -> None:
        (self.root / cid / "eval.json").write_text(json.dumps(eval_dict, indent=1, ensure_ascii=False), encoding="utf-8")

    def save_llm(self, cid: str, name: str, prompt: list, response: str, usage: dict) -> None:
        p = self.root / cid / "llm" / f"{name}.json"
        p.write_text(json.dumps({"prompt": prompt, "response": response, "usage": usage}, indent=1, ensure_ascii=False), encoding="utf-8")

    def get(self, cid: str) -> CandidateMeta:
        return self._metas[cid]

    def dir(self, cid: str) -> Path:
        return self.root / cid

    def all(self) -> List[CandidateMeta]:
        return sorted(self._metas.values(), key=lambda m: m.created_at)

    def evaluated(self) -> List[CandidateMeta]:
        return [m for m in self.all() if m.status == "evaluated" and m.score_total is not None]

    def children(self, cid: str) -> List[CandidateMeta]:
        return [m for m in self.all() if m.parent == cid]

    def lineage_of(self, cid: str) -> List[str]:
        chain, cur = [], cid
        while cur:
            chain.append(cur)
            cur = self._metas[cur].parent if cur in self._metas else None
        return list(reversed(chain))

    # ---------- 导出 ----------
    def export_tree_text(self) -> str:
        roots = [m for m in self.all() if m.parent is None]
        lines: List[str] = []

        def walk(m: CandidateMeta, depth: int):
            s = f"{m.score_total:+.3f}" if m.score_total is not None else "  --  "
            lines.append(f"{'  ' * depth}{m.id} [{m.status:>11}] {s}  {m.hypothesis[:60]}")
            for ch in self.children(m.id):
                walk(ch, depth + 1)

        for r in roots:
            walk(r, 0)
        return "\n".join(lines)

    # ---------- internals ----------
    def _flush(self, meta: CandidateMeta) -> None:
        (self.root / meta.id).mkdir(parents=True, exist_ok=True)
        (self.root / meta.id / "meta.json").write_text(json.dumps(asdict(meta), indent=1, ensure_ascii=False), encoding="utf-8")

    def _event(self, kind: str, cid: str, **kw) -> None:
        with open(self.lineage, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "event": kind, "id": cid, **kw}, ensure_ascii=False) + "\n")

    def _load(self) -> None:
        for mp in self.root.glob("*/meta.json"):
            d = json.loads(mp.read_text(encoding="utf-8"))
            self._metas[d["id"]] = CandidateMeta(**d)
