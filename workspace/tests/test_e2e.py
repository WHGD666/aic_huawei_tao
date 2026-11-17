"""端到端：用 fake baseline + FakeLLM + MockEvaluator 把六步循环跑若干轮，验证 logs/ 产物与预算刹车。"""
import json
import shutil
import sys
from pathlib import Path

import pytest

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from harness import loop as L  # noqa: E402
from harness.budget import Usage  # noqa: E402
from tests.fake_llm import BAD_SEARCH, FIXED_SEARCH, FakeLLM, GOOD_PROPOSAL, SYNTAX_ERR, TOUCH_EVAL, varied  # noqa: E402


def make_harness(tmp_path, script, max_evals=5, fail_rate=0.0):
    cfg = L.load_config(WS / "config" / "harness.yaml")
    cfg["budget"]["max_evaluations"] = max_evals
    cfg["budget"]["max_cost_cny"] = 5.0
    cfg["budget"]["reserve_evaluations"] = 0
    probe = max(2, max_evals // 2)
    cfg["search"]["phase_plan"] = {"probe": probe, "structural": max(0, max_evals - probe)}
    cfg["evaluator"]["mode"] = "mock"
    cfg["gate"]["whitelist_file"] = "tests/whitelist_fake.yaml"
    h = L.Harness(cfg, llm_mode="replay", run_dir=tmp_path / "run")
    # 换成 fake 基线与 fake LLM
    h.base_root = WS / "tests" / "fake_baseline"
    from harness.codebase import Codebase
    import yaml
    wl = yaml.safe_load(open(WS / cfg["gate"]["whitelist_file"], encoding="utf-8"))
    h.cb = Codebase(h.base_root, wl["allowed"], wl.get("denied", []))
    from harness.proposer import Proposer
    h.proposer = Proposer(h.cb, h.task)
    h.llm = FakeLLM(script)
    h.evaluator.fail_rate = fail_rate
    h.evaluator.sporadic_rate = 0.0
    return h


def test_e2e_happy_path(tmp_path):
    h = make_harness(tmp_path, varied(2), max_evals=3)
    best = h.run()
    assert best is not None and best.status == "evaluated"
    assert h.budget.state.evaluations == 3
    run = tmp_path / "run"
    assert (run / "candidates" / "lineage.jsonl").exists()
    assert (run / "lineage_tree.txt").exists()
    meta = json.loads((run / "candidates" / best.id / "meta.json").read_text(encoding="utf-8"))
    assert meta["hypothesis"].startswith("Add timing")
    assert meta["analysis"]
    patch = (run / "candidates" / best.id / "candidate.patch").read_text(encoding="utf-8")
    assert "+TIMING_WEIGHT = 0." in patch
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["code_version"]["baseline_whitelist_sha256"]
    assert (WS / "algorithms" / "partition_placement" / "candidate.patch").exists()
    # 费用记账在跑
    assert h.budget.state.llm_calls >= 3 and h.budget.state.cost_cny > 0


def test_e2e_fix_path_and_gate(tmp_path):
    # 第 1 轮：SEARCH 不匹配 -> precheck 拦下 -> fix -> 成功
    # 第 2 轮：动禁区（未展示的文件）-> precheck 拦下 -> fix 仍动 -> gate_failed（不耗评估）
    # 第 3 轮：语法错 -> fix 给的是另一个未展示文件 -> 仍 gate_failed；第 4 轮：正常
    script = [BAD_SEARCH, FIXED_SEARCH, TOUCH_EVAL, TOUCH_EVAL, SYNTAX_ERR, TOUCH_EVAL, GOOD_PROPOSAL]
    h = make_harness(tmp_path, script, max_evals=2)
    h.run()
    metas = h.store.all()
    statuses = [m.status for m in metas]
    assert statuses[0] == "evaluated" and "NET_WEIGHT_DEFAULT = 2.0" in (h.store.dir(metas[0].id) / "candidate.patch").read_text()
    assert statuses[1] == "gate_failed" and "NOT shown" in metas[1].gate_report
    assert statuses[2] == "gate_failed" and "syntax" in metas[2].gate_report.lower() or "NOT shown" in metas[2].gate_report
    assert statuses[3] == "evaluated"
    assert h.budget.state.evaluations == 2          # gate_failed 没有消耗评估
    tags = [c[0] for c in h.llm.calls]
    assert tags.count("fix") == 3


def test_e2e_invalid_results_not_selected(tmp_path):
    h = make_harness(tmp_path, varied(2), max_evals=3, fail_rate=1.0)
    best = h.run()
    assert best is None
    assert all(m.status == "invalid" for m in h.store.all())
    assert all(m.fail_reasons for m in h.store.all())


def test_budget_stop_on_cost(tmp_path):
    h = make_harness(tmp_path, varied(2), max_evals=50)
    h.llm.usage = Usage(2_000_000, 100, 0)   # 每次调用约 2 元，5 元预算 -> 2-3 轮就停
    h.run()
    assert h.budget.state.cost_cny <= 5.0 + 2.1
    assert h.budget.state.evaluations < 10


def test_lineage_patch_accumulates(tmp_path):
    """structural 阶段的子代建立在父代工作副本上：patch 必须同时包含父代与本轮的改动。"""
    from tests.fake_llm import CHILD_PROPOSAL
    # probe 阶段 2 轮都从基线出发；第 3 轮（structural）从精英出发并在同一文件再改一处
    h = make_harness(tmp_path, [GOOD_PROPOSAL, GOOD_PROPOSAL, CHILD_PROPOSAL, CHILD_PROPOSAL], max_evals=4)
    h.run()
    metas = h.store.all()
    structural = [m for m in metas if m.phase in ("structural", "combine", "final") and m.parent]
    assert structural, "expected a child candidate built on an elite parent"
    child = structural[0]
    patch = (h.store.dir(child.id) / "candidate.patch").read_text(encoding="utf-8")
    assert "+TIMING_WEIGHT = 0.5" in patch          # 来自父代
    assert "+AREA_BALANCE_TOL = 0.08" in patch      # 本轮
    assert "-AREA_BALANCE_TOL = 0.05" in patch
    assert child.files_changed == ["partition/gnn_partition.py"]
    assert child.prompt_chars > 0


def test_full_flow_overflow_hard_limit(tmp_path):
    """full_flow：任一 run 溢出 > 300 的候选不得进入精英集 / 不得被选为 best。"""
    import harness.loop as L
    cfg = L.load_config(WS / "config" / "harness.yaml")
    cfg["task"] = "full_flow"
    cfg["budget"].update(max_evaluations=4, max_cost_cny=5.0, reserve_evaluations=0)
    cfg["search"]["phase_plan"] = {"probe": 4}
    cfg["evaluator"]["mode"] = "mock"
    cfg["gate"]["whitelist_file"] = "tests/whitelist_fake.yaml"
    h = L.Harness(cfg, llm_mode="replay", run_dir=tmp_path / "run")
    h.base_root = WS / "tests" / "fake_baseline"
    import yaml
    from harness.codebase import Codebase
    from harness.proposer import Proposer
    wl = yaml.safe_load(open(WS / "tests" / "whitelist_fake.yaml", encoding="utf-8"))
    h.cb = Codebase(h.base_root, wl["allowed"], wl.get("denied", []))
    h.proposer = Proposer(h.cb, "full_flow")
    # 让 mock 评估器把溢出推到 >300（patch 含 "buffer" 关键字 +80，再加基线 120 ≈ 200；强制覆盖）
    from harness.evaluator import MockEvaluator
    class HighOverflow(MockEvaluator):
        def evaluate(self, cid, workdir):
            r = super().evaluate(cid, workdir)
            for k, run in r.runs.items():
                run.overflow = 900.0
            return r
    h.evaluator = HighOverflow(cfg["evaluator"]["cases"], cfg["evaluator"]["seeds"], fail_rate=0.0, full_flow=True)
    h.llm = FakeLLM([GOOD_PROPOSAL] * 6)
    best = h.run()
    assert best is None                      # 全部被硬门槛挡在精英集外
    assert all(m.max_overflow == 900.0 for m in h.store.evaluated())
    assert h.selector.elites() == []


def test_gate_failed_does_not_blacklist_parent(tmp_path):
    """MED#5：子代 gate_failed 不应给父代记失败；父代仍可被选。"""
    from tests.fake_llm import CHILD_PROPOSAL, TOUCH_EVAL
    # 2 probes OK -> structural: 4 轮 gate_failed（TOUCH_EVAL 改未展示文件，fix 也一样）-> 之后 CHILD_PROPOSAL 仍能以 c001/c002 为父代
    script = varied(2) + [TOUCH_EVAL] * 8
    h = make_harness(tmp_path, script, max_evals=4)
    h.max_consecutive_gate_failures = 100
    h.run()
    metas = h.store.all()
    ok_children = [m for m in metas if m.parent and m.status in ("evaluated", "invalid")]
    assert ok_children, "a structural child built on an elite parent must still be possible after gate failures"
    gate_failed_children = [m for m in metas if m.parent and m.status == "gate_failed"]
    assert gate_failed_children
    # gate_failed 子代不计入父代 streak：streak 只可能来自真正评估过的子代
    n_eval_children = len(ok_children)
    assert sum(h.selector._fail_streak.values()) <= n_eval_children


def test_duplicate_patch_rejected(tmp_path):
    """MED#6：与已评估候选完全相同的 patch 不得再消耗评估。"""
    # 轮 1：GOOD 评估。轮 2：GOOD 与轮 1 相同 -> 去重 -> fix 仍给 GOOD -> gate_failed（0 评估）。
    # 轮 3：GOOD 相同 -> 去重 -> 脚本用尽，fix 自动给不同值 -> 评估。
    h = make_harness(tmp_path, [GOOD_PROPOSAL, GOOD_PROPOSAL, GOOD_PROPOSAL, GOOD_PROPOSAL], max_evals=3)
    h.run(max_rounds=3)
    metas = h.store.all()
    assert [m.status for m in metas] == ["evaluated", "gate_failed", "evaluated"], [m.status for m in metas]
    assert "identical patch" in metas[1].gate_report
    assert any("identical patch" in c[1][-1]["content"] for c in h.llm.calls if c[0] == "fix")
    assert h.budget.state.evaluations == 2


def test_meta_exists_even_if_llm_crashes(tmp_path):
    """MED#7：propose 阶段崩溃也要有 meta.json 与血统事件。"""
    h = make_harness(tmp_path, [GOOD_PROPOSAL], max_evals=2)
    class Boom:
        def chat(self, *a, **k):
            raise RuntimeError("network down")
    h.llm = Boom()
    h.run()
    metas = h.store.all()
    assert metas and (h.store.dir(metas[0].id) / "meta.json").exists()
    assert (h.store.root / "lineage.jsonl").read_text(encoding="utf-8").count('"create"') >= 1
    assert h.budget.state.evaluations == 0


def test_consecutive_gate_failures_stop(tmp_path):
    """MED#8：连续门禁失败达到上限就停，不再烧 LLM 费。"""
    from tests.fake_llm import TOUCH_EVAL
    h = make_harness(tmp_path, [TOUCH_EVAL] * 100, max_evals=10)
    h.max_consecutive_gate_failures = 4
    h.run()
    assert sum(1 for m in h.store.all() if m.status == "gate_failed") == 4
    assert h.budget.state.evaluations == 0


def test_reserve_used_in_final_phase(tmp_path):
    """HIGH#1：reserve 的评估在 final 阶段被用掉，phase 标为 final。"""
    from tests.fake_llm import CHILD_PROPOSAL
    import harness.loop as L
    cfg = L.load_config(WS / "config" / "harness.yaml")
    cfg["budget"].update(max_evaluations=4, max_cost_cny=5.0, reserve_evaluations=1)
    cfg["search"]["phase_plan"] = {"probe": 2, "structural": 1}
    cfg["evaluator"]["mode"] = "mock"
    cfg["gate"]["whitelist_file"] = "tests/whitelist_fake.yaml"
    h = L.Harness(cfg, llm_mode="replay", run_dir=tmp_path / "run")
    h.base_root = WS / "tests" / "fake_baseline"
    import yaml
    from harness.codebase import Codebase
    from harness.proposer import Proposer
    wl = yaml.safe_load(open(WS / "tests" / "whitelist_fake.yaml", encoding="utf-8"))
    h.cb = Codebase(h.base_root, wl["allowed"], wl.get("denied", []))
    h.proposer = Proposer(h.cb, h.task)
    h.evaluator.fail_rate = 0.0
    h.evaluator.sporadic_rate = 0.0
    h.llm = FakeLLM(varied(2))
    h.run()
    assert h.budget.state.evaluations == 4, [(m.id, m.phase, m.status, (m.gate_report or "")[:50]) for m in h.store.all()]
    phases = [m.phase for m in h.store.all() if m.status == "evaluated"]
    assert "final" in phases, phases


def test_config_error_on_empty_official_cmd(tmp_path):
    """HIGH#3：mode=official 且 cmd 为空必须在启动时报错。"""
    import harness.loop as L
    cfg = L.load_config(WS / "config" / "harness.yaml")
    cfg["evaluator"]["mode"] = "official"
    cfg["evaluator"]["official_cmd"] = ""
    cfg["gate"]["whitelist_file"] = "tests/whitelist_fake.yaml"
    with pytest.raises(L.ConfigError):
        L.Harness(cfg, llm_mode="replay", run_dir=tmp_path / "run")


def test_work_dir_ignores_evaluator_artifacts(tmp_path):
    """HIGH#2：评估器写进 work/ 的产物不能被当成候选改动继承。"""
    from tests.fake_llm import CHILD_PROPOSAL
    h = make_harness(tmp_path, varied(2), max_evals=3)
    orig = h.evaluator.evaluate
    def dirty(cid, workdir):
        (workdir / "eval_result.json").write_text("{}")
        (workdir / "__pycache__").mkdir(exist_ok=True)
        (workdir / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        return orig(cid, workdir)
    h.evaluator.evaluate = dirty
    h.run()
    child = [m for m in h.store.all() if m.parent and m.status == "evaluated"]
    assert child, [ (m.id, m.status, m.gate_report[:60]) for m in h.store.all()]
    assert all("eval_result" not in f and "__pycache__" not in f for f in child[0].files_changed)
