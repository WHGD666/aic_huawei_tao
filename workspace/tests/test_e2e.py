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
from tests.fake_llm import BAD_SEARCH, FIXED_SEARCH, FakeLLM, GOOD_PROPOSAL, SYNTAX_ERR, TOUCH_EVAL  # noqa: E402


def make_harness(tmp_path, script, max_evals=5, fail_rate=0.0):
    cfg = L.load_config(WS / "config" / "harness.yaml")
    cfg["budget"]["max_evaluations"] = max_evals
    cfg["budget"]["max_cost_cny"] = 5.0
    cfg["budget"]["reserve_evaluations"] = 0
    cfg["search"]["phase_plan"] = {"probe": 2, "structural": 2, "combine": 1}
    cfg["evaluator"]["mode"] = "mock"
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
    return h


def test_e2e_happy_path(tmp_path):
    h = make_harness(tmp_path, [GOOD_PROPOSAL] * 10, max_evals=3)
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
    assert "+TIMING_WEIGHT = 0.5" in patch
    assert (WS / "algorithms" / "partition_placement" / "candidate.patch").exists()
    # 费用记账在跑
    assert h.budget.state.llm_calls >= 3 and h.budget.state.cost_cny > 0


def test_e2e_fix_path_and_gate(tmp_path):
    # 第 1 轮：SEARCH 不匹配 -> fix -> 成功；第 2 轮：动禁区 -> fix 仍动禁区 -> gate_failed（不耗评估）
    #           第 3 轮：语法错 -> fix 成功
    script = [BAD_SEARCH, FIXED_SEARCH, TOUCH_EVAL, TOUCH_EVAL, SYNTAX_ERR, GOOD_PROPOSAL]
    h = make_harness(tmp_path, script, max_evals=2)
    h.run()
    metas = h.store.all()
    statuses = [m.status for m in metas]
    assert statuses[0] == "evaluated"
    assert statuses[1] == "gate_failed"
    assert statuses[2] == "evaluated"
    assert h.budget.state.evaluations == 2          # gate_failed 没有消耗评估
    tags = [c[0] for c in h.llm.calls]
    assert tags.count("fix") == 3


def test_e2e_invalid_results_not_selected(tmp_path):
    h = make_harness(tmp_path, [GOOD_PROPOSAL] * 5, max_evals=3, fail_rate=1.0)
    best = h.run()
    assert best is None
    assert all(m.status == "invalid" for m in h.store.all())
    assert all(m.fail_reasons for m in h.store.all())


def test_budget_stop_on_cost(tmp_path):
    h = make_harness(tmp_path, [GOOD_PROPOSAL] * 50, max_evals=50)
    h.llm.usage = Usage(2_000_000, 100, 0)   # 每次调用约 2 元，5 元预算 -> 2-3 轮就停
    h.run()
    assert h.budget.state.cost_cny <= 5.0 + 2.1
    assert h.budget.state.evaluations < 10
