"""
loop.py -- 演化主循环：提出修改 → 生成代码 → 检查代码 → 提交评估 → 分析反馈 → 选择方案。

用法:
    python -m harness.loop --task partition_placement [--evaluator official|logdir|mock]
                           [--max-evaluations 30 --max-cost 15] [--llm-mode live|record|replay]
                           [--run-dir DIR] [--max-rounds N]

预算语义（题面 §十一(二)）：max_evaluations 次候选评估、max_cost_cny 元 API 费，任一触顶即停。
本文件保证：evaluator.evaluate() 与 budget.charge_evaluation() 严格配对；所有 LLM 调用经 _chat 计费；
每个候选在拿到 id 的瞬间就落 meta.json 与血统事件（即使之后崩溃）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .analyzer import build_analyze_messages, rule_digest
from .budget import Budget, Pricing
from .codebase import Codebase, apply_edits, git_diff, patch_changed_lines, patch_fingerprint
from .evaluator import EvalResult, Evaluator, MockEvaluator, OfficialEvaluator
from .gate import Gate
from .llm import LLMClient, LLMError
from .proposer import Proposer, parse_proposal
from .scoring import RunMetrics, candidate_score
from .selector import Selector
from .store import CandidateMeta, CandidateStore

WS = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


def _env_expand(v):
    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
        return os.environ.get(v[2:-1], "")
    return v


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(open(path, encoding="utf-8"))
    cfg["llm"]["model"] = _env_expand(cfg["llm"]["model"]) or "deepseek-flash"
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


def _sha256_tree(root: Path, rels: List[str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(rels):
        p = root / rel
        if p.exists():
            h.update(rel.encode()); h.update(b"\0"); h.update(p.read_bytes()); h.update(b"\0")
    return h.hexdigest()[:16]


def _git_head(path: Path) -> str:
    """向上找 git 仓库根（workspace/ 可能只是仓库的子目录）。"""
    for p in [path] + list(path.parents):
        if (p / ".git").exists():
            try:
                r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=p, capture_output=True, text=True, timeout=10)
                return r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                return ""
    return ""


class Harness:
    def __init__(self, cfg: dict, llm_mode: str = "live", run_dir: Optional[Path] = None):
        self.cfg = cfg
        self.task = cfg["task"]
        self.full_flow = self.task == "full_flow"
        self.run_dir = run_dir or (WS / "logs" / f"run_{self.task}_{time.strftime('%Y%m%d_%H%M%S')}")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_f = open(self.run_dir / "harness.log", "a", encoding="utf-8")

        # ---- 预算 ----
        pricing = Pricing(WS / "config" / "pricing.yaml")
        b = cfg["budget"]
        self.budget = Budget(b["max_evaluations"], b["max_cost_cny"], pricing,
                             state_file=self.run_dir / "budget.json",
                             reserve_evaluations=b.get("reserve_evaluations", 0))
        self.per_round_cap = b.get("per_round_cost_cap_cny", 0.35)
        self.max_consecutive_gate_failures = int(b.get("max_consecutive_gate_failures", 6))

        # ---- LLM ----
        l = cfg["llm"]
        self.llm = LLMClient(model=l["model"], timeout_s=l["timeout_s"], max_retries=l["max_retries"],
                             record_dir=WS / "logs" / "llm_records", mode=llm_mode)
        self.lcfg = l

        # ---- 代码库 / 门禁 ----
        wl_path = WS / cfg["gate"]["whitelist_file"]
        wl = yaml.safe_load(open(wl_path, encoding="utf-8"))
        self.base_root = Path(cfg.get("baseline_root") or (WS / "baseline"))
        self.cb = Codebase(self.base_root, wl["allowed"], wl.get("denied", []))
        e = cfg["evaluator"]
        self.gate = Gate(wl_path, cfg["gate"]["forbidden_patterns"],
                         cfg["gate"].get("run_pyflakes", True), cfg["gate"].get("smoke_test_cmd", ""),
                         case_names=list(e.get("cases", [])))
        self.gate.baseline_root = self.base_root

        # ---- 评估器（fail-fast：非 mock 必须有命令） ----
        mode = e["mode"]
        if mode == "mock":
            self.evaluator: Evaluator = MockEvaluator(e["cases"], e["seeds"], full_flow=self.full_flow)
        elif mode in ("official", "logdir"):
            if not str(e.get("official_cmd", "")).strip():
                raise ConfigError(f"evaluator.mode={mode} but evaluator.official_cmd is empty — refuse to burn evaluations")
            if mode == "logdir":
                from .evaluator import LogDirEvaluator
                self.evaluator = LogDirEvaluator(e["cases"], e["seeds"], e["official_cmd"],
                                                 WS / "results" / self.task, WS / "baseline" / "results" / self.task,
                                                 full_flow=self.full_flow)
            else:
                self.evaluator = OfficialEvaluator(e["cases"], e["seeds"], e["official_cmd"], WS / "baseline" / "baseline_eval.json")
        else:
            raise ConfigError(f"unknown evaluator.mode {mode!r}")

        # ---- git 可用性（git_diff 依赖；无则 codebase 走 difflib 兜底，这里只记日志） ----
        self.have_git = shutil.which("git") is not None

        # ---- 历史 / 选择 ----
        self.store = CandidateStore(self.run_dir / "candidates")
        s = cfg["search"]
        self.selector = Selector(self.store, s["elite_size"], s["robustness_metric"],
                                 s["max_consecutive_failures_per_lineage"], s.get("phase_plan"), seed=cfg.get("seed", 0),
                                 overflow_hard_limit=(e.get("overflow_threshold", 300) if self.full_flow else None))
        if sum(self.selector.phase_plan.values()) + self.budget.reserve_evaluations != self.budget.max_evaluations:
            self.log(f"WARNING phase_plan sum {sum(self.selector.phase_plan.values())} + reserve {self.budget.reserve_evaluations} "
                     f"!= max_evaluations {self.budget.max_evaluations}; phases will be rescaled")
            self.selector.rescale_phase_plan(self.budget.max_evaluations - self.budget.reserve_evaluations)
        self.proposer = Proposer(self.cb, self.task)
        self.code_chars = int(cfg["llm"].get("code_chars", 6000))
        self.code_chars_min = 2500

        self.baseline_eval: Optional[EvalResult] = None
        self.profile: str = ""
        self._patch_hashes: Dict[str, str] = {}   # sha -> candidate id（去重）
        for m in self.store.all():
            p = self.store.dir(m.id) / "candidate.patch"
            if p.exists() and m.status in ("evaluated", "invalid") and p.stat().st_size > 0:
                self._patch_hashes.setdefault(patch_fingerprint(p.read_text(encoding="utf-8")), m.id)

    # ---------- util ----------
    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        try:
            self.log_f.write(line + "\n")
            self.log_f.flush()
        except Exception:
            pass

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
            lines.append("Notes: TNS weight 0.6; prefer mechanisms that generalize across all cases.")
            self.profile = "\n".join(lines)
        self.log("baseline loaded\n" + self.profile)

    # ---------- work dir ----------
    def _prepare_work(self, cid: str, parent_id: Optional[str]) -> Path:
        """工作副本 = 父代 patch 应用到干净基线文件上（只含白名单内被改过的文件）。
        不再整体复制父代目录（那会把评估器产物 / __pycache__ 一并继承）。父代 work 缺失也没关系，patch 是真相。"""
        work = self.run_dir / "work" / cid
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        if parent_id:
            patch = self.store.dir(parent_id) / "candidate.patch"
            if not patch.exists():
                raise RuntimeError(f"parent {parent_id} has no candidate.patch; lineage would be wrong")
            files = patch_changed_lines(patch.read_text(encoding="utf-8")).keys()
            for rel in files:
                src = self.base_root / rel
                if src.exists():
                    (work / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, work / rel)
            if patch.stat().st_size > 0:
                self._apply_patch(patch, work, parent_id)
        return work

    def _apply_patch(self, patch: Path, work: Path, parent_id: str) -> None:
        """用 git apply 把父代 patch 打到 work；失败则回退到复制父代 work（并只取白名单文件）。"""
        ok = False
        if self.have_git:
            r = subprocess.run(["git", "apply", "--unsafe-paths", "--directory", str(work), str(patch)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(work))
            ok = r.returncode == 0
            if not ok:
                self.log(f"  git apply of parent patch failed ({r.stderr.strip()[:120]}); falling back to parent work copy")
        if not ok:
            pw = self.run_dir / "work" / parent_id
            if not pw.exists():
                raise RuntimeError(f"cannot rebuild parent {parent_id}: patch does not apply and work dir missing")
            for p in pw.rglob("*"):
                if p.is_file():
                    rel = str(p.relative_to(pw)).replace("\\", "/")
                    if self.cb._is_allowed(rel):
                        (work / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, work / rel)

    def _changed_files_in(self, work: Path) -> List[str]:
        """work 里与基线不同的**白名单**文件（评估器产物、缓存、日志一律忽略）。"""
        out = []
        for p in work.rglob("*"):
            if not p.is_file():
                continue
            rel = str(p.relative_to(work)).replace("\\", "/")
            if rel == "candidate.patch" or not self.cb._is_allowed(rel):
                continue
            base = self.base_root / rel
            if not base.exists() or base.read_bytes() != p.read_bytes():
                out.append(rel)
        return sorted(out)

    def _apply_and_gate(self, prop, work: Path, parent_id: Optional[str], shown_chunks):
        """返回 (changed_files, errors, gate_report, patch)。errors 非空表示解析/应用失败。"""
        if prop.parse_errors:
            return {}, list(prop.parse_errors), None, ""
        pre = self.proposer.precheck(prop, shown_chunks)
        if pre:
            return {}, pre, None, ""
        base_for_edit = work if parent_id else self.base_root
        changed, errs = apply_edits(base_for_edit, work, prop.edits, fallback_root=self.base_root)
        if errs:
            return changed, errs, None, ""
        all_changed = self._changed_files_in(work)
        patch = git_diff(self.base_root, work, all_changed) if all_changed else ""
        full = {f: (work / f).read_text(encoding="utf-8", errors="ignore") for f in all_changed}
        base_contents = {f: (self.base_root / f).read_text(encoding="utf-8", errors="ignore")
                         for f in all_changed if (self.base_root / f).exists()}
        rep = self.gate.check(full, patch, work, baseline_files=base_contents)
        # 去重 / 无效改动
        if rep.ok:
            parent_patch_text = (self.store.dir(parent_id) / "candidate.patch").read_text(encoding="utf-8") if parent_id else ""
            if not patch.strip() or patch == parent_patch_text:
                rep.errors.append("no effective change relative to parent/baseline (REPLACE equals SEARCH or edit reverted)")
                rep.ok = False
            else:
                h = patch_fingerprint(patch)
                if h in self._patch_hashes:
                    rep.errors.append(f"identical patch already evaluated as {self._patch_hashes[h]}; propose something different")
                    rep.ok = False
        return full, [], rep, patch

    # probe 阶段按菜单轮换检索焦点（对应 docs/knob_catalog.md 与 prompts/phase_probe*.md）
    PROBE_KEYWORDS_PP = [
        ["net_weight", "timing", "criticality", "timing_opt"],
        ["edge_weight", "cut", "contrastive", "neighbors", "lam"],
        ["target_density", "density_weight", "padding", "utilization"],
        ["area_balance", "balance", "tolerance", "error"],
        ["wirelength", "hpwl", "gamma", "weighted_average"],
        ["num_epochs", "learning_rate", "embedding_dim", "hidden_dim"],
    ]
    # full_flow：六环节轮换，Tcl/mk 旋钮用它们在脚本里的真实标识符检索
    PROBE_KEYWORDS_FF = [
        ["repair_timing", "repair_tns", "setup_margin", "TNS_END_PERCENT", "skip_pin_swap"],       # 时序优化
        ["clock_tree_synthesis", "sink_clustering", "CTS_CLUSTER", "balance_levels", "root_buf"],  # CTS
        ["global_route", "congestion_iterations", "critical_nets_percentage", "layer_adjustment", "GLOBAL_ROUTE_ARGS"],  # GRT
        ["net_weight", "timing", "criticality", "timing_opt"],                                       # 布局
        ["edge_weight", "cut", "contrastive", "balance", "lam"],                                     # 切分
        ["repair_design", "max_wire_length", "slew_margin", "cap_margin", "set_dont_use", "DONT_USE"],  # DRV / 单元集
        ["detailed_placement", "max_displacement", "hbt", "terminal", "pitch"],                      # 合法化 / HBT
    ]
    DEFAULT_KW_PP = ["timing", "weight", "partition", "density", "wirelength", "slack"]
    DEFAULT_KW_FF = ["timing", "repair", "clock", "route", "congestion", "weight"]

    @property
    def PROBE_KEYWORDS(self):
        return self.PROBE_KEYWORDS_FF if self.full_flow else self.PROBE_KEYWORDS_PP

    @property
    def DEFAULT_KW(self):
        return self.DEFAULT_KW_FF if self.full_flow else self.DEFAULT_KW_PP

    # ---------- one round ----------
    def round(self, idx: int) -> Optional[CandidateMeta]:
        evals_done = self.budget.state.evaluations
        phase = self.selector.phase_for(evals_done)
        parent_id = self.selector.choose_parent(phase)
        parent_patch = (self.store.dir(parent_id) / "candidate.patch").read_text(encoding="utf-8") if parent_id else None
        parent_meta = self.store.get(parent_id) if parent_id else None
        if phase == "probe":
            keywords = self.PROBE_KEYWORDS[(idx - 1) % len(self.PROBE_KEYWORDS)]
        else:
            keywords = (parent_meta.tags if parent_meta and parent_meta.tags else self.DEFAULT_KW)
        if not self.cb.search(keywords, k=1):
            keywords = self.DEFAULT_KW

        # ---- 候选先登记（规则 f：任何之后的崩溃都有 meta.json 与血统事件） ----
        cid = self.store.new_id()
        meta = CandidateMeta(cid, parent_id, "(proposing)", "", [], phase)
        self.store.create(meta, "")
        self.log(f"=== round {idx} | {cid} | phase={phase} | parent={parent_id or 'base'} | code_chars={self.code_chars} | {self.budget.summary()}")

        # 工作副本（父代 patch 打到干净基线上）
        work = self._prepare_work(cid, parent_id)
        self.proposer.cb = Codebase.overlay(self.cb, work) if parent_id else self.cb

        round_cost = 0.0
        try:
            # ① 提出修改
            boost = patch_changed_lines(parent_patch) if parent_patch else None
            msgs, shown = self.proposer.build_messages(phase, self.profile, self.selector.history_digest(), parent_patch, keywords,
                                                      code_chars=self.code_chars, boost_lines=boost)
            meta.prompt_chars = sum(len(m["content"]) for m in msgs)
            text, c = self._chat(msgs, self.lcfg["temperature_propose"], self.lcfg["max_tokens_propose"], "propose", cid)
            round_cost += c
            prop = parse_proposal(text)
            meta.hypothesis = prop.hypothesis or "(unparsed)"; meta.expected = prop.expected; meta.tags = prop.keywords[:8]
            self.store.update(meta, "proposed")

            # ② 生成代码 + ③ 检查（最多 1 次修复）
            changed, errs, rep, patch = self._apply_and_gate(prop, work, parent_id, shown)
            if errs or (rep and not rep.ok):
                fb = "\n".join(errs) if errs else rep.text()
                self.log(f"  gate/apply failed -> fix attempt: {fb[:200]}")
                msgs2, shown2 = self.proposer.build_messages(phase, self.profile, self.selector.history_digest(), parent_patch, keywords,
                                                            feedback_for_fix=fb, previous_response=text, code_chars=self.code_chars,
                                                            boost_lines=boost)
                text, c = self._chat(msgs2, self.lcfg["temperature_fix"], self.lcfg["max_tokens_fix"], "fix", cid)
                round_cost += c
                prop = parse_proposal(text)
                if prop.hypothesis:
                    meta.hypothesis = prop.hypothesis; meta.expected = prop.expected; meta.tags = prop.keywords[:8]
                work = self._prepare_work(cid, parent_id)
                changed, errs, rep, patch = self._apply_and_gate(prop, work, parent_id, shown2)
        finally:
            meta.llm_cost_cny += round_cost
            self._adapt_code_chars(round_cost)

        meta.files_changed = self._changed_files_in(work)
        self.store.write_patch(cid, patch)
        (work / "candidate.patch").write_text(patch, encoding="utf-8")

        if errs or (rep and not rep.ok):
            meta.status = "gate_failed"
            meta.gate_report = "\n".join(errs) if errs else rep.text()
            meta.fail_reasons = [meta.gate_report[:200]]
            self.store.update(meta, "gate_failed")
            self.selector.record_outcome(meta)   # gate_failed 不影响父代（selector 内部跳过）
            self.log(f"  {cid} gate_failed (no evaluation consumed): {meta.gate_report[:120]}")
            return meta
        meta.gate_report = rep.text() if rep else "OK"
        self._patch_hashes[patch_fingerprint(patch)] = cid

        # ④ 提交评估（final 阶段允许动用 reserve）
        if not self.budget.can_evaluate(keep_reserve=(self.selector.force_phase != "final")):
            self.log("  evaluation budget exhausted; candidate discarded")
            meta.status = "discarded"; self.store.update(meta, "no_budget")
            return None
        try:
            try:
                res = self.evaluator.evaluate(cid, work)
            except Exception as ex:  # 评估器自身炸了：记为无效，评估次数仍计（保守）
                self.log(f"  evaluator raised: {ex!r}")
                res = EvalResult(cid, {k: RunMetrics(r.case, r.seed, 0, 0, valid=False, fail_reason=f"evaluator error: {ex!r}"[:120])
                                       for k, r in self.baseline_eval.runs.items()})
            self.budget.charge_evaluation()
            self.store.save_eval(cid, res.to_dict())
            meta.eval_wall_s = res.wall_s

            # ⑤ 分析反馈
            score = candidate_score(self.baseline_eval.runs, res.runs, self.full_flow)
            digest = rule_digest(self.baseline_eval, res, score)
            meta.all_valid = bool(score.all_valid and res.all_valid)
            meta.per_case = score.per_case
            meta.fail_reasons = [f"{k}:{r.fail_reason}" for k, r in res.runs.items() if not r.valid][:6]
            if self.full_flow:
                ovs = [r.overflow for r in res.runs.values() if r.valid]
                meta.max_overflow = max(ovs) if ovs else None
            if meta.all_valid:
                meta.status = "evaluated"
                meta.score_total = score.total
                meta.score_robust = {"mean": score.robust_mean, "min": score.robust_min,
                                     "mean_minus_std": score.robust_mean_minus_std}.get(self.selector.robustness, score.robust_mean_minus_std)
            else:
                meta.status = "invalid"
            if self.budget.can_call_llm(0.02):
                try:
                    a, c = self._chat(build_analyze_messages(meta.hypothesis, digest), self.lcfg["temperature_analyze"],
                                      self.lcfg["max_tokens_analyze"], "analyze", cid)
                    meta.analysis = a.strip()
                    meta.llm_cost_cny += c
                except LLMError as ex:
                    self.log(f"  analyze skipped: {ex}")
        finally:
            if meta.status == "proposed":
                meta.status = "invalid"
                meta.fail_reasons = meta.fail_reasons or ["harness error during evaluation/analysis"]
            self.store.update(meta, "evaluated")
        self.selector.record_outcome(meta)
        self.log(f"  {cid} {meta.status} total={meta.score_total} robust={meta.score_robust}\n{digest}")
        return meta

    def _adapt_code_chars(self, round_cost: float) -> None:
        if round_cost > self.per_round_cap:
            self.code_chars = max(self.code_chars_min, int(self.code_chars * 0.7))
            self.log(f"  round cost {round_cost:.3f} > cap {self.per_round_cap}; code_chars -> {self.code_chars}")
        elif round_cost < self.per_round_cap * 0.3 and self.code_chars < 6000:
            self.code_chars = min(6000, int(self.code_chars * 1.15))

    # ---------- run ----------
    def run(self, max_rounds: Optional[int] = None) -> Optional[CandidateMeta]:
        if max_rounds is None:
            max_rounds = 3 * self.budget.max_evaluations     # 防止模型持续违反协议时无限烧 LLM 费
        best = None
        try:
            self.init_baseline()
            idx = len(self.store.all())
            consecutive_errors = 0
            consecutive_gate = 0
            while self.budget.can_evaluate() and idx < max_rounds:
                idx += 1
                try:
                    m = self.round(idx)
                    consecutive_errors = 0
                    consecutive_gate = consecutive_gate + 1 if (m is not None and m.status == "gate_failed") else 0
                    if consecutive_gate >= self.max_consecutive_gate_failures:
                        self.log(f"  {consecutive_gate} consecutive gate failures; stopping to protect cost budget")
                        break
                except LLMError as e:
                    consecutive_errors += 1
                    self.log(f"  LLM error ({consecutive_errors}): {e}")
                    if "budget exhausted" in str(e) or consecutive_errors >= 3:
                        self.log("  stopping")
                        break
                    time.sleep(5 * consecutive_errors)
                except Exception as e:
                    consecutive_errors += 1
                    import traceback
                    self.log(f"  round {idx} crashed: {e!r}\n{traceback.format_exc()[-800:]}")
                    if consecutive_errors >= 3:
                        self.log("  too many consecutive crashes; stopping")
                        break
            # ---- final 阶段：用 reserve 对最优候选做保守微调 ----
            if self.budget.reserve_evaluations > 0 and self.selector.best() is not None:
                self.log(f"=== final phase: {self.budget.reserve_evaluations} reserved evaluation(s) for conservative refinement of the best")
                self.selector.force_phase = "final"
                k = 0
                while self.budget.can_evaluate(keep_reserve=False) and k < 3 * self.budget.reserve_evaluations:
                    idx += 1; k += 1
                    try:
                        self.round(idx)
                    except Exception as e:
                        self.log(f"  final round crashed: {e!r}")
                        break
                self.selector.force_phase = None
            best = self.selector.best()
        except KeyboardInterrupt:
            self.log("interrupted; finalizing")
            best = self.selector.best()
        finally:
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
            "code_version": {
                "harness_git": _git_head(WS),
                "baseline_whitelist_sha256": _sha256_tree(self.base_root, self.cb.files),
                "model": self.lcfg["model"],
                "evaluator_mode": self.cfg["evaluator"]["mode"],
                "whitelist_file": self.cfg["gate"]["whitelist_file"],
            },
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
    ap.add_argument("--max-rounds", type=int, default=None, help="最多跑多少轮（含 gate_failed）；默认 3×max_evaluations")
    ap.add_argument("--max-evaluations", type=int, default=None, help="覆盖 budget.max_evaluations（题面：读取指定预算）")
    ap.add_argument("--max-cost", type=float, default=None, help="覆盖 budget.max_cost_cny")
    ap.add_argument("--evaluator", default=None, choices=[None, "mock", "official", "logdir"])
    ap.add_argument("--official-cmd", default=None, help="覆盖 evaluator.official_cmd（{workdir} {out} 占位）")
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
    if args.official_cmd:
        cfg["evaluator"]["official_cmd"] = args.official_cmd
    if args.seed is not None:
        cfg["seed"] = args.seed
    try:
        h = Harness(cfg, llm_mode=args.llm_mode, run_dir=Path(args.run_dir) if args.run_dir else None)
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    h.run(max_rounds=args.max_rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
