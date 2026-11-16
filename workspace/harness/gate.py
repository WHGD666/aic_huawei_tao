"""
gate.py -- 提交评估前的免费本地门禁。目标：把明显无效的候选拦在 30 次预算之外。

检查项（按代价从低到高）：
  1. 路径白名单：改动文件必须命中 allowed 且不命中 denied
  2. 禁改模式：diff 新增行里不得出现评估/计分/预算相关标识（可配置）
  3. 语法：对改动后的 .py 做 py_compile
  4. 静态：pyflakes（未定义名、未使用导入等）
  5. 冒烟：可选外部命令（官方若允许离线小算例）
每一项失败都返回可读报告，供 LLM 做一次自动修复。
"""
from __future__ import annotations

import py_compile
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from .codebase import match_any


def _undefined_names(src: str):
    """极简未定义名检查（pyflakes 不可用时的兜底）：模块级 + 函数级作用域，忽略属性/关键字参数。"""
    import ast
    import builtins
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    defined = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            if not isinstance(node, ast.ClassDef):
                for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                    defined.add(a.arg)
                if node.args.vararg: defined.add(node.args.vararg.arg)
                if node.args.kwarg: defined.add(node.args.kwarg.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                defined.add((al.asname or al.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, (ast.For, ast.comprehension)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name): defined.add(n.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for it in node.items:
                if it.optional_vars is not None:
                    for n in ast.walk(it.optional_vars):
                        if isinstance(n, ast.Name): defined.add(n.id)
        elif isinstance(node, ast.Global):
            defined.update(node.names)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in defined:
            out.append((node.id, node.lineno))
    return out


@dataclass
class GateReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def text(self) -> str:
        out = []
        for e in self.errors:
            out.append(f"ERROR: {e}")
        for w in self.warnings:
            out.append(f"WARN : {w}")
        return "\n".join(out) if out else "OK"


class Gate:
    def __init__(self, whitelist_file: Path, forbidden_patterns: List[str], run_pyflakes: bool = True, smoke_cmd: str = ""):
        wl = yaml.safe_load(open(whitelist_file, encoding="utf-8"))
        self.allowed: List[str] = wl.get("allowed", [])
        self.denied: List[str] = wl.get("denied", [])
        self.forbidden = [re.compile(p, re.IGNORECASE) for p in forbidden_patterns]
        self.run_pyflakes = run_pyflakes
        self.smoke_cmd = smoke_cmd

    # ---------- public ----------
    def check(self, changed_files: Dict[str, str], patch_text: str, workdir: Optional[Path] = None) -> GateReport:
        """
        changed_files: {relpath: new_full_content}
        patch_text   : unified diff（用于禁改模式检查，只看 '+' 行）
        """
        rep = GateReport(ok=True)
        self._check_paths(changed_files, rep)
        self._check_forbidden(patch_text, rep)
        self._check_syntax(changed_files, rep)
        if self.run_pyflakes:
            self._check_pyflakes(changed_files, rep)
        if self.smoke_cmd and workdir and rep.ok:
            self._check_smoke(workdir, rep)
        rep.ok = not rep.errors
        return rep

    # ---------- checks ----------
    def _match_any(self, path: str, patterns: List[str]) -> bool:
        return match_any(path, patterns)

    def _check_paths(self, files: Dict[str, str], rep: GateReport) -> None:
        if not files:
            rep.errors.append("no files changed")
            return
        for f in files:
            if not self._match_any(f, self.allowed):
                rep.errors.append(f"file not in whitelist: {f}")
            elif self._match_any(f, self.denied):
                rep.errors.append(f"file explicitly denied: {f}")

    def _check_forbidden(self, patch: str, rep: GateReport) -> None:
        for ln in patch.splitlines():
            if ln.startswith("+") and not ln.startswith("+++"):
                for pat in self.forbidden:
                    if pat.search(ln):
                        rep.warnings.append(f"added line touches forbidden token /{pat.pattern}/: {ln.strip()[:100]}")

    def _check_syntax(self, files: Dict[str, str], rep: GateReport) -> None:
        for f, content in files.items():
            if not f.endswith(".py"):
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(content)
                tmp = tf.name
            try:
                py_compile.compile(tmp, doraise=True)
            except py_compile.PyCompileError as e:
                rep.errors.append(f"syntax error in {f}: {e.msg.splitlines()[-1] if e.msg else e}")
            finally:
                Path(tmp).unlink(missing_ok=True)

    def _check_pyflakes(self, files: Dict[str, str], rep: GateReport) -> None:
        try:
            import pyflakes.api  # noqa
            from pyflakes.reporter import Reporter
        except ImportError:
            # 退化：内置的轻量未定义名检查
            for f, content in files.items():
                if f.endswith(".py"):
                    for name, ln in _undefined_names(content):
                        rep.errors.append(f"{f}:{ln}: undefined name '{name}' (builtin checker)")
            return
        import io
        for f, content in files.items():
            if not f.endswith(".py"):
                continue
            out, err = io.StringIO(), io.StringIO()
            n = pyflakes.api.check(content, f, Reporter(out, err))
            if n:
                for line in out.getvalue().splitlines():
                    # 未定义名是硬错误，其余当警告
                    if "undefined name" in line:
                        rep.errors.append(line)
                    else:
                        rep.warnings.append(line)

    def _check_smoke(self, workdir: Path, rep: GateReport) -> None:
        try:
            r = subprocess.run(self.smoke_cmd.format(workdir=str(workdir)), shell=True, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                rep.errors.append(f"smoke test failed (rc={r.returncode}): {(r.stderr or r.stdout)[-800:]}")
        except subprocess.TimeoutExpired:
            rep.errors.append("smoke test timeout")
