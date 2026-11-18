"""Default participant loop: edit with Codex, then evaluate outside its call."""
import json
from hashlib import sha256
from pathlib import Path
import uuid
import time

from competition.agent_api import AgentAPIError


SMOKE_ALGORITHM = "Place-3D/thirdparty/DREAMPlace/dreamplace/concurrent_lol.py"


def _allowed_file_hashes(root, policy):
    """Snapshot permitted source files before a model edit."""
    return {name: sha256((root / name).read_bytes()).hexdigest()
            for name in policy.get("allowed_paths", []) if (root / name).is_file()}


def _changed_source(root, before):
    changed = [name for name, old in before.items()
               if (root / name).is_file()
               and sha256((root / name).read_bytes()).hexdigest() != old]
    for name in changed:
        if name.endswith(".py"):
            path = root / name
            compile(path.read_bytes(), str(path), "exec")
    return changed


def evaluate(context, checkout_id, request_id):
    try:
        context.submit_evaluation(checkout_id, request_id=request_id)
    except AgentAPIError as error:
        # An error after acceptance must never cause a duplicate submission.
        if not any(row["request_id"] == request_id for row in context.status()["history"]):
            return {"status": "rejected", "reason": str(error)}
    result = context.wait_evaluation(request_id)
    return result


class Agent:
    def run(self, context):
        folder = Path(__file__).parent
        smoke = context.evaluation_mode == "smoke"
        feedback = None
        state = context.status()
        best = None
        # Reconcile pending work and recover the last feedback before another edit.
        for index, row in enumerate(state["history"]):
            if row["status"] == "accepted" or index == len(state["history"]) - 1:
                feedback = context.wait_evaluation(row["request_id"])
        state = context.status()
        successes = [row for row in state["history"] if row["status"] == "succeeded"]
        if successes:
            best = max(successes, key=lambda row: row["score"])
        attempts = {}
        model_failure = None
        checkout = None
        checkout_before = None
        edit_failures = 0
        while True:
            state = context.status()
            if not state.get("codex_available", True):
                raise RuntimeError("Default Agent requires Codex CLI; install it on the host or use a custom Agent with context.model")
            draft_only = smoke and best is not None
            if not state["dispatch_allowed"] or (state["evaluations_remaining"] == 0 and not draft_only):
                if state.get("unresolved_model_calls") and "insufficient_model_reservation" in state.get("model_stop_reasons", []):
                    raise RuntimeError(
                        "Model usage is unresolved and blocks further calls. "
                        "Inspect control/model-receipts/*.error.json before resuming this work directory; "
                        "verified candidates and evaluation history are retained.")
                break
            parent = best["candidate_id"] if best else context.baseline_candidate
            phase = "feedback-edit" if draft_only else "first-edit"
            checkout_key = ("next-source" if draft_only else "smoke-source")
            if attempts.get(phase, 0):
                checkout_key += "-retry-" + str(attempts[phase])
            if checkout is None:
                checkout = context.checkout(parent=parent, request_id=checkout_key if smoke else uuid.uuid4().hex)
            path = Path(checkout["path"])
            if checkout_before is None:
                checkout_before = _allowed_file_hashes(path, context.source_policy)
            if smoke:
                algorithm = path / SMOKE_ALGORITHM
                if not (folder / "initial_program.py").exists():
                    (folder / "initial_program.py").write_text(algorithm.read_text())
                instruction = (
                    "Inside backend_aware_target_density, change the utilization_floor margin "
                    "from utilization + 0.01 to utilization + 0.0101."
                    if not draft_only else
                    "Inside backend_aware_target_density, choose one further small numerical "
                    "adjustment to the utilization_floor margin based on the feedback. "
                    "Cite a measured score or timing value in your explanation.")
                prompt = "Edit only " + str(algorithm) + ". " + instruction + (
                    " Preserve signatures, imports and all other functions. Use the container "
                    "terminal to implement the change. Do not evaluate, submit, wait for an "
                    "evaluation, or invoke Agent API operations. Return after editing.\n")
            else:
                endpoint = ("one OPT pass, before CTS" if context.task == "partition_placement"
                            else "global routing")
                manifest = ("partition-placement-native-task.json" if context.task == "partition_placement"
                            else "full-flow-native-task.json")
                prompt = (
                    "Improve the " + context.task + " physical-design algorithms in " + str(path) + ".\n"
                    "Read /opt/contest/AgenticBench/competition/manifests/" + manifest +
                    " for the allowed files. Scoring is after " + endpoint + ".\n"
                    "Legal cell scores are floored at -100. A confirmed physical-invalid cell scores -100 "
                    "and remains in the fixed matrix average. Such scored candidates may be parents or "
                    "final submissions; inspect valid, raw_score and reason in cell feedback.\n"
                    "Make one concrete algorithm improvement, preserving interfaces and protected files. "
                    "You may run small local checks. Do not call Agent API operations or submit/wait for "
                    "an official evaluation. Return after editing and explain the change. "
                    "The Python Agent will submit this checkout and wait after this Codex call ends.\n"
                    "Current status:\n" + json.dumps(state, ensure_ascii=False) + "\n")
            limits = context.source_policy.get("function_scope", {})
            if limits:
                prompt += ("Editable function bodies in mixed modules (official source policy):\n"
                           + json.dumps(limits, sort_keys=True, ensure_ascii=False) + "\n"
                           "In these files, edit only the named function bodies. Keep module-level "
                           "imports, signatures, decorators and all code outside those bodies unchanged. "
                           "Do not add top-level helper functions in these mixed modules. "
                           "In other task-allowed files, you may add helper functions and revise "
                           "algorithms while preserving stage interfaces. Apply both the file list "
                           "and these function-body limits.\n")
            if not smoke:
                creation_paths = context.source_policy.get("creation_paths", {})
                prompt += ("New helper directories and suffixes (context.source_policy.creation_paths):\n"
                           + json.dumps(creation_paths, sort_keys=True, ensure_ascii=False) + "\n"
                           "Create Python modules or C++ headers only in these contest_algorithms "
                           "directories with the listed suffixes. Newly added helpers may be edited "
                           "or deleted in later candidates. Existing baseline file permissions and "
                           "function-body limits remain unchanged; never delete baseline source files. "
                           "Use explicit imports/includes from already editable code. In mixed modules, "
                           "put any new Python import inside an allowed function body. Python helper "
                           "directories are namespace packages: do not add __init__.py. Do not add "
                           "autoload files, symlinks, caches, executable file modes, native translation "
                           "units, or build-configuration changes. An empty map grants no new files.\n")
            prompt += ("For a terminal result marked running, poll exec with its session_id "
                       "until the command finishes before returning.\n")
            if model_failure is not None:
                prompt += "PREVIOUS MODEL FAILURE:\n" + json.dumps(model_failure, ensure_ascii=False) + "\n"
            prompt += ("Read /workspace/README.md for the contest workflow and /opt/contest/source "
                       "for the complete physical source. Evaluation feedback includes artifacts_dir "
                       "and files_index: inspect the original logs there with terminal tools. "
                       "Large text files are complete gzip files, readable with zcat or Python gzip.\n")
            if feedback is not None:
                prompt += "EVALUATOR FEEDBACK:\n" + json.dumps(feedback, sort_keys=True, ensure_ascii=False)
            prompt = "OFFLINE EXPERT KNOWLEDGE: NONE.\n" + prompt
            phase = "feedback-edit" if draft_only else "first-edit"
            attempt = attempts.get(phase, 0)
            attempts[phase] = attempt + 1
            turn_id = (phase + ("-retry-" + str(attempt) if attempt else "")) if smoke else uuid.uuid4().hex
            try:
                turn = context.codex_turn(prompt, request_id=turn_id)
            except AgentAPIError as error:
                turn = {"success": False, "error": str(error)}
            if not turn.get("success"):
                if turn.get("retryable") is False:
                    raise RuntimeError(turn.get("error", "Model adapter requires host reconciliation"))
                model_failure = {"status": "model_failed", "turn": turn}
                (folder / "model_failure.json").write_text(json.dumps(model_failure, ensure_ascii=False))
                edit_failures += 1
                if edit_failures >= 3:
                    if best is not None:
                        break
                    raise RuntimeError("Three model edits failed before a candidate could be evaluated")
                time.sleep(5)
                continue
            try:
                changed = _changed_source(path, checkout_before)
                if not changed:
                    raise ValueError("No authorized source file changed")
            except (SyntaxError, ValueError) as error:
                model_failure = {"status": "edit_check_failed", "reason": str(error)}
                (folder / "model_failure.json").write_text(json.dumps(model_failure, ensure_ascii=False))
                edit_failures += 1
                if edit_failures >= 3:
                    if best is not None:
                        break
                    raise RuntimeError("Three edits failed local source checks") from error
                continue
            model_failure = None
            edit_failures = 0
            if smoke:
                (folder / ("second_program.py" if draft_only else "first_program.py")).write_text(
                    algorithm.read_text())
            if draft_only:
                break
            request_id = "smoke-evaluation" if smoke else uuid.uuid4().hex
            feedback = evaluate(context, checkout["checkout_id"], request_id)
            checkout = None
            checkout_before = None
            (folder / "feedback.json").write_text(json.dumps(feedback, sort_keys=True, ensure_ascii=False))
            if feedback["status"] == "succeeded":
                candidate = {"candidate_id": feedback["candidate_id"], "score": feedback["feedback"]["score"]}
                if best is None or candidate["score"] > best["score"]:
                    best = candidate
            elif smoke:
                raise RuntimeError("Single-cell native evaluation failed")
            # Rejections and failed accepted matrices become the next turn's feedback.
        if best is None:
            raise RuntimeError("No candidate completed a verified training evaluation")
        return best["candidate_id"]
