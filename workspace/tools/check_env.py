"""check_env.py -- 一键体检：Python/依赖/路径/网络/GPU/Docker。全绿才算 M1 完成。"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
ROOT = WS.parent
OK, BAD, WARN = "✓", "✗", "!"


def row(flag, name, detail=""):
    print(f" {flag} {name:<28} {detail}")


def main():
    print(f"workspace: {WS}")
    row(OK if sys.version_info >= (3, 10) else BAD, "python", sys.version.split()[0] + f"  ({sys.executable})")
    # 路径必须不在 C 盘
    exe = sys.executable.lower()
    row(OK if not exe.startswith("c:") else WARN, "python not on C:", exe[:3])
    for var in ("CONDA_PKGS_DIRS", "PIP_CACHE_DIR", "TMP", "TORCH_HOME"):
        v = os.environ.get(var, "")
        row(OK if v and not v.lower().startswith("c:") else WARN, f"env {var}", v or "(unset) -> run env/00_setenv.bat")
    # 依赖
    for mod in ("requests", "yaml", "pyflakes", "pytest"):
        try:
            m = importlib.import_module(mod)
            row(OK, f"import {mod}", getattr(m, "__version__", ""))
        except ImportError:
            row(BAD, f"import {mod}", "pip install -r requirements-harness.txt")
    # .env
    env = WS / ".env"
    row(OK if env.exists() else BAD, ".env", str(env))
    # git
    g = shutil.which("git")
    row(OK if g else BAD, "git", g or "not found in PATH")
    # docker
    d = shutil.which("docker")
    if d:
        try:
            out = subprocess.run(["docker", "info", "--format", "{{.DockerRootDir}} | {{.OSType}}"], capture_output=True, text=True, timeout=20)
            row(OK if out.returncode == 0 else WARN, "docker", out.stdout.strip() or out.stderr.strip()[:80])
        except Exception as e:
            row(WARN, "docker", str(e)[:80])
    else:
        row(WARN, "docker", "not found (needed for OpenROAD / official container)")
    # GPU
    n = shutil.which("nvidia-smi")
    if n:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True)
        row(OK, "nvidia-smi", out.stdout.strip()[:80])
    else:
        row(WARN, "nvidia-smi", "not in PATH")
    # baseline
    b = WS / "baseline"
    n_files = sum(1 for p in b.rglob("*") if p.is_file() and p.name != ".gitkeep")
    row(OK if n_files else WARN, "baseline/", f"{n_files} files" if n_files else "empty -> waiting for official code")
    # DeepSeek connectivity (optional; costs nothing for /models)
    if "--net" in sys.argv:
        sys.path.insert(0, str(WS))
        from harness.loop import load_dotenv
        from harness.llm import LLMClient
        load_dotenv(env)
        try:
            c = LLMClient()
            ms = c.list_models()
            row(OK, "deepseek /models", ", ".join(ms)[:80])
        except Exception as e:
            row(BAD, "deepseek /models", str(e)[:100])


if __name__ == "__main__":
    main()
