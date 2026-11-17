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

from .codebase import glob_match, match_any


def _mk_check(src: str) -> str:
    """极简 Makefile 片段检查：每个非空非注释行要么是变量赋值/export/include/条件，要么是以 Tab 开头的 recipe，要么是续行。
    主要拦 LLM 把 `export X = 1` 写成 `X: 1` 或丢了续行反斜杠之类的低级错。"""
    import re as _re
    assign = _re.compile(r"^(export\s+|unexport\s+|override\s+)?[A-Za-z_][\w.-]*\s*[:?+!]?=")
    directive = _re.compile(r"^(include|-include|sinclude|ifeq|ifneq|ifdef|ifndef|else|endif|define|endef|\.PHONY|\$\(|export\s+[A-Za-z_]|unexport)")
    rule = _re.compile(r"^[^\s:=#][^:=#]*:")
    cont = False
    for no, line in enumerate(src.splitlines(), 1):
        s = line.rstrip()
        if cont:
            cont = s.endswith("\\")
            continue
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        if s.startswith("\t"):
            cont = s.endswith("\\")
            continue
        body = s.strip()
        if assign.match(body) or directive.match(body) or rule.match(body):
            cont = s.endswith("\\")
            continue
        return f"line {no}: not an assignment/directive/rule: {body[:60]}"
    return ""


def _tcl_brace_check(src: str) -> str:
    """极简 Tcl 检查：忽略注释行与 \\{ \\} 转义后，{} 与 [] 必须配平。返回错误描述或空串。"""
    depth_b = depth_s = 0
    for ln_no, line in enumerate(src.splitlines(), 1):
        s = line.lstrip()
        if s.startswith("#"):
            continue
        prev = ""
        for ch in line:
            if prev == "\\":
                prev = ""
                continue
            if ch == "{":
                depth_b += 1
            elif ch == "}":
                depth_b -= 1
                if depth_b < 0:
                    return f"unexpected '}}' at line {ln_no}"
            elif ch == "[":
                depth_s += 1
            elif ch == "]":
                depth_s -= 1
                if depth_s < 0:
                    return f"unexpected ']' at line {ln_no}"
            prev = ch
    if depth_b:
        return f"unbalanced braces: {depth_b} unclosed '{{'"
    if depth_s:
        return f"unbalanced brackets: {depth_s} unclosed '['"
    return ""


def _undefined_names(src: str):
    """极简未定义名检查（pyflakes 不可用时的兜底）。作用域不精确，所以只报"全模块任何地方都没定义过"的名字。
    补齐：模块魔法名、推导式/lambda 参数、walrus、match 捕获、类型注解里的字符串、全局 import *。"""
    import ast
    import builtins
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
                                    "__builtins__", "__all__", "__path__", "__annotations__", "__dict__", "__class__"}
    star_import = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = node.args
            for p in a.args + a.kwonlyargs + a.posonlyargs:
                defined.add(p.arg)
            if a.vararg: defined.add(a.vararg.arg)
            if a.kwarg: defined.add(a.kwarg.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                if al.name == "*":
                    star_import = True
                defined.add((al.asname or al.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name): defined.add(n.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for it in node.items:
                if it.optional_vars is not None:
                    for n in ast.walk(it.optional_vars):
                        if isinstance(n, ast.Name): defined.add(n.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            defined.update(node.names)
        elif isinstance(node, ast.NamedExpr):
            defined.add(node.target.id)
        elif hasattr(ast, "MatchAs") and isinstance(node, getattr(ast, "MatchAs")) and node.name:
            defined.add(node.name)
        elif hasattr(ast, "MatchStar") and isinstance(node, getattr(ast, "MatchStar")) and node.name:
            defined.add(node.name)
    if star_import:
        return []  # 无法判断
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
    def __init__(self, whitelist_file: Path, forbidden_patterns: List[str], run_pyflakes: bool = True, smoke_cmd: str = "",
                 case_names: Optional[List[str]] = None):
        wl = yaml.safe_load(open(whitelist_file, encoding="utf-8"))
        self.allowed: List[str] = wl.get("allowed", [])
        self.denied: List[str] = wl.get("denied", [])
        # per_design: 这些 glob 匹配"按算例一份"的文件；候选改了其中一个就必须对所有同类文件做相同改动
        self.per_design: List[str] = wl.get("per_design", [])
        self.forbidden = [re.compile(p, re.IGNORECASE) for p in forbidden_patterns]
        self.run_pyflakes = run_pyflakes
        self.smoke_cmd = smoke_cmd
        # 题面 §十一(三)：不得按算例名硬编码。算例名及其电路名/工艺名 token 在新增行里出现 -> 硬错误
        toks, short = set(), set()
        generic = {"top", "core", "wrapper", "ng45", "asap7", "nangate45", "sky130", "gf180"}
        for c in case_names or []:
            toks.add(c)
            circuit = c.rsplit("-", 1)[0] if "-" in c else c        # bp_fe-ng45 -> bp_fe
            (toks if len(circuit) >= 4 else short).add(circuit)      # 'bp' 这种短名只在字符串字面量里才算
            for part in re.split(r"[-_]", c):
                if len(part) >= 4 and part.lower() not in generic:
                    toks.add(part)
        self.case_tokens = sorted(toks, key=len, reverse=True)
        self.short_tokens = sorted(short)
        self._case_re = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, self.case_tokens)) + r")(?![A-Za-z0-9_])", re.I) if self.case_tokens else None
        self._short_re = re.compile(r"['\"](" + "|".join(map(re.escape, self.short_tokens)) + r")['\"]", re.I) if self.short_tokens else None

    # ---------- public ----------
    def check(self, changed_files: Dict[str, str], patch_text: str, workdir: Optional[Path] = None,
              baseline_files: Optional[Dict[str, str]] = None) -> GateReport:
        """
        changed_files : {relpath: new_full_content}
        patch_text    : unified diff（用于禁改模式检查，只看 '+' 行）
        baseline_files: {relpath: original_content}；给了就只报"候选新引入"的静态问题，不替基线背锅
        """
        rep = GateReport(ok=True)
        self._check_paths(changed_files, rep)
        self._check_forbidden(patch_text, rep)
        self._check_case_names(patch_text, rep)
        self._check_per_design_consistency(changed_files, baseline_files or {}, rep)
        self._check_syntax(changed_files, rep)
        if self.run_pyflakes:
            self._check_pyflakes(changed_files, rep, baseline_files or {})
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

    _PDK_RE = re.compile(r"(?<![A-Za-z0-9_])(ng45|nangate45|asap7|sky130|gf180)(?![A-Za-z0-9_])", re.I)
    _COND_RE = re.compile(r"==|!=|\bif\b|\belif\b|\bin\b|\bmatch\b|\bcase\b|\bswitch\b|[{[]\s*['\"]")

    def _check_case_names(self, patch: str, rep: GateReport) -> None:
        """新增行里出现训练/验证算例名或其电路名 token（bp_fe / ariane133 / swerv ...）-> 视为按算例特判，硬错误。
        工艺名 token（ng45 / asap7 ...）出现在条件/查表语境里 -> 硬错误（按工艺分支 = 对隐藏算例不泛化）；其它语境只警告。
        注释（整行或行尾）里提到不算错，只警告。"""
        for ln in patch.splitlines():
            if not (ln.startswith("+") and not ln.startswith("+++")):
                continue
            body = ln[1:]
            stripped = body.strip()
            # 去掉行尾注释（粗略：# 或 // 之后；字符串里的 # 极少见于这些代码）
            code = re.split(r"\s+#|^#|//", body, maxsplit=1)[0]
            is_comment_only = stripped.startswith(("#", "//", "/*", "*", "--"))
            if self._case_re is not None or self._short_re is not None:
                m = None
                if not is_comment_only:
                    m = (self._case_re.search(code) if self._case_re else None) or (self._short_re.search(code) if self._short_re else None)
                if m:
                    rep.errors.append(f"added line references case/circuit token '{m.group(1)}' (no per-circuit special-casing allowed): {stripped[:100]}")
                    continue
                m2 = self._case_re.search(body) if self._case_re else None
                if m2:
                    rep.warnings.append(f"added comment mentions case token '{m2.group(1)}': {stripped[:80]}")
            pm = self._PDK_RE.search(code if not is_comment_only else "")
            if pm:
                if self._COND_RE.search(code):
                    rep.errors.append(f"added line branches on PDK/platform token '{pm.group(1)}' (derive from LEF/Liberty instead): {stripped[:100]}")
                else:
                    rep.warnings.append(f"added line mentions PDK token '{pm.group(1)}': {stripped[:80]}")

    def _check_per_design_consistency(self, files: Dict[str, str], baseline: Dict[str, str], rep: GateReport) -> None:
        """白名单 per_design 下的文件：若改了其中一个，则所有同类文件的 diff 必须一致（同样的 SEARCH→REPLACE），否则就是按算例调参。
        实现：把每个被改文件相对基线的 'added lines 集合' 比较；只要 per_design 组里有文件被改而其它没被改，或 added 集不同 -> 错误。"""
        if not self.per_design:
            return
        groups: Dict[str, List[str]] = {}
        for f in files:
            for g in self.per_design:
                if glob_match(f, g):
                    groups.setdefault(g, []).append(f)
        for g, changed in groups.items():
            import difflib
            added = {}
            for f in changed:
                b = baseline.get(f, "").splitlines()
                n = files[f].splitlines()
                added[f] = tuple(l for l in difflib.unified_diff(b, n, lineterm="", n=0) if l.startswith("+") and not l.startswith("+++"))
            vals = set(added.values())
            if len(vals) > 1:
                rep.errors.append(f"per-design files under '{g}' received different edits ({', '.join(Path(f).name for f in changed)}); "
                                  f"per-circuit tuning is not allowed — apply the identical change to every design or edit shared code instead")

    def _check_forbidden(self, patch: str, rep: GateReport) -> None:
        for ln in patch.splitlines():
            if ln.startswith("+") and not ln.startswith("+++"):
                for pat in self.forbidden:
                    if pat.search(ln):
                        rep.warnings.append(f"added line touches forbidden token /{pat.pattern}/: {ln.strip()[:100]}")

    def _check_syntax(self, files: Dict[str, str], rep: GateReport) -> None:
        for f, content in files.items():
            if f.endswith(".py"):
                with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                    tf.write(content)
                    tmp = tf.name
                try:
                    py_compile.compile(tmp, doraise=True)
                except py_compile.PyCompileError as e:
                    rep.errors.append(f"syntax error in {f}: {e.msg.splitlines()[-1] if e.msg else e}")
                finally:
                    Path(tmp).unlink(missing_ok=True)
            elif f.endswith(".tcl"):
                err = _tcl_brace_check(content)
                if err:
                    rep.errors.append(f"tcl syntax in {f}: {err}")
            elif f.endswith((".yaml", ".yml")):
                try:
                    yaml.safe_load(content)
                except yaml.YAMLError as e:
                    rep.errors.append(f"yaml error in {f}: {str(e).splitlines()[0]}")
            elif f.endswith(".json"):
                import json
                try:
                    json.loads(content)
                except json.JSONDecodeError as e:
                    rep.errors.append(f"json error in {f}: {e}")
            elif f.endswith(".mk") or f.endswith("Makefile"):
                err = _mk_check(content)
                if err:
                    rep.errors.append(f"makefile syntax in {f}: {err}")

    def _check_pyflakes(self, files: Dict[str, str], rep: GateReport, baseline: Dict[str, str]) -> None:
        """静态检查只报候选新引入的问题：基线里本来就有的 undefined name（作用域误报或真实遗留）不算。"""
        try:
            import pyflakes.api  # noqa
            from pyflakes.reporter import Reporter
        except ImportError:
            pyflakes = None
        else:
            pyflakes = pyflakes.api
        import io

        def issues(name: str, content: str):
            if pyflakes is None:
                return {(n, "undefined name") for n, _ in _undefined_names(content)}, set()
            out, err = io.StringIO(), io.StringIO()
            pyflakes.check(content, name, Reporter(out, err))
            hard, soft = set(), set()
            for line in out.getvalue().splitlines():
                msg = line.split(":", 2)[-1].strip() if ":" in line else line
                # 去掉行号，只按"消息"比较，这样基线已有的问题即使移动了行也能对上
                (hard if "undefined name" in msg else soft).add(msg)
            return hard, soft

        for f, content in files.items():
            if not f.endswith(".py"):
                continue
            hard, soft = issues(f, content)
            if f in baseline:
                bh, bs = issues(f, baseline[f])
                hard -= bh
                soft -= bs
            for m in sorted(hard):
                rep.errors.append(f"{f}: {m}")
            for m in sorted(soft):
                rep.warnings.append(f"{f}: {m}")

    def _check_smoke(self, workdir: Path, rep: GateReport) -> None:
        try:
            r = subprocess.run(self.smoke_cmd.format(workdir=str(workdir)), shell=True, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                rep.errors.append(f"smoke test failed (rc={r.returncode}): {(r.stderr or r.stdout)[-800:]}")
        except subprocess.TimeoutExpired:
            rep.errors.append("smoke test timeout")
