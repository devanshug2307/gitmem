"""Vertex AI express-mode bridge for litellm.

Some Google keys (the `AQ.…` "express mode" kind) only work against
`aiplatform.googleapis.com` — litellm's `gemini/` provider can't use them
(it targets generativelanguage) and its `vertex_ai/` provider demands full
GCP credentials + project id.

This module monkeypatches `litellm.acompletion` / `litellm.completion`:
calls for `gemini/*` models are translated to a raw Vertex express
`generateContent` request and the reply is wrapped back into a
`litellm.ModelResponse`. Everything else passes through untouched.

cognee's Gemini adapter runs instructor in `json_mode` (schema in the prompt,
`response_format={"type": "json_object"}`), which maps 1:1 onto Vertex's
`responseMimeType: application/json` — so structured extraction works without
any tool-call translation.

Activate by setting VERTEX_EXPRESS_KEY and calling install() before cognee
makes its first LLM call.
"""

from __future__ import annotations

import os
import time
import uuid

import aiohttp
import litellm

ENDPOINT = "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent"

_orig_acompletion = litellm.acompletion
_orig_completion = litellm.completion


def _to_vertex_payload(messages: list[dict], kwargs: dict) -> dict:
    system_parts, contents = [], []
    for m in messages:
        role, content = m.get("role"), m.get("content") or ""
        if isinstance(content, list):  # multimodal-style parts; keep text only
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role == "system":
            system_parts.append({"text": content})
        else:
            contents.append(
                {"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]}
            )

    gen_cfg: dict = {}
    if kwargs.get("temperature") is not None:
        gen_cfg["temperature"] = kwargs["temperature"]
    max_toks = kwargs.get("max_completion_tokens") or kwargs.get("max_tokens")
    if max_toks:
        gen_cfg["maxOutputTokens"] = max_toks
    rf = kwargs.get("response_format")
    if isinstance(rf, dict) and "json" in str(rf.get("type", "")):
        gen_cfg["responseMimeType"] = "application/json"
        schema = (rf.get("json_schema") or {}).get("schema")
        if schema:
            gen_cfg["responseSchema"] = schema

    payload: dict = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    if gen_cfg:
        payload["generationConfig"] = gen_cfg
    return payload


def _to_model_response(model: str, data: dict) -> litellm.ModelResponse:
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    usage = data.get("usageMetadata", {})
    return litellm.ModelResponse(
        id=f"vertex-express-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=model,
        choices=[
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        usage={
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    )


def install() -> bool:
    """Patch litellm if VERTEX_EXPRESS_KEY is set. Returns True when active."""
    key = (os.getenv("VERTEX_EXPRESS_KEY") or "").strip().strip('"')
    if not key:
        return False

    async def patched_acompletion(*args, **kwargs):
        model = kwargs.get("model") or (args[0] if args else "")
        if not str(model).startswith("gemini/"):
            return await _orig_acompletion(*args, **kwargs)
        vertex_model = str(model).split("/", 1)[1]
        payload = _to_vertex_payload(kwargs.get("messages") or [], kwargs)
        timeout = aiohttp.ClientTimeout(total=float(kwargs.get("timeout") or 120))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                ENDPOINT.format(model=vertex_model),
                params={"key": key},
                json=payload,
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    raise litellm.APIConnectionError(
                        message=f"vertex express {resp.status}: {str(body)[:300]}",
                        llm_provider="vertex_express",
                        model=str(model),
                    )
        return _to_model_response(str(model), body)

    def patched_completion(*args, **kwargs):
        model = kwargs.get("model") or (args[0] if args else "")
        if not str(model).startswith("gemini/"):
            return _orig_completion(*args, **kwargs)
        import asyncio

        return asyncio.get_event_loop().run_until_complete(patched_acompletion(*args, **kwargs))

    litellm.acompletion = patched_acompletion
    litellm.completion = patched_completion
    return True
