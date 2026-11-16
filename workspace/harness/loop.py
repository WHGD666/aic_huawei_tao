"""
loop.py -- 演化主循环：提出修改 → 生成代码 → 检查代码 → 提交评估 → 分析反馈 → 选择方案。

用法:
    python -m harness.loop --config config/harness.yaml [--llm-mode live|record|replay] [--max-rounds N]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .analyzer import build_analyze_messages, failure_feedback, rule_digest
from .budget import Budget, Pricing, Usage
from .codebase import Codebase, apply_edits, git_diff
from .evaluator import EvalResult, Evaluator, MockEvaluator, OfficialEvaluator
from .gate import Gate
from .llm import LLMClient, LLMError
from .proposer import Proposer, parse_proposal
from .scoring import candidate_score
from .selector import Selector
from .store import CandidateMeta, CandidateStore

WS = Path(__file__).resolve().parent.parent


def _env_expand(v):
    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
        return os.environ.get(v[2:-1], "")
    return v


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(open(path, encoding="utf-8"))
    cfg["llm"]["model"] = _env_expand(cfg["llm"]["model"]) or "deepseek-chat"
    return cfg


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


class Harness:
    def __init__(self, cfg: dict, llm_mode: str = "live", run_dir: Optional[Path] = None):
        self.cfg = cfg
        self.task = cfg["task"]
        self.full_flow = self.task == "full_flow"
        self.run_dir = run_dir or (WS / "logs" / f"run_{self.task}_{time.strftime('%Y%m%d_%H%M%S')}")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_f = open(self.run_dir / "harness.log", "a", encoding="utf-8")

        # 预算
        pricing = Pricing(WS / "config" / "pricing.yaml")
        b = cfg["budget"]
        self.budget = Budget(b["max_evaluations"], b["max_cost_cny"], pricing,
                             state_file=self.run_dir / "budget.json",
                             reserve_evaluations=b.get("reserve_evaluations", 0))
        self.per_round_cap = b.get("per_round_cost_cap_cny", 0.35)

        # LLM
        l = cfg["llm"]
        self.llm = LLMClient(model=l["model"], timeout_s=l["timeout_s"], max_retries=l["max_retries"],
                             record_dir=WS / "logs" / "llm_records", mode=llm_mode)
        self.lcfg = l

        # 代码库 / 门禁
        wl = yaml.safe_load(open(WS / cfg["gate"]["whitelist_file"], encoding="utf-8"))
        self.base_root = WS / "baseline"
        self.cb = Codebase(self.base_root, wl["allowed"], wl.get("denied", []))
        self.gate = Gate(WS / cfg["gate"]["whitelist_file"], cfg["gate"]["forbidden_patterns"],
                         cfg["gate"].get("run_pyflakes", True), cfg["gate"].get("smoke_test_cmd", ""))

        # 评估器
        e = cfg["evaluator"]
        if e["mode"] == "mock":
            self.evaluator: Evaluator = MockEvaluator(e["cases"], e["seeds"], full_flow=self.full_flow)
        elif e["mode"] == "logdir":
            from .evaluator import LogDirEvaluator
            self.evaluator = LogDirEvaluator(e["cases"], e["seeds"], e["official_cmd"],
                                             WS / "results" / self.task, WS / "baseline" / "results" / self.task,
                                             full_flow=self.full_flow)
        else:
            self.evaluator = OfficialEvaluator(e["cases"], e["seeds"], e["official_cmd"], WS / "baseline" / "baseline_eval.json")

        # 历史 / 选择
        self.store = CandidateStore(self.run_dir / "candidates")
        s = cfg["search"]
        self.selector = Selector(self.store, s["elite_size"], s["robustness_metric"],
                                 s["max_consecutive_failures_per_lineage"], s.get("phase_plan"), seed=cfg.get("seed", 0))
        self.proposer = Proposer(self.cb, self.task)

        self.baseline_eval: Optional[EvalResult] = None
        self.profile: str = ""

    # ---------- util ----------
    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        self.log_f.write(line + "\n")
        self.log_f.flush()

    def _chat(self, msgs, temperature, max_tokens, tag, cid: Optional[str] = None):
        if not self.budget.can_call_llm():
            raise LLMError("cost budget exhausted")
        text, usage = self.llm.chat(msgs, temperature=temperature, max_tokens=max_tokens, tag=tag)
        cost = self.budget.charge_llm(self.lcfg["model"], usage)
        if cid:
            self.store.save_llm(cid, tag, msgs, text, {**vars(usage), "cost_cny": cost})
        self.log(f"  llm[{tag}] in={usage.prompt_tokens} hit={usage.cache_hit_tokens} out={usage.completion_tokens} cost={cost:.4f}")
        return text, cost

    # ---------- init ----------
    def init_baseline(self) -> None:
        self.baseline_eval = self.evaluator.baseline()
        (self.store.root / "baseline").mkdir(exist_ok=True)
        self.store.save_eval("baseline", self.baseline_eval.to_dict())
        # 问题画像：若评估器带了解析好的 profiles（LogDirEvaluator），用 profile.render_profile；否则退化为指标摘要
        profs = self.baseline_eval.raw.get("profiles") if isinstance(self.baseline_eval.raw, dict) else None
        if profs:
            from .profile import RunProfile, render_profile
            rps = []
            for d in profs.values():
                rp = RunProfile(d["case"], d["seed"], d.get("wns_ns"), d.get("tns_ns"), d.get("time_unit", "ns"),
                                d.get("n_paths", 0), d.get("slack_hist", {}))
                rp.cross_tier_ratio = d.get("cross_tier_ratio", 0.0)
                rp.avg_wire_ratio = d.get("avg_wire_ratio", 0.0)
                rp.avg_stages = d.get("avg_stages", 0.0)
                rp.partition_stats = d.get("partition_stats", {})
                rps.append(rp)
            self.profile = render_profile(rps)
        else:
            lines = ["Baseline violations (negative slack, ns) per case (avg over seeds):"]
            by_case: Dict[str, List] = {}
            for k, r in self.baseline_eval.runs.items():
                by_case.setdefault(r.case, []).append(r)
            for c, rs in by_case.items():
                w = sum(r.wns for r in rs) / len(rs)
                t = sum(r.tns for r in rs) / len(rs)
                ov = sum(r.overflow for r in rs) / len(rs)
                lines.append(f"  {c}: WNS={w:.3f} TNS={t:.1f} overflow={ov:.0f}")
            lines.append("Notes: TNS weight 0.6; prefer mechanisms that generalize across all three cases.")
            self.profile = "\n".join(lines)
        self.log("baseline loaded\n" + self.profile)

    # ---------- one round ----------
    def round(self, idx: int) -> Optional[CandidateMeta]:
        evals_done = self.budget.state.evaluations
        phase = self.selector.phase_for(evals_done)
        parent_id = self.selector.choose_parent(phase)
        parent_patch = (self.store.dir(parent_id) / "candidate.patch").read_text(encoding="utf-8") if parent_id else None
        parent_meta = self.store.get(parent_id) if parent_id else None
        keywords = (parent_meta.tags if parent_meta and parent_meta.tags else
                    ["timing", "weight", "partition", "density", "wirelength", "slack"])
        cid = self.store.new_id()
        self.log(f"=== round {idx} | phase={phase} | parent={parent_id or 'base'} | {self.budget.summary()}")

        # ① 提出修改（含最多 1 次修复）
        round_cost = 0.0
        msgs = self.proposer.build_messages(phase, self.profile, self.selector.history_digest(), parent_patch, keywords)
        text, c = self._chat(msgs, self.lcfg["temperature_propose"], self.lcfg["max_tokens_propose"], "propose")
        round_cost += c
        prop = parse_proposal(text)

        work = self.run_dir / "work" / cid
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        # 父代 patch 先落到工作副本（简单做法：父代工作目录整体复制）
        if parent_id and (self.run_dir / "work" / parent_id).exists():
            shutil.copytree(self.run_dir / "work" / parent_id, work, dirs_exist_ok=True)
        base_for_edit = work if parent_id else self.base_root

        changed, errs = ({}, prop.parse_errors)
        if not errs:
            changed, errs = apply_edits(base_for_edit, work, prop.edits)
        rep = self.gate.check(changed, git_diff(self.base_root, work, list(changed)) if changed else "", work) if not errs else None
        if errs or (rep and not rep.ok):
            fb = "\n".join(errs) if errs else rep.text()
            self.log(f"  gate/apply failed -> fix attempt: {fb[:200]}")
            msgs2 = self.proposer.build_messages(phase, self.profile, self.selector.history_digest(), parent_patch, keywords,
                                                 feedback_for_fix=fb, previous_response=text)
            text, c = self._chat(msgs2, self.lcfg["temperature_fix"], self.lcfg["max_tokens_fix"], "fix")
            round_cost += c
            prop = parse_proposal(text)
            shutil.rmtree(work); work.mkdir(parents=True)
            if parent_id and (self.run_dir / "work" / parent_id).exists():
                shutil.copytree(self.run_dir / "work" / parent_id, work, dirs_exist_ok=True)
            changed, errs = ({}, prop.parse_errors)
            if not errs:
                changed, errs = apply_edits(base_for_edit, work, prop.edits)
            rep = self.gate.check(changed, git_diff(self.base_root, work, list(changed)) if changed else "", work) if not errs else None

        patch = git_diff(self.base_root, work, list(changed)) if changed else ""
        meta = CandidateMeta(cid, parent_id, prop.hypothesis or "(unparsed)", prop.expected, list(changed), phase,
                             tags=prop.keywords[:8], llm_cost_cny=round_cost)
        self.store.create(meta, patch)
        self.store.save_llm(cid, "propose_final", msgs, text, {"cost_cny": round_cost})
        (work / "candidate.patch").write_text(patch, encoding="utf-8")

        if errs or (rep and not rep.ok):
            meta.status = "gate_failed"
            meta.gate_report = "\n".join(errs) if errs else rep.text()
            meta.fail_reasons = [meta.gate_report[:200]]
            self.store.update(meta, "gate_failed")
            self.selector.record_outcome(meta)
            self.log(f"  {cid} gate_failed (no evaluation consumed)")
            return meta
        meta.gate_report = rep.text() if rep else "OK"

        # ④ 提交评估
        if not self.budget.can_evaluate():
            self.log("  evaluation budget exhausted; stopping")
            meta.status = "discarded"; self.store.update(meta, "no_budget")
            return None
        res = self.evaluator.evaluate(cid, work)
        self.budget.charge_evaluation()
        self.store.save_eval(cid, res.to_dict())
        meta.eval_wall_s = res.wall_s

        # ⑤ 分析反馈
        score = candidate_score(self.baseline_eval.runs, res.runs, self.full_flow)
        digest = rule_digest(self.baseline_eval, res, score)
        meta.all_valid = score.all_valid
        meta.per_case = score.per_case
        meta.fail_reasons = [f"{k}:{r.fail_reason}" for k, r in res.runs.items() if not r.valid][:6]
        if score.all_valid:
            meta.status = "evaluated"
            meta.score_total = score.total
            meta.score_robust = {"mean": score.robust_mean, "min": score.robust_min,
                                 "mean_minus_std": score.robust_mean_minus_std}[self.selector.robustness]
        else:
            meta.status = "invalid"  # 题面：任一 run 无效则候选无效
        # LLM 解读（便宜，但预算紧时可跳过）
        if self.budget.can_call_llm(0.02):
            try:
                a, c = self._chat(build_analyze_messages(meta.hypothesis, digest), self.lcfg["temperature_analyze"],
                                  self.lcfg["max_tokens_analyze"], "analyze", cid)
                meta.analysis = a.strip()
                meta.llm_cost_cny += c
            except LLMError as e:
                self.log(f"  analyze skipped: {e}")
        self.store.update(meta, "evaluated")
        self.selector.record_outcome(meta)
        self.log(f"  {cid} {meta.status} total={meta.score_total} robust={meta.score_robust}\n{digest}")
        return meta

    # ---------- run ----------
    def run(self, max_rounds: Optional[int] = None) -> Optional[CandidateMeta]:
        self.init_baseline()
        idx = 0
        while self.budget.can_evaluate() and (max_rounds is None or idx < max_rounds):
            idx += 1
            try:
                self.round(idx)
            except LLMError as e:
                self.log(f"  LLM error: {e}; stopping")
                break
        best = self.selector.best()
        self.finalize(best)
        return best

    def finalize(self, best: Optional[CandidateMeta]) -> None:
        self.log("=== done | " + self.budget.summary())
        tree = self.store.export_tree_text()
        (self.run_dir / "lineage_tree.txt").write_text(tree, encoding="utf-8")
        self.log("lineage:\n" + tree)
        if best:
            out = WS / "algorithms" / self.task
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.store.dir(best.id) / "candidate.patch", out / "candidate.patch")
            (out / "SELECTED.json").write_text(json.dumps(vars(best), indent=1, ensure_ascii=False), encoding="utf-8")
            self.log(f"best = {best.id} total={best.score_total:+.3f} -> {out / 'candidate.patch'}")
        else:
            self.log("no valid candidate produced")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(WS / "config" / "harness.yaml"))
    ap.add_argument("--llm-mode", default="live", choices=["live", "record", "replay"])
    ap.add_argument("--max-rounds", type=int, default=None)
    ap.add_argument("--task", default=None)
    args = ap.parse_args(argv)
    load_dotenv(WS / ".env")
    cfg = load_config(Path(args.config))
    if args.task:
        cfg["task"] = args.task
        cfg["gate"]["whitelist_file"] = f"config/whitelist_{args.task}.yaml"
    h = Harness(cfg, llm_mode=args.llm_mode)
    h.run(max_rounds=args.max_rounds)


if __name__ == "__main__":
    main()
