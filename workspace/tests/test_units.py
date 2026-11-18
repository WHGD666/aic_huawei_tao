import shutil
import sys
from pathlib import Path

import pytest

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from harness.budget import Budget, Pricing, Usage  # noqa: E402
from harness.codebase import Codebase, Edit, apply_edits, git_diff  # noqa: E402
from harness.evaluator import MockEvaluator, OfficialEvaluator  # noqa: E402
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
    assert run_score(b, RunMetrics("x", 1, float("nan"), -50), False) is None
    assert run_score(b, RunMetrics("x", 1, -0.5, -50, overflow=float("nan")), True) is None


def test_official_evaluator_rejects_missing_metrics():
    ev = OfficialEvaluator(["x"], [1], cmd="unused", full_flow=True)
    for run in ({"tns": -10, "overflow": 2}, {"wns": -1, "tns": -10},
                {"wns": "nan", "tns": -10, "overflow": 2}):
        result = ev._parse("c001", {"runs": {"x/1": run}}).runs["x/1"]
        assert not result.valid and result.fail_reason
    ev1 = OfficialEvaluator(["x"], [1], cmd="unused", full_flow=False)
    assert ev1._parse("c001", {"runs": {"x/1": {"wns": -1, "tns": -10}}}).runs["x/1"].valid


def test_official_evaluator_rejects_failed_command(tmp_path):
    import json
    import subprocess
    from unittest.mock import patch
    ev = OfficialEvaluator(["x"], [1], cmd="unused")
    work = tmp_path / "work"; work.mkdir()
    (work / "eval_result.json").write_text(json.dumps({"runs": {"x/1": {"wns": -1, "tns": -10, "valid": True}}}))
    with patch("harness.evaluator.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
        run = ev.evaluate("c001", work).runs["x/1"]
    assert not run.valid and "exited 1" in run.fail_reason


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
    import os, shutil, subprocess
    base_copy = tmp_path / "base"
    shutil.copytree(FAKE, base_copy)
    (tmp_path / "c.patch").write_text(d, encoding="utf-8", newline="")
    env = os.environ.copy(); env["GIT_CEILING_DIRECTORIES"] = str(base_copy.parent.resolve())
    r = subprocess.run(["git", "apply", "--check", str(tmp_path / "c.patch")], cwd=base_copy, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["git", "apply", str(tmp_path / "c.patch")], cwd=base_copy, env=env, capture_output=True, text=True)
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
    sel = Selector(st, elite_size=2, max_consecutive_failures=2, phase_plan={"probe": 1, "structural": 2, "combine": 1})
    assert sel.phase_for(0) == "probe" and sel.phase_for(1) == "structural" and sel.phase_for(3) == "combine" and sel.phase_for(4) == "final"
    assert sel.best().id == "c001"
    assert sel.choose_parent("probe") is None
    # 构造时已重放历史：c002 劣于父代 c001 -> c001 streak=1（上限 2，仍可选）
    assert sel._fail_streak == {"c001": 1}
    assert sel.choose_parent("final") == "c001"
    sel.record_outcome(b)                      # 再记一次 -> streak 2 -> 排除
    assert sel.choose_parent("final") == "c002"
    # gate_failed 的子代不算父代的账
    g = CandidateMeta("c003", "c002", "h3", "e", [], "structural", status="gate_failed")
    sel.record_outcome(g)
    assert sel._fail_streak.get("c002", 0) == 0
    sel.force_phase = "final"
    assert sel.phase_for(0) == "final"
    sel.force_phase = None
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


def test_gate_case_name_hardcoding_rejected():
    g = Gate(WL, [], run_pyflakes=False, case_names=["bp_fe-ng45", "bp-ng45", "ariane133-ng45"])
    bad = "+    if design == 'bp_fe':\n+        lam = 0.5\n"
    rep = g.check({"partition/gnn_partition.py": "x = 1\n"}, bad)
    assert not rep.ok and any("bp_fe" in e for e in rep.errors)
    bad2 = "+    weights = {'ariane133': 2.0}\n"
    assert not g.check({"partition/gnn_partition.py": "x = 1\n"}, bad2).ok
    # 注释里提一下不算错（只警告）；普通代码不含 token 正常
    ok = g.check({"partition/gnn_partition.py": "x = 1\n"}, "+    # tuned on bp_fe-like designs\n+    lam = 0.5\n")
    assert ok.ok and ok.warnings
    # 'top' 'core' 这类通用词不应成为 token；短名 'bp' 只在字符串字面量里才算
    assert "top" not in g.case_tokens and "core" not in g.case_tokens
    assert g.check({"partition/gnn_partition.py": "x = 1\n"}, "+    bp = base_point + 1\n").ok
    assert not g.check({"partition/gnn_partition.py": "x = 1\n"}, "+    if d == 'bp': pass\n").ok
    # 按工艺分支是错误；只是提到工艺名是警告
    assert not g.check({"partition/gnn_partition.py": "x = 1\n"}, "+    if pdk == 'asap7': u = 'ps'\n").ok
    r = g.check({"partition/gnn_partition.py": "x = 1\n"}, "+    lef = read(platform)  # ng45 or asap7\n")
    assert r.ok
    # 普通 for ... in 与同一行的工艺标签无关，不能误判为条件分支。
    r = g.check({"partition/gnn_partition.py": "x = 1\n"}, '+    for name in examples: print("ng45")\n')
    assert r.ok and r.warnings


def test_gate_per_design_consistency(tmp_path):
    import yaml
    wlf = tmp_path / "wl.yaml"
    wlf.write_text(yaml.safe_dump({"allowed": ["cfg/*.json"], "per_design": ["cfg/*.json"], "denied": []}), encoding="utf-8")
    g = Gate(wlf, [], run_pyflakes=False)
    (tmp_path / "base" / "cfg").mkdir(parents=True)
    for n in ("a", "b", "c"):
        (tmp_path / "base" / "cfg" / f"{n}.json").write_text('{"d": 0.4}\n')
    g.baseline_root = tmp_path / "base"
    base = {f"cfg/{n}.json": '{"d": 0.4}\n' for n in "abc"}
    same = {f"cfg/{n}.json": '{"d": 0.5}\n' for n in "abc"}
    diff = {"cfg/a.json": '{"d": 0.5}\n', "cfg/b.json": '{"d": 0.4}\n', "cfg/c.json": '{"d": 0.5}\n'}
    only_one = {"cfg/a.json": '{"d": 0.5}\n'}
    assert g.check(same, "", baseline_files=base).ok
    r = g.check(diff, "", baseline_files=base)
    assert not r.ok and any("different edits" in e for e in r.errors)
    r2 = g.check(only_one, "", baseline_files=base)
    assert not r2.ok and any("but not" in e for e in r2.errors)


def test_store_new_id_after_deletion(tmp_path):
    st = CandidateStore(tmp_path / "c")
    for i in range(3):
        st.create(CandidateMeta(st.new_id(), None, "h", "e", [], "probe"), "")
    import shutil
    shutil.rmtree(tmp_path / "c" / "c003")
    st2 = CandidateStore(tmp_path / "c")
    assert st2.new_id() == "c004"


def test_small_budget_keeps_probe_and_one_evaluation(tmp_path):
    from harness import loop as L
    store = CandidateStore(tmp_path / "store")
    sel = Selector(store, phase_plan={"probe": 6, "structural": 14, "combine": 8})
    sel.rescale_phase_plan(1)
    assert sel.phase_plan == {"probe": 1, "structural": 0, "combine": 0}
    cfg = L.load_config(WS / "config" / "harness.yaml")
    cfg["budget"]["max_evaluations"] = 1
    cfg["gate"]["whitelist_file"] = "tests/whitelist_fake.yaml"
    h = L.Harness(cfg, llm_mode="replay", run_dir=tmp_path / "run")
    assert h.budget.reserve_evaluations == 0 and h.budget.can_evaluate()
    h.log_f.close()


def test_apply_edits_atomic_and_path_guard(tmp_path):
    work = tmp_path / "w"
    changed, errs = apply_edits(FAKE, work, [
        Edit("partition/gnn_partition.py", "TIMING_WEIGHT = 0.0", "TIMING_WEIGHT = 0.5"),
        Edit("placement/place3d.py", "NOPE", "x"),
    ])
    assert errs and not (work / "partition/gnn_partition.py").exists()   # 任一失败则整体不写
    _, errs2 = apply_edits(FAKE, work, [Edit("../harness/budget.py", "a", "b")])
    assert errs2 and "illegal path" in errs2[0]


def test_difflib_fallback_patch_applies(tmp_path):
    """HIGH#4：无 git 时 difflib 生成的 patch 也要能被 git apply（这里用 git 验证兜底产物）。"""
    from harness.codebase import _difflib_diff
    import os, subprocess, shutil
    work = tmp_path / "w"
    apply_edits(FAKE, work, [Edit("partition/gnn_partition.py", "TIMING_WEIGHT = 0.0", "TIMING_WEIGHT = 0.5")])
    d = _difflib_diff("partition/gnn_partition.py", FAKE / "partition/gnn_partition.py", work / "partition/gnn_partition.py")
    assert d.startswith("diff --git a/partition/gnn_partition.py b/partition/gnn_partition.py")
    base_copy = tmp_path / "base"; shutil.copytree(FAKE, base_copy)
    (tmp_path / "p.patch").write_text(d, encoding="utf-8", newline="")
    env = os.environ.copy(); env["GIT_CEILING_DIRECTORIES"] = str(base_copy.parent.resolve())
    r = subprocess.run(["git", "apply", str(tmp_path / "p.patch")], cwd=base_copy, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "TIMING_WEIGHT = 0.5" in (base_copy / "partition/gnn_partition.py").read_text()


def test_patch_fingerprint_keeps_file_identity():
    from harness.codebase import patch_fingerprint
    hunk = "@@ -1 +1 @@\n-old\n+new\n"
    a = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n" + hunk
    b = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n" + hunk
    assert patch_fingerprint(a) != patch_fingerprint(b)
    assert patch_fingerprint(a) == patch_fingerprint(a.replace("@@ -1 +1 @@", "@@ -5 +5 @@"))


def test_adapter_restores_seed_after_each_run(tmp_path):
    import json
    from unittest.mock import patch
    from tools.evaluate_open3dbench import run_placer

    o3 = tmp_path / "Open3DBench"
    params = o3 / "Place-LoL" / "test" / "3D_input_default" / "bp_fe.json"
    params.parent.mkdir(parents=True)
    params.write_text('{"random_seed": 1000, "density": 0.4}\n', encoding="utf-8", newline="")
    original = params.read_bytes()
    seen = []

    def fake_run(*args):
        seen.append(json.loads(params.read_text(encoding="utf-8"))["random_seed"])
        return 0

    with patch("tools.evaluate_open3dbench.run", side_effect=fake_run):
        for seed in (1001, 1002, 1003):
            run_placer(o3, "bp_fe-ng45", seed, "harness", "default", "echo {seed}", {}, tmp_path / "run.log", False, 10)
            assert params.read_bytes() == original
    assert seen == [1001, 1002, 1003]
    with patch("tools.evaluate_open3dbench.run", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError):
            run_placer(o3, "bp_fe-ng45", 1001, "harness", "default", "echo {seed}", {}, tmp_path / "run.log", False, 10)
    assert params.read_bytes() == original


def test_adapter_inplace_guard_and_repaired_alias(tmp_path):
    from tools.evaluate_open3dbench import collect, main

    o3 = tmp_path / "Open3DBench"; o3.mkdir()
    work = tmp_path / "candidate"; work.mkdir()
    (work / "edit.py").write_text("x = 1\n")
    out = tmp_path / "out"
    assert main(["--workdir", str(work), "--out", str(out), "--o3", str(o3), "--inplace"]) == 2
    assert not (o3 / "edit.py").exists()
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    results_dir = tmp_path / "results"; results_dir.mkdir()
    (log_dir / "3_4b_task1_repaired.json").write_text("{}")
    collect(log_dir, results_dir, out, "3_4b_task1_repaired.json", 0, False)
    assert (out / "placeopt.json").exists()
