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
from .codebase import Codebase, apply_edits, git_diff, patch_changed_lines
from .evaluator import EvalResult, Evaluator, MockEvaluator, OfficialEvaluator
from .gate import Gate
from .llm import LLMClient, LLMError
from .proposer import Proposer, parse_proposal
from .scoring import RunMetrics, candidate_score
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
                                 s["max_consecutive_failures_per_lineage"], s.get("phase_plan"), seed=cfg.get("seed", 0),
                                 overflow_hard_limit=(cfg["evaluator"].get("overflow_threshold", 300) if self.full_flow else None))
        self.proposer = Proposer(self.cb, self.task)
        # 费用自适应：上一轮超过 per_round_cap 就把代码片段预算缩小
        self.code_chars = int(cfg["llm"].get("code_chars", 6000))
        self.code_chars_min = 2500

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
    def _prepare_work(self, cid: str, parent_id: Optional[str]) -> Path:
        """工作副本：从父代工作目录整体复制（父代 patch 已落在里面），否则空目录按需从 baseline 拷文件。"""
        work = self.run_dir / "work" / cid
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        if parent_id and (self.run_dir / "work" / parent_id).exists():
            shutil.copytree(self.run_dir / "work" / parent_id, work, dirs_exist_ok=True)
            (work / "candidate.patch").unlink(missing_ok=True)
        return work

    def _apply_and_gate(self, prop, work: Path, parent_id: Optional[str]):
        """返回 (changed_files, errors, gate_report)。errors 非空表示解析/应用失败；gate_report 可能为 None。"""
        if prop.parse_errors:
            return {}, list(prop.parse_errors), None
        pre = self.proposer.precheck(prop)
        if pre:
            return {}, pre, None
        base_for_edit = work if parent_id else self.base_root
        changed, errs = apply_edits(base_for_edit, work, prop.edits, fallback_root=self.base_root)
        if errs:
            return changed, errs, None
        # 父代链上的所有改动文件都要进 patch（不只是本轮改的）
        all_changed = self._changed_files_in(work)
        patch = git_diff(self.base_root, work, all_changed) if all_changed else ""
        full = {f: (work / f).read_text(encoding="utf-8", errors="ignore") for f in all_changed}
        base_contents = {f: (self.base_root / f).read_text(encoding="utf-8", errors="ignore")
                         for f in all_changed if (self.base_root / f).exists()}
        rep = self.gate.check(full, patch, work, baseline_files=base_contents)
        return full, [], rep

    def _changed_files_in(self, work: Path) -> List[str]:
        out = []
        for p in work.rglob("*"):
            if p.is_file() and p.name != "candidate.patch":
                rel = str(p.relative_to(work)).replace("\\", "/")
                base = self.base_root / rel
                if not base.exists() or base.read_bytes() != p.read_bytes():
                    out.append(rel)
        return sorted(out)

    # probe 阶段按菜单轮换检索焦点，保证 6 次探针看的是不同代码区域（对应 docs/knob_catalog.md 与 prompts/phase_probe.md）
    PROBE_KEYWORDS = [
        ["net_weight", "timing", "criticality", "timing_opt"],          # P-L1
        ["edge_weight", "cut", "contrastive", "neighbors", "lam"],      # P-P2
        ["target_density", "density_weight", "padding", "utilization"],  # P-L3
        ["area_balance", "balance", "tolerance", "error"],              # P-P4
        ["wirelength", "hpwl", "gamma", "weighted_average"],            # P-L4/L5
        ["num_epochs", "learning_rate", "embedding_dim", "hidden_dim"],  # P-P8
    ]

    def round(self, idx: int) -> Optional[CandidateMeta]:
        evals_done = self.budget.state.evaluations
        phase = self.selector.phase_for(evals_done)
        parent_id = self.selector.choose_parent(phase)
        parent_patch = (self.store.dir(parent_id) / "candidate.patch").read_text(encoding="utf-8") if parent_id else None
        parent_meta = self.store.get(parent_id) if parent_id else None
        DEFAULT_KW = ["timing", "weight", "partition", "density", "wirelength", "slack"]
        if phase == "probe":
            keywords = self.PROBE_KEYWORDS[(idx - 1) % len(self.PROBE_KEYWORDS)]
        else:
            keywords = (parent_meta.tags if parent_meta and parent_meta.tags else DEFAULT_KW)
        # 关键字在当前代码库里没命中任何片段 -> 退回通用集合（避免 prompt 里没有代码可改）
        if not self.cb.search(keywords, k=1):
            keywords = DEFAULT_KW
        cid = self.store.new_id()
        self.log(f"=== round {idx} | phase={phase} | parent={parent_id or 'base'} | code_chars={self.code_chars} | {self.budget.summary()}")

        # 有父代时，展示给模型的代码必须是父代工作副本（父代已改过的区域与基线不同，否则 SEARCH 必然对不上）
        if parent_id and (self.run_dir / "work" / parent_id).exists():
            self.proposer.cb = Codebase.overlay(self.cb, self.run_dir / "work" / parent_id)
        else:
            self.proposer.cb = self.cb

        # ① 提出修改
        round_cost = 0.0
        boost = patch_changed_lines(parent_patch) if parent_patch else None
        msgs = self.proposer.build_messages(phase, self.profile, self.selector.history_digest(), parent_patch, keywords,
                                            code_chars=self.code_chars, boost_lines=boost)
        prompt_chars = sum(len(m["content"]) for m in msgs)
        text, c = self._chat(msgs, self.lcfg["temperature_propose"], self.lcfg["max_tokens_propose"], "propose")
        round_cost += c
        prop = parse_proposal(text)

        # ② 生成代码 + ③ 检查（最多 1 次修复）
        work = self._prepare_work(cid, parent_id)
        changed, errs, rep = self._apply_and_gate(prop, work, parent_id)
        if errs or (rep and not rep.ok):
            fb = "\n".join(errs) if errs else rep.text()
            self.log(f"  gate/apply failed -> fix attempt: {fb[:200]}")
            msgs2 = self.proposer.build_messages(phase, self.profile, self.selector.history_digest(), parent_patch, keywords,
                                                 feedback_for_fix=fb, previous_response=text, code_chars=self.code_chars,
                                                 boost_lines=boost)
            text, c = self._chat(msgs2, self.lcfg["temperature_fix"], self.lcfg["max_tokens_fix"], "fix")
            round_cost += c
            prop = parse_proposal(text)
            work = self._prepare_work(cid, parent_id)
            changed, errs, rep = self._apply_and_gate(prop, work, parent_id)

        all_changed = self._changed_files_in(work)
        patch = git_diff(self.base_root, work, all_changed) if all_changed else ""
        meta = CandidateMeta(cid, parent_id, prop.hypothesis or "(unparsed)", prop.expected, all_changed, phase,
                             tags=prop.keywords[:8], llm_cost_cny=round_cost, prompt_chars=prompt_chars)
        self.store.create(meta, patch)
        self.store.save_llm(cid, "propose_final", msgs, text, {"cost_cny": round_cost})
        (work / "candidate.patch").write_text(patch, encoding="utf-8")
        self._adapt_code_chars(round_cost)

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
        try:
            res = self.evaluator.evaluate(cid, work)
        except Exception as e:  # 评估器自身炸了：记为无效，不吞掉预算之外的东西
            self.log(f"  evaluator raised: {e!r}")
            res = EvalResult(cid, {k: RunMetrics(r.case, r.seed, 0, 0, valid=False, fail_reason=f"evaluator error: {e!r}"[:120])
                                   for k, r in self.baseline_eval.runs.items()})
        self.budget.charge_evaluation()
        self.store.save_eval(cid, res.to_dict())
        meta.eval_wall_s = res.wall_s

        # ⑤ 分析反馈
        score = candidate_score(self.baseline_eval.runs, res.runs, self.full_flow)
        digest = rule_digest(self.baseline_eval, res, score)
        meta.all_valid = score.all_valid
        meta.per_case = score.per_case
        meta.fail_reasons = [f"{k}:{r.fail_reason}" for k, r in res.runs.items() if not r.valid][:6]
        if self.full_flow:
            ovs = [r.overflow for r in res.runs.values() if r.valid]
            meta.max_overflow = max(ovs) if ovs else None
        if score.all_valid:
            meta.status = "evaluated"
            meta.score_total = score.total
            meta.score_robust = {"mean": score.robust_mean, "min": score.robust_min,
                                 "mean_minus_std": score.robust_mean_minus_std}[self.selector.robustness]
        else:
            meta.status = "invalid"  # 题面：任一 run 无效则候选无效
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

    def _adapt_code_chars(self, round_cost: float) -> None:
        """单轮费用超过软上限 -> 下一轮缩小代码片段；远低于上限 -> 慢慢放回。"""
        if round_cost > self.per_round_cap:
            self.code_chars = max(self.code_chars_min, int(self.code_chars * 0.7))
            self.log(f"  round cost {round_cost:.3f} > cap {self.per_round_cap}; code_chars -> {self.code_chars}")
        elif round_cost < self.per_round_cap * 0.3 and self.code_chars < 6000:
            self.code_chars = min(6000, int(self.code_chars * 1.15))

    # ---------- run ----------
    def run(self, max_rounds: Optional[int] = None) -> Optional[CandidateMeta]:
        self.init_baseline()
        idx = 0
        consecutive_errors = 0
        while self.budget.can_evaluate() and (max_rounds is None or idx < max_rounds):
            idx += 1
            try:
                self.round(idx)
                consecutive_errors = 0
            except LLMError as e:
                consecutive_errors += 1
                self.log(f"  LLM error ({consecutive_errors}): {e}")
                if "budget exhausted" in str(e) or consecutive_errors >= 3:
                    self.log("  stopping")
                    break
                time.sleep(5 * consecutive_errors)
            except Exception as e:  # 单轮任何未预期错误都不该终止整个演化
                consecutive_errors += 1
                import traceback
                self.log(f"  round {idx} crashed: {e!r}\n{traceback.format_exc()[-800:]}")
                if consecutive_errors >= 3:
                    self.log("  too many consecutive crashes; stopping")
                    break
        best = self.selector.best()
        self.finalize(best)
        return best

    def finalize(self, best: Optional[CandidateMeta]) -> None:
        self.log("=== done | " + self.budget.summary())
        tree = self.store.export_tree_text()
        (self.run_dir / "lineage_tree.txt").write_text(tree, encoding="utf-8")
        self.log("lineage:\n" + tree)
        summary = {
            "task": self.task, "run_dir": str(self.run_dir), "budget": self.budget.state.to_dict(),
            "n_candidates": len(self.store.all()),
            "n_evaluated": len(self.store.evaluated()),
            "n_gate_failed": sum(1 for m in self.store.all() if m.status == "gate_failed"),
            "n_invalid": sum(1 for m in self.store.all() if m.status == "invalid"),
            "best": vars(best) if best else None,
        }
        (self.run_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        if best:
            out = WS / "algorithms" / self.task
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.store.dir(best.id) / "candidate.patch", out / "candidate.patch")
            (out / "SELECTED.json").write_text(json.dumps(vars(best), indent=1, ensure_ascii=False), encoding="utf-8")
            self.log(f"best = {best.id} total={best.score_total:+.3f} -> {out / 'candidate.patch'}")
        else:
            self.log("no valid candidate produced")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Agent harness: 提出修改→生成代码→检查→评估→分析→选择")
    ap.add_argument("--config", default=str(WS / "config" / "harness.yaml"))
    ap.add_argument("--task", default=None, choices=[None, "partition_placement", "full_flow"])
    ap.add_argument("--llm-mode", default="live", choices=["live", "record", "replay"])
    ap.add_argument("--max-rounds", type=int, default=None, help="最多跑多少轮（含 gate_failed 的轮）")
    ap.add_argument("--max-evaluations", type=int, default=None, help="覆盖 budget.max_evaluations（题面：读取指定预算）")
    ap.add_argument("--max-cost", type=float, default=None, help="覆盖 budget.max_cost_cny")
    ap.add_argument("--evaluator", default=None, choices=[None, "mock", "official", "logdir"])
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)
    load_dotenv(WS / ".env")
    cfg = load_config(Path(args.config))
    if args.task:
        cfg["task"] = args.task
        cfg["gate"]["whitelist_file"] = f"config/whitelist_{args.task}.yaml"
    if args.max_evaluations is not None:
        cfg["budget"]["max_evaluations"] = args.max_evaluations
    if args.max_cost is not None:
        cfg["budget"]["max_cost_cny"] = args.max_cost
    if args.evaluator:
        cfg["evaluator"]["mode"] = args.evaluator
    if args.seed is not None:
        cfg["seed"] = args.seed
    h = Harness(cfg, llm_mode=args.llm_mode, run_dir=Path(args.run_dir) if args.run_dir else None)
    h.run(max_rounds=args.max_rounds)


if __name__ == "__main__":
    main()
