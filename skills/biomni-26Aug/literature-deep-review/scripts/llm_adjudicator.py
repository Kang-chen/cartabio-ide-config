#!/usr/bin/env python3
"""Small provider adapter for claim-to-passage evidence adjudication.

The model sees only a bounded set of candidate source blocks. It must return
verbatim quotations and stable block IDs; evidence_first.py performs the
authoritative deterministic validation after the call.
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any


EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "block_id": {"type": "string"},
                    "quote": {"type": "string"},
                    "stance": {
                        "type": "string",
                        "enum": ["supports", "contradicts", "mentions"],
                    },
                    "evidence_kind": {
                        "type": "string",
                        "enum": [
                            "primary", "indirect", "control", "secondary",
                            "correlative", "inferred",
                        ],
                    },
                    "scope_note": {"type": "string"},
                    "rationale": {"type": "string"},
                    "needs_figure_review": {"type": "boolean"},
                },
                "required": [
                    "claim_id", "block_id", "quote", "stance",
                    "evidence_kind", "scope_note", "rationale",
                    "needs_figure_review",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["evidence"],
    "additionalProperties": False,
}


class AdjudicationError(RuntimeError):
    pass


def build_prompt(paper: dict, claims: list[dict], blocks: list[dict]) -> str:
    compact_claims = [
        {"claim_id": c["claim_id"], "claim_text": c["claim_text"]}
        for c in claims
    ]
    compact_blocks = [
        {
            "block_id": b["block_id"],
            "type": b["block_type"],
            "page": b.get("page"),
            "section": b.get("section"),
            "figure_id": b.get("figure_id"),
            "text": b["text"],
        }
        for b in blocks
    ]
    return (
        "Map scientific claims to evidence in one paper. Use only the supplied "
        "source blocks. Return zero or more evidence rows. The quote must be an "
        "exact verbatim substring of the selected block. Label a mere term "
        "appearance as mentions, not supports. Label contradictions only when "
        "the block directly bears on the same scoped claim. Use primary only "
        "for this paper's own direct result; use secondary when it reports a "
        "cited study. Use inferred for reviewer reasoning, which cannot directly "
        "support a claim. Set needs_figure_review only when the caption indicates "
        "that panel-level evidence is required. Do not summarize outside JSON.\n\n"
        f"PAPER:\n{json.dumps(paper, ensure_ascii=False)}\n\n"
        f"CLAIMS:\n{json.dumps(compact_claims, ensure_ascii=False)}\n\n"
        f"SOURCE BLOCKS:\n{json.dumps(compact_blocks, ensure_ascii=False)}"
    )


def adjudicate(
    backend: str,
    model: str,
    prompt: str,
    *,
    timeout: int = 180,
    max_retries: int = 4,
) -> tuple[list[dict], dict]:
    payload, meta = request_json(
        backend,
        model,
        prompt,
        schema=EVIDENCE_SCHEMA,
        schema_name="claim_evidence",
        timeout=timeout,
        max_retries=max_retries,
    )
    evidence = payload.get("evidence") if isinstance(payload, dict) else None
    if not isinstance(evidence, list):
        raise AdjudicationError("provider response did not contain an evidence array")
    return evidence, meta


def request_json(
    backend: str,
    model: str,
    prompt: str,
    *,
    schema: dict[str, Any],
    schema_name: str,
    timeout: int = 180,
    max_retries: int = 4,
) -> tuple[dict, dict]:
    """Request one schema-constrained JSON object from a supported provider."""
    if backend == "gemini":
        return _gemini(
            model, prompt, schema, schema_name, timeout, max_retries
        )
    elif backend == "openai":
        return _openai(
            model, prompt, schema, schema_name, timeout, max_retries
        )
    elif backend == "ollama":
        return _ollama(
            model, prompt, schema, schema_name, timeout, max_retries
        )
    else:
        raise AdjudicationError(f"unsupported backend: {backend}")


def _post(url: str, *, headers: dict, body: dict, timeout: int, max_retries: int):
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - install-time failure
        raise AdjudicationError("requests is required for cloud/local model calls") from exc

    last = "unknown error"
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout)
            if response.status_code < 400:
                return response.json(), dict(response.headers)
            last = f"HTTP {response.status_code}: {response.text[:500]}"
            if response.status_code != 429 and response.status_code < 500:
                break
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        if attempt < max_retries:
            time.sleep(min(30.0, 2.0**attempt) + random.random())
    raise AdjudicationError(last)


def _gemini(
    model: str,
    prompt: str,
    schema: dict[str, Any],
    _schema_name: str,
    timeout: int,
    max_retries: int,
):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise AdjudicationError("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    data, headers = _post(
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        body={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        },
        timeout=timeout,
        max_retries=max_retries,
    )
    try:
        text = "".join(
            part.get("text", "")
            for part in data["candidates"][0]["content"]["parts"]
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise AdjudicationError(f"unexpected Gemini response: {str(data)[:500]}") from exc
    return _parse_json(text), {
        "provider": "gemini",
        "model": data.get("modelVersion", model),
        "request_id": data.get("responseId") or headers.get("x-request-id"),
        "usage": data.get("usageMetadata", {}),
    }


def _openai(
    model: str,
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    timeout: int,
    max_retries: int,
):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise AdjudicationError("OPENAI_API_KEY is not set")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    data, headers = _post(
        f"{base}/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        body={
            "model": model,
            "input": prompt,
            "store": False,
            "temperature": 0,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        },
        timeout=timeout,
        max_retries=max_retries,
    )
    text = data.get("output_text") or ""
    if not text:
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") == "output_text":
                    text += content.get("text", "")
    if not text:
        raise AdjudicationError(f"unexpected OpenAI response: {str(data)[:500]}")
    return _parse_json(text), {
        "provider": "openai",
        "model": data.get("model", model),
        "request_id": data.get("id") or headers.get("x-request-id"),
        "usage": data.get("usage", {}),
    }


def _ollama(
    model: str,
    prompt: str,
    schema: dict[str, Any],
    _schema_name: str,
    timeout: int,
    max_retries: int,
):
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    data, _ = _post(
        f"{host}/api/chat",
        headers={"Content-Type": "application/json"},
        body={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=timeout,
        max_retries=max_retries,
    )
    text = ((data.get("message") or {}).get("content") or "").strip()
    if not text:
        raise AdjudicationError(f"unexpected Ollama response: {str(data)[:500]}")
    return _parse_json(text), {
        "provider": "ollama",
        "model": data.get("model", model),
        "request_id": None,
        "usage": {
            "input_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
        },
    }


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise AdjudicationError(f"model did not return JSON: {text[:500]}") from exc
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as inner:
            raise AdjudicationError(f"invalid model JSON: {text[:500]}") from inner
    if not isinstance(value, dict):
        raise AdjudicationError("model JSON must be an object")
    return value
