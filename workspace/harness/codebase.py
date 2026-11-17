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
    def __init__(self, root: Path, allowed_globs: List[str], denied_globs: List[str] = (), overlay_root: Optional[Path] = None):
        """overlay_root：若给定，同名文件优先从这里读（父代工作副本），其余从 root（官方基线）。"""
        self.root = root
        self.overlay_root = overlay_root
        self.allowed = allowed_globs
        self.denied = list(denied_globs)
        self.files: List[str] = []
        self.chunks: List[Chunk] = []
        self._scan()

    @classmethod
    def overlay(cls, base: "Codebase", overlay_root: Path) -> "Codebase":
        return cls(base.root, base.allowed, base.denied, overlay_root=overlay_root)

    def _read_rel(self, rel: str) -> str:
        if self.overlay_root is not None and (self.overlay_root / rel).exists():
            return (self.overlay_root / rel).read_text(encoding="utf-8", errors="ignore")
        return (self.root / rel).read_text(encoding="utf-8", errors="ignore")

    # ---------- 索引 ----------
    def _is_allowed(self, rel: str) -> bool:
        return match_any(rel, self.allowed) and not match_any(rel, self.denied)

    def _scan(self) -> None:
        if not self.root.exists():
            return
        rels = set()
        for base in (self.root, self.overlay_root):
            if base is None or not base.exists():
                continue
            for p in base.rglob("*"):
                if p.is_file():
                    rel = str(p.relative_to(base)).replace("\\", "/")
                    if rel != "candidate.patch" and self._is_allowed(rel):
                        rels.add(rel)
        for rel in sorted(rels):
            self.files.append(rel)
            src = self._read_rel(rel)
            if rel.endswith(".py"):
                self.chunks.extend(self._chunk_py(rel, src))
            else:
                self.chunks.append(Chunk(rel, "<file>", 1, src.count("\n") + 1, src))

    @staticmethod
    def _chunk_py(rel: str, src: str) -> List[Chunk]:
        """函数级分块；类按方法再切（DREAMPlace 的类动辄上千行，整类塞 prompt 会挤掉其它片段）。"""
        out: List[Chunk] = []
        lines = src.splitlines()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return [Chunk(rel, "<module>", 1, len(lines), src)]
        covered = set()

        def add(node, name):
            s, e = node.lineno, getattr(node, "end_lineno", node.lineno)
            # 把装饰器行也带上
            for d in getattr(node, "decorator_list", []):
                s = min(s, d.lineno)
            out.append(Chunk(rel, name, s, e, "\n".join(lines[s - 1:e])))
            covered.update(range(s, e + 1))

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add(node, node.name)
            elif isinstance(node, ast.ClassDef):
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if not methods or (getattr(node, "end_lineno", node.lineno) - node.lineno) < 80:
                    add(node, node.name)
                    continue
                # 类头（签名 + 类级属性，到第一个方法之前）
                first = min(min([m.lineno] + [d.lineno for d in m.decorator_list]) for m in methods)
                s = node.lineno
                for d in node.decorator_list:
                    s = min(s, d.lineno)
                out.append(Chunk(rel, node.name, s, first - 1, "\n".join(lines[s - 1:first - 1])))
                covered.update(range(s, first))
                for m in methods:
                    add(m, f"{node.name}.{m.name}")
        # 模块级剩余代码（import、常量、配置）作为一块
        rest = [ln for i, ln in enumerate(lines, 1) if i not in covered]
        if any(l.strip() for l in rest):
            out.insert(0, Chunk(rel, "<module>", 1, len(lines), "\n".join(rest)))
        return out

    # ---------- 检索 ----------
    def search(self, keywords: List[str], k: int = 6, max_chars: int = 6000, max_chunk_chars: int = 2500,
               max_per_file: int = 3) -> List[Chunk]:
        """关键字打分（名称命中 ×3），按分数取前 k 块；单块超过 max_chunk_chars 的截断到命中行附近。
        每个文件最多 max_per_file 块，避免 6 块全来自一个文件、模型看不到别的地方。"""
        scored: List[Tuple[float, Chunk]] = []
        for c in self.chunks:
            low = c.source.lower()
            s = sum(low.count(kw.lower()) * (3 if kw.lower() in c.name.lower() else 1) for kw in keywords)
            if s:
                # 归一化：长块命中多是自然的，略压一下
                scored.append((s / (1 + len(c.source) / 4000), c))
        scored.sort(key=lambda t: -t[0])
        picked, used = [], 0
        per_file: Dict[str, int] = {}
        for _, c in scored:
            if per_file.get(c.file, 0) >= max_per_file:
                continue
            cc = c if len(c.source) <= max_chunk_chars else self._window(c, keywords, max_chunk_chars)
            if used + len(cc.source) > max_chars:
                continue
            picked.append(cc)
            per_file[c.file] = per_file.get(c.file, 0) + 1
            used += len(cc.source)
            if len(picked) >= k:
                break
        return picked

    @staticmethod
    def _window(c: Chunk, keywords: List[str], max_chars: int) -> Chunk:
        """从大块里截取"关键字命中最密集"的连续行窗口，并修正行号。"""
        lines = c.source.splitlines()
        kws = [k.lower() for k in keywords]
        hits = [i for i, ln in enumerate(lines) if any(k in ln.lower() for k in kws)]
        if not hits:
            hits = [0]
        # 以命中行中位数为中心向两侧扩
        center = hits[len(hits) // 2]
        lo = hi = center
        size = len(lines[center]) + 1
        while True:
            grown = False
            if lo > 0 and size + len(lines[lo - 1]) + 1 <= max_chars:
                lo -= 1; size += len(lines[lo]) + 1; grown = True
            if hi < len(lines) - 1 and size + len(lines[hi + 1]) + 1 <= max_chars:
                hi += 1; size += len(lines[hi]) + 1; grown = True
            if not grown:
                break
        return Chunk(c.file, c.name + " (excerpt)", c.start + lo, c.start + hi, "\n".join(lines[lo:hi + 1]))

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
        return self._read_rel(rel)


@dataclass
class Edit:
    file: str
    search: str
    replace: str


def apply_edits(base_root: Path, work_root: Path, edits: List[Edit], fallback_root: Optional[Path] = None) -> Tuple[Dict[str, str], List[str]]:
    """
    依次应用 SEARCH/REPLACE。文件来源优先级：work_root 已有 > base_root > fallback_root（父代工作副本只含父代改过的文件，
    其它文件要回退到官方基线）。返回 ({relpath: new_content}, errors)。SEARCH 必须唯一精确匹配。
    """
    import shutil
    changed: Dict[str, str] = {}
    errors: List[str] = []
    for e in edits:
        dst = work_root / e.file
        if e.file not in changed:
            if not dst.exists():
                src = None
                for root in (base_root, fallback_root):
                    if root is not None and (root / e.file).exists():
                        src = root / e.file
                        break
                if src is None:
                    errors.append(f"{e.file}: file does not exist in baseline")
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
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
