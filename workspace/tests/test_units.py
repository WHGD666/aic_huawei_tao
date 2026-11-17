import shutil
import sys
from pathlib import Path

import pytest

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from harness.budget import Budget, Pricing, Usage  # noqa: E402
from harness.codebase import Codebase, Edit, apply_edits, git_diff  # noqa: E402
from harness.evaluator import MockEvaluator  # noqa: E402
from harness.gate import Gate  # noqa: E402
from harness.proposer import parse_proposal  # noqa: E402
from harness.scoring import RunMetrics, candidate_score, improvement, overflow_penalty, run_score  # noqa: E402
from harness.store import CandidateMeta, CandidateStore  # noqa: E402
from harness.selector import Selector  # noqa: E402
from tests.fake_llm import BAD_SEARCH, GOOD_PROPOSAL, TOUCH_EVAL  # noqa: E402

FAKE = WS / "tests" / "fake_baseline"
WL = WS / "tests" / "whitelist_fake.yaml"


# ---------------- scoring ----------------
def test_improvement_formula():
    assert improvement(10, 5) == pytest.approx(0.5)
    assert improvement(10, 15) == pytest.approx(-0.5)
    assert improvement(0, 0) == 0.0
    assert improvement(0, 3) == -1.0


def test_overflow_penalty():
    assert overflow_penalty(0) == 0
    assert overflow_penalty(300) == 0
    assert overflow_penalty(600) == pytest.approx(0.1)
    assert overflow_penalty(1200) == pytest.approx(0.3)


def test_run_score_weights():
    b = RunMetrics("x", 1, -1.0, -100.0)
    c = RunMetrics("x", 1, -0.5, -50.0, overflow=900)
    assert run_score(b, c, full_flow=False) == pytest.approx(0.5)
    assert run_score(b, c, full_flow=True) == pytest.approx(0.5 - 0.2)
    assert run_score(b, RunMetrics("x", 1, 0, 0, valid=False), False) is None


def test_candidate_score_aggregation():
    base = {f"a/{s}": RunMetrics("a", s, -1, -100) for s in (1, 2, 3)}
    base.update({f"b/{s}": RunMetrics("b", s, -2, -200) for s in (1, 2, 3)})
    cand = {k: RunMetrics(v.case, v.seed, v.wns * 0.5, v.tns * 0.5) for k, v in base.items()}
    sc = candidate_score(base, cand, False)
    assert sc.all_valid
    assert sc.per_case["a"] == pytest.approx(0.5)
    assert sc.total == pytest.approx(1.0)  # 等权加和
    assert sc.robust_min == pytest.approx(0.5)
    # 任一 run 无效 -> 整个候选 -inf，不得被选中
    cand["b/2"] = RunMetrics("b", 2, 0, 0, valid=False, fail_reason="overlap")
    sc2 = candidate_score(base, cand, False)
    assert not sc2.all_valid and sc2.total == float("-inf") and sc2.robust_mean_minus_std == float("-inf")


# ---------------- budget ----------------
def test_budget_pricing_and_limits(tmp_path):
    p = Pricing(WS / "config" / "pricing.yaml")
    b = Budget(3, 1.0, p, tmp_path / "b.json", reserve_evaluations=1)
    assert b.can_evaluate()
    b.charge_evaluation(); b.charge_evaluation()
    assert not b.can_evaluate()            # 2 used, 1 reserved
    assert b.can_evaluate(keep_reserve=False)
    c = b.charge_llm("deepseek-chat", Usage(1_000_000, 0, 0))
    assert c == pytest.approx(1.0)
    assert not b.can_call_llm()
    # persisted
    b2 = Budget(3, 1.0, p, tmp_path / "b.json")
    assert b2.state.evaluations == 2 and b2.state.cost_cny == pytest.approx(1.0)


# ---------------- codebase / edits ----------------
def test_codebase_index_and_search():
    cb = Codebase(FAKE, ["partition/**/*.py", "placement/**/*.py", "config/*.yaml"], ["**/evaluate*"])
    assert "partition/gnn_partition.py" in cb.files
    assert "eval/evaluate.py" not in cb.files
    names = {c.name for c in cb.chunks}
    assert {"partition", "repair_balance", "net_weights"} <= names
    hit = cb.search(["balance"], k=3)
    assert any(c.name == "repair_balance" for c in hit)
    assert "gnn_partition.py" in cb.outline()


def test_codebase_real_baseline_chunking():
    """真实 Open3DBench 代码：大类按方法切、超大块按命中窗口截取、检索命中正确符号。"""
    import yaml
    base = WS / "baseline"
    if not (base / "Place-LoL" / "dreamplace" / "PlaceObj.py").exists():
        return  # baseline 未就绪时跳过
    wl = yaml.safe_load(open(WS / "config" / "whitelist_partition_placement.yaml", encoding="utf-8"))
    cb = Codebase(base, wl["allowed"], wl["denied"])
    assert "Place-LoL/dreamplace/PlaceObj.py" in cb.files
    assert "Place-LoL/dreamplace/PlaceDB.py" not in cb.files          # denied
    names = {c.name for c in cb.chunks}
    assert "PlaceObj.build_update_density_weight" in names             # 类方法级分块
    assert "TimingOpt.update_net_weights" in names
    hit = cb.search(["net_weight", "timing", "criticality"], k=6)
    assert any(c.name == "TimingOpt.update_net_weights" for c in hit)
    assert all(len(c.source) <= 2500 for c in hit)                     # 窗口截取生效
    assert sum(len(c.source) for c in hit) <= 6000
    exc = [c for c in hit if c.name.endswith("(excerpt)")]
    if exc:
        assert exc[0].start >= 1 and exc[0].end >= exc[0].start


def test_gate_mk_check():
    from harness.gate import _mk_check
    assert _mk_check("export A = 1\nB ?= x \\\n  y\n# c\n\ttab recipe\nifeq ($(A),1)\nendif\n") == ""
    assert "line 2" in _mk_check("export A = 1\nthis is junk\n")


def test_apply_edits_and_diff(tmp_path):
    work = tmp_path / "w"
    changed, errs = apply_edits(FAKE, work, [Edit("partition/gnn_partition.py", "TIMING_WEIGHT = 0.0", "TIMING_WEIGHT = 0.5")])
    assert not errs and "TIMING_WEIGHT = 0.5" in changed["partition/gnn_partition.py"]
    d = git_diff(FAKE, work, list(changed))
    assert "+TIMING_WEIGHT = 0.5" in d
    assert d.startswith("diff --git a/partition/gnn_partition.py b/partition/gnn_partition.py")
    assert "--- a/partition/gnn_partition.py" in d and "+++ b/partition/gnn_partition.py" in d
    # 提交要求：candidate.patch 必须能用 git apply 打到官方基线上
    import shutil, subprocess
    base_copy = tmp_path / "base"
    shutil.copytree(FAKE, base_copy)
    (tmp_path / "c.patch").write_text(d, encoding="utf-8")
    r = subprocess.run(["git", "apply", "--check", str(tmp_path / "c.patch")], cwd=base_copy, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["git", "apply", str(tmp_path / "c.patch")], cwd=base_copy, capture_output=True, text=True)
    assert r.returncode == 0 and "TIMING_WEIGHT = 0.5" in (base_copy / "partition/gnn_partition.py").read_text()
    _, errs = apply_edits(FAKE, work, [Edit("partition/gnn_partition.py", "NOPE", "x")])
    assert errs and "not found" in errs[0]


# ---------------- proposer parsing ----------------
def test_parse_proposal():
    p = parse_proposal(GOOD_PROPOSAL)
    assert not p.parse_errors
    assert p.hypothesis.startswith("Add timing")
    assert p.keywords[:2] == ["timing", "slack"]
    assert p.edits[0].file == "partition/gnn_partition.py"
    assert p.edits[0].search == "TIMING_WEIGHT = 0.0"
    bad = parse_proposal("random text")
    assert bad.parse_errors
    # 宽松解析：围栏包住、漏第二个 >>>、=== 带尾随空格、**HYPOTHESIS**: 加粗、file= 带反引号
    lenient = """**HYPOTHESIS**: x
EXPECTED: y
KEYWORDS: `a`, b
```text
<<<EDIT file=`partition/gnn_partition.py`
<<<SEARCH
TIMING_WEIGHT = 0.0
===
TIMING_WEIGHT = 0.9
>>>
```
"""
    q = parse_proposal(lenient)
    assert not q.parse_errors, q.parse_errors
    assert q.hypothesis == "x" and q.keywords == ["a", "b"]
    assert q.edits[0].file == "partition/gnn_partition.py" and q.edits[0].replace == "TIMING_WEIGHT = 0.9"
    malformed = parse_proposal("HYPOTHESIS: h\n<<<EDIT file=a.py\nno search marker\n>>>")
    assert any("malformed" in e for e in malformed.parse_errors)


# ---------------- gate ----------------
def test_gate_blocks_denied_and_syntax(tmp_path):
    g = Gate(WL, [r"\bscoring\b"], run_pyflakes=True)
    work = tmp_path / "w"
    p = parse_proposal(TOUCH_EVAL)
    changed, errs = apply_edits(FAKE, work, p.edits)
    assert not errs
    rep = g.check(changed, git_diff(FAKE, work, list(changed)))
    assert not rep.ok and any("whitelist" in e or "denied" in e for e in rep.errors)

    rep2 = g.check({"placement/place3d.py": "x = (1\n"}, "")
    assert not rep2.ok and any("syntax" in e for e in rep2.errors)

    rep3 = g.check({"placement/place3d.py": "def f():\n    return undefined_thing\n"}, "")
    assert not rep3.ok and any("undefined name" in e for e in rep3.errors)

    # 合法代码里的 model.eval() 不应被禁改模式误伤
    rep3b = g.check({"placement/place3d.py": "def f(model):\n    model.eval()\n    return model\n"}, "+    model.eval()\n")
    assert rep3b.ok and not rep3b.warnings

    # Tcl / yaml / json 的语法门禁（full_flow 白名单含 tcl）
    g2 = Gate(WL, [], run_pyflakes=False)
    bad_tcl = g2.check({"cts/cts.tcl": "proc f {} {\n  if {1} {\n    puts hi\n  \n}\n"}, "")
    assert not bad_tcl.ok and any("tcl" in e for e in bad_tcl.errors)
    ok_tcl = g2.check({"cts/cts.tcl": "# comment with {\nproc f {} {\n  set x [expr {1 + 2}]\n  puts \"\\{\"\n}\n"}, "")
    assert ok_tcl.ok
    bad_yaml = g2.check({"config/cts.yaml": "a: [1, 2\n"}, "")
    assert not bad_yaml.ok and any("yaml" in e for e in bad_yaml.errors)
    bad_json = g2.check({"config/grt.json": "{\"a\": 1,}"}, "")
    assert not bad_json.ok and any("json" in e for e in bad_json.errors)

    p = parse_proposal(GOOD_PROPOSAL)
    changed, errs = apply_edits(FAKE, tmp_path / "w2", p.edits)
    rep4 = g.check(changed, git_diff(FAKE, tmp_path / "w2", list(changed)))
    assert rep4.ok


# ---------------- store / selector ----------------
def test_store_lineage_and_selector(tmp_path):
    st = CandidateStore(tmp_path / "c")
    a = CandidateMeta("c001", None, "h1", "e", ["f"], "probe"); st.create(a, "patchA")
    a.status, a.score_total, a.score_robust, a.all_valid = "evaluated", 0.2, 0.15, True; st.update(a)
    b = CandidateMeta("c002", "c001", "h2", "e", ["f"], "structural"); st.create(b, "patchB")
    b.status, b.score_total, b.score_robust, b.all_valid = "evaluated", 0.1, 0.05, True; st.update(b)
    assert st.lineage_of("c002") == ["c001", "c002"]
    assert st.children("c001")[0].id == "c002"
    assert (tmp_path / "c" / "lineage.jsonl").exists()
    sel = Selector(st, elite_size=2, max_consecutive_failures=1, phase_plan={"probe": 1, "structural": 2, "combine": 1})
    assert sel.phase_for(0) == "probe" and sel.phase_for(1) == "structural" and sel.phase_for(3) == "combine" and sel.phase_for(4) == "final"
    assert sel.best().id == "c001"
    assert sel.choose_parent("probe") is None
    assert sel.choose_parent("final") == "c001"
    sel.record_outcome(b)                      # b worse than parent -> parent streak 1 -> excluded
    assert sel.choose_parent("final") == "c002"
    assert "c001" in sel.history_digest()
    # reload from disk
    st2 = CandidateStore(tmp_path / "c")
    assert st2.get("c002").parent == "c001"


# ---------------- mock evaluator ----------------
def test_mock_evaluator_deterministic(tmp_path):
    ev = MockEvaluator(["bp_fe-ng45", "bp-ng45"], [1001, 1002], fail_rate=0.0)
    (tmp_path / "candidate.patch").write_text("+TIMING_WEIGHT = 0.5")
    r1 = ev.evaluate("c1", tmp_path); r2 = ev.evaluate("c1", tmp_path)
    assert r1.runs["bp-ng45/1001"].tns == r2.runs["bp-ng45/1001"].tns
    assert len(r1.runs) == 4 and r1.all_valid
    base = ev.baseline()
    assert all(r.wns < 0 and r.tns < 0 for r in base.runs.values())


def test_patch_changed_lines_and_boost():
    from harness.codebase import patch_changed_lines
    patch = """diff --git a/x/a.py b/x/a.py
--- a/x/a.py
+++ b/x/a.py
@@ -10,4 +10,6 @@ def f():
     keep
-    old
+    new1
+    new2
     keep2
+    new3
"""
    cl = patch_changed_lines(patch)
    assert cl == {"x/a.py": [11, 12, 14]}
    # boost：命中父代改动行的块必须排第一，且窗口以它为中心
    cb = Codebase(FAKE, ["partition/**/*.py", "placement/**/*.py"], [])
    # fake_baseline 的 repair_balance 在 gnn_partition.py 第 ~20 行附近
    src = (FAKE / "partition/gnn_partition.py").read_text().splitlines()
    ln = next(i for i, l in enumerate(src, 1) if "def repair_balance" in l) + 1
    hit = cb.search(["zzz_nomatch"], k=3, boost_lines={"partition/gnn_partition.py": [ln]})
    assert hit and hit[0].file == "partition/gnn_partition.py" and hit[0].start <= ln <= hit[0].end
