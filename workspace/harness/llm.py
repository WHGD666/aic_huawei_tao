"""
llm.py -- DeepSeek 客户端。只依赖 requests。

* OpenAI 兼容的 /chat/completions
* 自动重试（网络错误 / 429 / 5xx）
* 返回 Usage（含 prompt_cache_hit_tokens）供 Budget 记账
* record/replay：开发阶段可把真实响应录下来，之后用 replay 模式零费用调试循环
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .budget import Usage


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: int = 120,
        max_retries: int = 3,
        record_dir: Optional[Path] = None,
        mode: str = "live",  # live | record | replay
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.mode = mode
        self.record_dir = record_dir
        if mode in ("record", "replay") and record_dir is None:
            raise ValueError("record/replay mode requires record_dir")
        if mode != "replay" and not self.api_key:
            raise LLMError("DEEPSEEK_API_KEY not set")

    # ---------- public ----------
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tag: str = "",
    ) -> Tuple[str, Usage]:
        key = self._cache_key(messages, temperature, max_tokens)
        if self.mode == "replay":
            return self._replay(key)

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        text, usage, raw = self._post(payload)
        if self.mode == "record":
            self._record(key, raw, tag)
        return text, usage

    def list_models(self) -> List[str]:
        r = requests.get(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    # ---------- internals ----------
    def _post(self, payload: dict):
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=self.timeout_s)
                if r.status_code == 200:
                    data = r.json()
                    choice = data["choices"][0]
                    text = choice["message"]["content"] or ""
                    u = data.get("usage", {})
                    usage = Usage(
                        prompt_tokens=int(u.get("prompt_tokens", 0)),
                        completion_tokens=int(u.get("completion_tokens", 0)),
                        cache_hit_tokens=int(u.get("prompt_cache_hit_tokens", 0)),
                    )
                    return text, usage, data
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")
            except requests.RequestException as e:
                last_err = LLMError(str(e))
                time.sleep(2 ** attempt)
        raise last_err or LLMError("unknown error")

    @staticmethod
    def _cache_key(messages, temperature, max_tokens) -> str:
        blob = json.dumps({"m": messages, "t": temperature, "k": max_tokens}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]

    def _record(self, key: str, raw: dict, tag: str) -> None:
        self.record_dir.mkdir(parents=True, exist_ok=True)
        with open(self.record_dir / f"{key}.json", "w", encoding="utf-8") as f:
            json.dump({"tag": tag, "raw": raw}, f, ensure_ascii=False, indent=1)

    def _replay(self, key: str) -> Tuple[str, Usage]:
        p = self.record_dir / f"{key}.json"
        if not p.exists():
            raise LLMError(f"replay miss for key {key}; record it first")
        raw = json.load(open(p, encoding="utf-8"))["raw"]
        text = raw["choices"][0]["message"]["content"] or ""
        u = raw.get("usage", {})
        return text, Usage(
            int(u.get("prompt_tokens", 0)),
            int(u.get("completion_tokens", 0)),
            int(u.get("prompt_cache_hit_tokens", 0)),
        )
