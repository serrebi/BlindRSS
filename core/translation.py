import logging
from typing import Iterable, List

import requests

from core import utils

log = logging.getLogger(__name__)

_XAI_CHAT_COMPLETIONS_URL = "https://api.x.ai/v1/chat/completions"
_DEFAULT_MODEL_CANDIDATES = (
    "grok-3-mini",
    "grok-3",
    "grok-4",
    "grok-beta",
)
_DEFAULT_TIMEOUT_S = 45
_DEFAULT_CHUNK_CHARS = 3500
_MAX_TOTAL_CHARS = 50000


def _clean_target_language(target_language: str | None) -> str:
    value = str(target_language or "").strip()
    return value or "en"


def _iter_text_chunks(text: str, max_chars: int = _DEFAULT_CHUNK_CHARS) -> Iterable[str]:
    """Split text into translation-friendly chunks while preserving order."""
    s = str(text or "")
    if not s:
        return []

    try:
        max_chars = max(200, int(max_chars or _DEFAULT_CHUNK_CHARS))
    except Exception:
        max_chars = _DEFAULT_CHUNK_CHARS

    if len(s) <= max_chars:
        return [s]

    chunks: List[str] = []
    start = 0
    n = len(s)
    while start < n:
        end = min(n, start + max_chars)
        if end < n:
            # Prefer paragraph/newline boundaries for better translation continuity.
            split_at = s.rfind("\n\n", start, end)
            if split_at == -1:
                split_at = s.rfind("\n", start, end)
            if split_at == -1:
                split_at = s.rfind(" ", start, end)
            if split_at != -1 and split_at > start + 200:
                end = split_at
        chunk = s[start:end]
        if chunk:
            chunks.append(chunk)
        if end <= start:
            end = min(n, start + max_chars)
            if end <= start:
                break
        start = end
    return chunks


def _extract_chat_completion_text(payload: dict) -> str:
    try:
        choices = payload.get("choices") or []
        first = choices[0] if choices else {}
        msg = (first.get("message") or {})
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    txt = part.get("text")
                    if isinstance(txt, str) and txt:
                        parts.append(txt)
            if parts:
                return "".join(parts).strip()
    except Exception:
        pass
    return ""


def _model_not_found_error(resp: requests.Response | None, err_text: str = "") -> bool:
    if resp is not None:
        try:
            if int(getattr(resp, "status_code", 0) or 0) not in (400, 404):
                return False
        except Exception:
            return False
        try:
            data = resp.json()
            msg = str(data.get("error") or data.get("message") or data).lower()
            return ("model" in msg) and ("not found" in msg or "unknown" in msg or "invalid" in msg)
        except Exception:
            pass
    txt = str(err_text or "").lower()
    return ("model" in txt) and ("not found" in txt or "unknown" in txt or "invalid" in txt)


def _translate_chunk_grok(
    chunk: str,
    *,
    api_key: str,
    target_language: str,
    model: str | None = None,
    model_candidates: Iterable[str] | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    endpoint: str = _XAI_CHAT_COMPLETIONS_URL,
) -> str:
    api_key = str(api_key or "").strip()
    if not api_key:
        raise RuntimeError("Missing Grok API key.")
    target_language = _clean_target_language(target_language)
    chunk = str(chunk or "")
    if not chunk:
        return ""

    headers = dict(utils.HEADERS)
    headers["Authorization"] = f"Bearer {api_key}"
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"

    explicit_model = str(model or "").strip()
    if explicit_model:
        candidates = [explicit_model]
    else:
        candidates = [str(m).strip() for m in (model_candidates or _DEFAULT_MODEL_CANDIDATES) if str(m).strip()]
        if not candidates:
            candidates = list(_DEFAULT_MODEL_CANDIDATES)

    system_prompt = (
        "You are a translation engine. Translate the user's text into the requested target language. "
        "Preserve line breaks, headings, and overall formatting. Return only the translated text with no commentary."
    )
    user_prompt = f"Target language: {target_language}\n\nText to translate:\n{chunk}"

    last_err = None
    for model in candidates:
        try:
            resp = requests.post(
                endpoint,
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "stream": False,
                },
                timeout=max(5, int(timeout_s or _DEFAULT_TIMEOUT_S)),
            )
            if not getattr(resp, "ok", False):
                err_text = ""
                try:
                    err_text = resp.text or ""
                except Exception:
                    err_text = ""
                if _model_not_found_error(resp, err_text):
                    last_err = RuntimeError(f"Grok model '{model}' unavailable")
                    continue
                try:
                    resp.raise_for_status()
                except Exception as e:
                    raise RuntimeError(str(e) or "Translation request failed") from e

            data = resp.json() if resp is not None else {}
            translated = _extract_chat_completion_text(data)
            if translated:
                return translated
            raise RuntimeError("Grok returned an empty translation response.")
        except Exception as e:
            last_err = e
            if _model_not_found_error(getattr(e, "response", None), str(e)):
                continue
            break

    raise RuntimeError(str(last_err) or "Translation failed")


def translate_text_grok(
    text: str,
    *,
    api_key: str,
    target_language: str,
    model: str | None = None,
    model_candidates: Iterable[str] | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    chunk_chars: int = _DEFAULT_CHUNK_CHARS,
    endpoint: str = _XAI_CHAT_COMPLETIONS_URL,
) -> str:
    raw = str(text or "")
    if not raw.strip():
        return raw

    # Avoid accidentally sending extremely large content in a single UI action.
    if len(raw) > _MAX_TOTAL_CHARS:
        raw = raw[:_MAX_TOTAL_CHARS]

    translated_chunks: List[str] = []
    for chunk in _iter_text_chunks(raw, max_chars=chunk_chars):
        translated_chunks.append(
            _translate_chunk_grok(
                chunk,
                api_key=api_key,
                target_language=target_language,
                model=model,
                model_candidates=model_candidates,
                timeout_s=timeout_s,
                endpoint=endpoint,
            )
        )
    return "".join(translated_chunks)


def translate_text(
    text: str,
    *,
    provider: str,
    api_key: str,
    target_language: str,
    grok_model: str | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    chunk_chars: int = _DEFAULT_CHUNK_CHARS,
) -> str:
    prov = str(provider or "").strip().lower()
    if prov == "grok":
        return translate_text_grok(
            text,
            api_key=api_key,
            target_language=target_language,
            model=grok_model,
            timeout_s=timeout_s,
            chunk_chars=chunk_chars,
        )
    raise RuntimeError(f"Unsupported translation provider: {provider}")
