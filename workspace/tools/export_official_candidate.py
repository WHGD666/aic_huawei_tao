"""Export a verified official candidate as a portable Git patch.

This reads the organizer's frozen task manifest and candidate snapshots. It
does not modify the official release, work directory, or evaluation records.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess


MANIFESTS = {
    "partition_placement": "partition-placement-native-task.json",
    "full_flow": "full-flow-native-task.json",
}


def _diff_file(baseline: Path, candidate: Path, relative: str) -> str:
    old = baseline / relative
    new = candidate / relative
    if old.is_symlink() or new.is_symlink():
        raise ValueError(f"Source symlink is not allowed: {relative}")
    if old.is_file() and not new.is_file():
        raise ValueError(f"Baseline source was deleted: {relative}")
    if not new.is_file():
        return ""
    source = str(old) if old.is_file() else "/dev/null"
    result = subprocess.run(
        ["git", "diff", "--no-index", "--no-ext-diff", "--no-textconv",
         "--", source, str(new)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        return ""
    if result.returncode != 1 or not result.stdout.startswith("diff --git "):
        raise RuntimeError(f"Git could not diff {relative}: {result.stderr[:400]}")
    lines = result.stdout.splitlines(keepends=True)
    lines[0] = f"diff --git a/{relative} b/{relative}\n"
    for index, line in enumerate(lines):
        if line.startswith("--- "):
            lines[index] = f"--- {'a/' + relative if old.is_file() else '/dev/null'}\n"
        elif line.startswith("+++ "):
            lines[index] = f"+++ b/{relative}\n"
    return "".join(lines)


def export(release: Path, work_root: Path, task: str, candidate_id: str,
           output: Path) -> list[str]:
    import json

    release = release.resolve(strict=True)
    work_root = work_root.resolve(strict=True)
    manifest = json.loads(
        (release / "framework/competition/manifests" / MANIFESTS[task]).read_text()
    )
    policy = manifest["optimization"]
    baseline_id = policy["baseline"]
    root = work_root / task / "candidates"
    baseline = (root / baseline_id / "tree").resolve(strict=True)
    candidate = (root / candidate_id / "tree").resolve(strict=True)
    if baseline == candidate:
        raise ValueError("Selected candidate is the unmodified baseline")
    if baseline.parent.parent != root or candidate.parent.parent != root:
        raise ValueError("Candidate path escaped the official work directory")

    files = set(policy["allowed_files"])
    new_files = []
    for directory, suffixes in policy.get("new_files", {}).items():
        folder = candidate / directory
        if not folder.exists():
            continue
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError(f"Invalid helper directory: {directory}")
        for path in folder.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Helper symlink is not allowed: {path}")
            if not path.is_file():
                continue
            if path.suffix not in suffixes:
                raise ValueError(f"Unexpected helper suffix: {path}")
            relative = path.relative_to(candidate).as_posix()
            files.add(relative)
            if not (baseline / relative).exists():
                new_files.append(relative)

    chunks = [_diff_file(baseline, candidate, name) for name in sorted(files)]
    changed = [chunk.splitlines()[0].split(" b/", 1)[1]
               for chunk in chunks if chunk]
    if not changed:
        raise ValueError("Selected candidate has no authorized source changes")
    output.mkdir(parents=True, exist_ok=True)
    patch_path = output / "candidate.patch"
    patch_path.write_text("".join(chunks))
    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=baseline, capture_output=True, text=True, check=False,
    )
    if check.returncode:
        patch_path.unlink()
        raise RuntimeError(f"Exported patch does not apply: {check.stderr[:800]}")
    for relative in new_files:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate / relative, destination)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--task", choices=sorted(MANIFESTS), required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    changed = export(args.release, args.work_root, args.task,
                     args.candidate_id, args.output)
    print(f"Exported {len(changed)} changed source files to {args.output}")
    for name in changed:
        print(name)


if __name__ == "__main__":
    main()
