"""MiMo V2.5 клієнт: збір messages, виклик API, парсинг JSON-відповіді."""

import base64
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

_EMPTY_EXTRACTED = {"shelf_type": None, "content": None, "profile_query": None}


class MimoClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def _complete(self, messages: list[dict]) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "messages": messages}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def ask(self, system_prompt: str, history: list[dict], user_content) -> dict:
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_content})
        raw = await self._complete(messages)
        return parse_response(raw)


class GeminiClient:
    """Gemini (Google AI Studio) омні-клієнт — заміна MiMo для української.

    Та сама сигнатура ask(), той самий JSON-контракт (parse_response). Аудіо/зір
    приймаються нативно; responseMimeType=application/json гарантує валідний JSON.
    Конвертує OpenAI-стиль блоків (text/image_url/input_audio) у Gemini parts.
    """

    def __init__(self, api_key: str, model: str, timeout: float = 60.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @staticmethod
    def _to_parts(content) -> list[dict]:
        if isinstance(content, str):
            return [{"text": content}] if content else []
        parts: list[dict] = []
        for b in content or []:
            t = b.get("type")
            if t == "text" and b.get("text"):
                parts.append({"text": b["text"]})
            elif t == "image_url":
                url = b["image_url"]["url"]
                if url.startswith("data:"):
                    head, data = url.split(",", 1)
                    mime = head[5:].split(";")[0] or "image/jpeg"
                    parts.append({"inlineData": {"mimeType": mime, "data": data}})
            elif t == "input_audio":
                ia = b["input_audio"]
                fmt = (ia.get("format") or "ogg")
                parts.append({"inlineData": {"mimeType": f"audio/{fmt}", "data": ia["data"]}})
        return parts

    async def _complete(self, system_prompt: str, history: list[dict], user_content) -> str:
        contents: list[dict] = []
        for m in history:
            parts = self._to_parts(m.get("content", ""))
            if parts:
                role = "model" if m.get("role") == "assistant" else "user"
                contents.append({"role": role, "parts": parts})
        contents.append({"role": "user", "parts": self._to_parts(user_content)})
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    async def ask(self, system_prompt: str, history: list[dict], user_content) -> dict:
        raw = await self._complete(system_prompt, history, user_content)
        return parse_response(raw)


def parse_response(raw: str) -> dict:
    """Парсить JSON-контракт моделі. Якщо парсинг не вдався — фолбек у chat."""
    text = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or "intent" not in data:
            raise ValueError("missing intent")
    except (json.JSONDecodeError, ValueError):
        logger.warning("MiMo response is not valid JSON, falling back to chat: %s", raw[:200])
        return {"intent": "chat", "reply": raw.strip(), "extracted": dict(_EMPTY_EXTRACTED)}

    extracted = data.get("extracted") or {}
    data["extracted"] = {
        "shelf_type": extracted.get("shelf_type"),
        "content": extracted.get("content"),
        "profile_query": extracted.get("profile_query"),
        "remind_at": extracted.get("remind_at"),
        "recurring": extracted.get("recurring"),
    }
    data.setdefault("reply", "")
    return data


def build_user_content(
    text: str | None = None,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    audio_bytes: bytes | None = None,
    audio_mime: str | None = None,
):
    """Будує content для повідомлення користувача: рядок або список мультимодальних блоків."""
    blocks = []
    if text:
        blocks.append({"type": "text", "text": text})
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{image_mime or 'image/jpeg'};base64,{b64}"},
        })
    if audio_bytes:
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        fmt = (audio_mime or "audio/ogg").split("/")[-1]
        blocks.append({
            "type": "input_audio",
            "input_audio": {"data": b64, "format": fmt},
        })

    if not blocks:
        return ""
    if len(blocks) == 1 and blocks[0]["type"] == "text":
        return blocks[0]["text"]
    return blocks
