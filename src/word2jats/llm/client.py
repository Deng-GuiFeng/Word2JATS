"""统一 LLM 客户端（可选语义增强层）。

设计要点（对齐落地性/稳定性要求）：
- **可降级**：provider='off' 或缺 Key 时 :meth:`enabled` 为 False，所有调用返回 None，
  上层回退到纯规则路径，保证产出完整 XML。
- **provider 无关**：DeepSeek / DashScope 均为 OpenAI 兼容接口（已实测确认），统一用
  openai SDK + base_url 调用 chat.completions。
- **强约束 + 低温**：temperature=0、response_format=json_object，规则法后校验，抑制幻觉。
- **缓存 + 计量**：磁盘缓存重复请求；记录调用次数/tokens，便于答辩展示成本可控。

注意：模型名 / base_url 均经官方文档核对 + 联网实测（deepseek-chat、qwen-plus、
qwen-flash 均可用，json_object 与 temperature 受支持）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .cache import DiskCache

_log = logging.getLogger(__name__)

# 各 provider 默认配置（base_url 缺省值仅作兜底，优先读 .env）
# local = 本机 sglang/vLLM(OpenAI 兼容)多卡服务,见 qwen36_deploy。无需 Key、无外网成本。
_PROVIDERS = {
    "deepseek": {
        "key_env": "DEEPSEEK_API_KEY", "url_env": "DEEPSEEK_BASE_URL",
        "default_url": "https://api.deepseek.com", "default_model": "deepseek-chat",
        "needs_key": True, "thinking_off": False, "response_format": True, "timeout": 60,
    },
    "dashscope": {
        "key_env": "DASHSCOPE_API_KEY", "url_env": "DASHSCOPE_BASE_URL",
        "default_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "needs_key": True, "thinking_off": False, "response_format": True, "timeout": 60,
    },
    "local": {
        "key_env": "LOCAL_LLM_API_KEY", "url_env": "LOCAL_LLM_BASE_URL",
        "default_url": "http://localhost:30000/v1", "default_model": None,
        "needs_key": False, "thinking_off": True, "response_format": False, "timeout": 600,
    },
}


class LLMClient:
    def __init__(self, provider: str = "off", model: Optional[str] = None,
                 cache_dir: Optional[str] = None, env_path: Optional[str] = None):
        self.provider = (provider or "off").lower()
        self.calls = 0
        self.tokens = 0
        self.failures = 0   # 模型调用失败/空响应次数(可观测,避免静默)
        self._client = None
        self.model = model
        if self.provider == "off" or self.provider not in _PROVIDERS:
            return
        self._load_env(env_path)
        self.cfg = _PROVIDERS[self.provider]
        key = os.getenv(self.cfg["key_env"]) or ("EMPTY" if not self.cfg["needs_key"] else None)
        url = os.getenv(self.cfg["url_env"]) or self.cfg["default_url"]
        self.model = model or self.cfg["default_model"]
        if not key:
            self.provider = "off"  # 需要 Key 却没有 → 退回纯规则
            return
        try:
            from openai import OpenAI
            os.environ.setdefault("no_proxy", "localhost,127.0.0.1")
            self._client = OpenAI(api_key=key, base_url=url,
                                  timeout=self.cfg["timeout"], max_retries=2)
            if self.model is None:  # 本地服务:取已加载模型 id(按 id 排序取首,避免多模型时顺序漂移)
                models = sorted(self._client.models.list().data, key=lambda m: m.id)
                self.model = models[0].id
        except Exception:
            self.provider = "off"
            self._client = None
            return
        self._cache = DiskCache(cache_dir)

    @staticmethod
    def _load_env(env_path):
        try:
            from dotenv import load_dotenv
            if env_path and os.path.exists(env_path):
                load_dotenv(env_path)
            else:
                p = os.path.join(os.getcwd(), ".env")
                if os.path.exists(p):
                    load_dotenv(p)
        except Exception:
            pass

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def extract_json(self, system: str, user: str, max_tokens: int = 4096):
        """调用 LLM 返回 JSON 对象（dict/list）；失败或未启用时返回 None。"""
        if not self.enabled:
            return None
        payload = {"provider": self.provider, "model": self.model,
                   "system": system, "user": user}
        cached = self._cache.get(payload)
        if cached is not None:
            return _safe_json(cached)
        kwargs = dict(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0,
            max_tokens=max_tokens,
        )
        if self.cfg["response_format"]:
            kwargs["response_format"] = {"type": "json_object"}
        if self.cfg["thinking_off"]:  # Qwen thinking 模型:关闭思维链以求确定、短输出
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        try:
            resp = self._client.chat.completions.create(**kwargs)
            self.calls += 1
            if resp.usage:
                self.tokens += resp.usage.total_tokens
            content = resp.choices[0].message.content
            if content and content.strip():
                self._cache.put(payload, content)  # 不缓存空响应:让瞬时失败下次可重试
            else:
                self.failures += 1
                _log.warning("extract_json 返回空内容(model=%s)", self.model)
            return _safe_json(content)
        except Exception as e:
            self.failures += 1
            _log.warning("extract_json 调用失败: %s", e)
            return None

    def chat_vision(self, prompt: str, image_path: str, max_tokens: int = 2048):
        """图文问答:看图 + 文本指令,返回纯文本。用于读图片表格、核对版式。"""
        if not self.enabled:
            return None
        import base64
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "image/jpeg")
        payload = {"provider": self.provider, "model": self.model,
                   "prompt": prompt, "img_sha": _sha(b64)}
        cached = self._cache.get(payload)
        if cached is not None:
            return cached
        kwargs = dict(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}},
                {"type": "text", "text": prompt}]}],
            temperature=0, max_tokens=max_tokens,
        )
        if self.cfg["thinking_off"]:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        try:
            resp = self._client.chat.completions.create(**kwargs)
            self.calls += 1
            if resp.usage:
                self.tokens += resp.usage.total_tokens
            content = resp.choices[0].message.content
            if content and content.strip():
                self._cache.put(payload, content)  # 不缓存空响应:让瞬时失败下次可重试
            else:
                self.failures += 1
                _log.warning("chat_vision 返回空内容(img=%s)", os.path.basename(image_path))
            return content
        except Exception as e:
            self.failures += 1
            _log.warning("chat_vision 调用失败: %s", e)
            return None

    @property
    def stats(self) -> dict:
        return {"provider": self.provider, "model": self.model,
                "calls": self.calls, "tokens": self.tokens, "failures": self.failures}


def _sha(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _safe_json(text: str):
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        # 容错：截取首个 { 或 [ 到末尾
        import re
        m = re.search(r"[\{\[].*[\}\]]", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None
