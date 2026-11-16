"""
codebase.py -- 基线代码的索引与片段检索。目的是控制 prompt 长度：永远不把整个文件塞给模型。

* 只索引白名单内文件
* 函数级分块（用 ast），每块记录 (file, name, lineno range, source)
* 关键字检索：按名称 / docstring / 源码命中次数打分，返回前 k 块
* apply_edits：把 SEARCH/REPLACE 块应用到工作副本，并生成 git diff
"""
from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def glob_match(path: str, pattern: str) -> bool:
    """gitignore 风格匹配：`**/` 表示零个或多个目录层级，`*` 不跨越 `/`。"""
    path = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.fullmatch(out, path) is not None


def match_any(path: str, patterns) -> bool:
    return any(glob_match(path, p) for p in patterns)


@dataclass
class Chunk:
    file: str          # 相对路径
    name: str          # 函数/类名 或 "<module>"
    start: int
    end: int
    source: str

    def header(self) -> str:
        return f"### {self.file}:{self.start}-{self.end}  {self.name}"


class Codebase:
    def __init__(self, root: Path, allowed_globs: List[str], denied_globs: List[str] = ()):
        self.root = root
        self.allowed = allowed_globs
        self.denied = list(denied_globs)
        self.files: List[str] = []
        self.chunks: List[Chunk] = []
        self._scan()

    # ---------- 索引 ----------
    def _is_allowed(self, rel: str) -> bool:
        return match_any(rel, self.allowed) and not match_any(rel, self.denied)

    def _scan(self) -> None:
        if not self.root.exists():
            return
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(self.root)).replace("\\", "/")
            if not self._is_allowed(rel):
                continue
            self.files.append(rel)
            if p.suffix == ".py":
                self.chunks.extend(self._chunk_py(rel, p.read_text(encoding="utf-8", errors="ignore")))
            else:
                src = p.read_text(encoding="utf-8", errors="ignore")
                self.chunks.append(Chunk(rel, "<file>", 1, src.count("\n") + 1, src))

    @staticmethod
    def _chunk_py(rel: str, src: str) -> List[Chunk]:
        out: List[Chunk] = []
        lines = src.splitlines()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return [Chunk(rel, "<module>", 1, len(lines), src)]
        covered = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                s, e = node.lineno, getattr(node, "end_lineno", node.lineno)
                out.append(Chunk(rel, node.name, s, e, "\n".join(lines[s - 1:e])))
                covered.update(range(s, e + 1))
        # 模块级剩余代码（import、常量、配置）作为一块
        rest = [ln for i, ln in enumerate(lines, 1) if i not in covered]
        if any(l.strip() for l in rest):
            out.insert(0, Chunk(rel, "<module>", 1, len(lines), "\n".join(rest)))
        return out

    # ---------- 检索 ----------
    def search(self, keywords: List[str], k: int = 6, max_chars: int = 6000) -> List[Chunk]:
        scored: List[Tuple[int, Chunk]] = []
        for c in self.chunks:
            low = c.source.lower()
            s = sum(low.count(kw.lower()) * (3 if kw.lower() in c.name.lower() else 1) for kw in keywords)
            if s:
                scored.append((s, c))
        scored.sort(key=lambda t: -t[0])
        picked, used = [], 0
        for _, c in scored:
            if used + len(c.source) > max_chars:
                continue
            picked.append(c)
            used += len(c.source)
            if len(picked) >= k:
                break
        return picked

    def render(self, chunks: List[Chunk]) -> str:
        return "\n\n".join(f"{c.header()}\n```python\n{c.source}\n```" for c in chunks)

    def outline(self) -> str:
        """文件 + 顶层符号清单，用于系统提示（很短）。"""
        by_file: Dict[str, List[str]] = {}
        for c in self.chunks:
            if c.name not in ("<module>", "<file>"):
                by_file.setdefault(c.file, []).append(c.name)
        lines = []
        for f in self.files:
            syms = by_file.get(f, [])
            lines.append(f"- {f}: {', '.join(syms[:12])}{' ...' if len(syms) > 12 else ''}")
        return "\n".join(lines)

    # ---------- 编辑 ----------
    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8", errors="ignore")


@dataclass
class Edit:
    file: str
    search: str
    replace: str


def apply_edits(base_root: Path, work_root: Path, edits: List[Edit]) -> Tuple[Dict[str, str], List[str]]:
    """
    从 base_root 复制涉及文件到 work_root（若尚不存在），依次应用 SEARCH/REPLACE。
    返回 ({relpath: new_content}, errors)。SEARCH 必须唯一精确匹配。
    """
    import shutil
    changed: Dict[str, str] = {}
    errors: List[str] = []
    for e in edits:
        src = base_root / e.file
        dst = work_root / e.file
        if not src.exists():
            errors.append(f"{e.file}: file does not exist in baseline")
            continue
        if e.file not in changed:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            changed[e.file] = dst.read_text(encoding="utf-8", errors="ignore")
        content = changed[e.file]
        n = content.count(e.search)
        if n == 0:
            errors.append(f"{e.file}: SEARCH block not found (must match exactly, incl. indentation)")
            continue
        if n > 1:
            errors.append(f"{e.file}: SEARCH block matches {n} places; make it more specific")
            continue
        changed[e.file] = content.replace(e.search, e.replace, 1)
    for f, c in changed.items():
        (work_root / f).write_text(c, encoding="utf-8")
    return changed, errors


def git_diff(base_root: Path, work_root: Path, files: List[str]) -> str:
    """用 git diff --no-index 生成标准 unified diff，路径规整为 a/<rel> b/<rel>（可直接 git apply 到基线）。"""
    parts = []
    for f in files:
        a, b = base_root / f, work_root / f
        r = subprocess.run(
            ["git", "diff", "--no-index", "--", str(a), str(b)],
            capture_output=True, text=True,
        )
        lines = []
        for ln in r.stdout.splitlines():
            if ln.startswith("diff --git "):
                lines.append(f"diff --git a/{f} b/{f}")
            elif ln.startswith("--- ") and not ln.startswith("--- /dev/null"):
                lines.append(f"--- a/{f}")
            elif ln.startswith("+++ ") and not ln.startswith("+++ /dev/null"):
                lines.append(f"+++ b/{f}")
            else:
                lines.append(ln)
        parts.append("\n".join(lines) + "\n")
    return "".join(parts)
