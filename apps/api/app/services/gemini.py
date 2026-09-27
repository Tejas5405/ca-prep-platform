"""A grounded study suggestion. Never a legal authority.

The model is called only when the caller already has excerpts the student may
read. An empty excerpt list returns None and does not open a network connection.
A failed call also returns None. The caller must keep the quotations and must
not invent a section, a rate, or a case name in its place.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_INSTRUCTION = (
    "You are a study assistant for CA students in India. You are not a legal "
    "authority, not ICAI, and not a substitute for the statute. Use only the "
    "excerpts below. If they do not contain the answer, say you cannot tell from "
    "these excerpts. Do not invent a section number, a rate, a case name, a year, "
    "or the correct option of a practice question. Write at most 120 words. "
    "Start with the words 'Study suggestion:'."
)


def build_prompt(query: str, excerpts: list[str]) -> str | None:
    """Return the prompt, or None when there is nothing to ground it on."""
    cleaned = [item.strip() for item in excerpts if item and item.strip()]
    if not cleaned:
        return None
    blocks = "\n\n".join(
        f"Excerpt {index}:\n{text[:800]}" for index, text in enumerate(cleaned[:8], start=1)
    )
    return f"{_INSTRUCTION}\n\nQuestion:\n{query.strip()[:200]}\n\n{blocks}"


async def write_suggestion(
    *,
    api_key: str,
    model: str,
    query: str,
    excerpts: list[str],
    fallback_model: str | None = None,
) -> str | None:
    """Call the provider. None means no suggestion, not a fallback explanation.

    ``fallback_model`` is CONFIGURATION (``AI_PROVIDER_FALLBACK_MODEL``), not a
    constant. It is tried only when it is set AND different from the primary
    model, so the retry can never re-run the same request against the same
    outage, and an unset value means exactly one attempt.
    """
    prompt = build_prompt(query, excerpts)
    if prompt is None or not api_key:
        return None
    if api_key in prompt:
        prompt = prompt.replace(api_key, "[redacted]")

    text = await _call(api_key, model, prompt)
    if text is None and fallback_model and fallback_model != model:
        text = await _call(api_key, fallback_model, prompt)
    if not text:
        return None
    cleaned = text.strip()
    if api_key in cleaned:
        cleaned = cleaned.replace(api_key, "[redacted]")
    if not cleaned.lower().startswith("study suggestion"):
        cleaned = f"Study suggestion: {cleaned}"
    return cleaned[:1500]


async def _call(api_key: str, model: str, prompt: str) -> str | None:
    url = _ENDPOINT.format(model=model)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
                },
            )
    except httpx.HTTPError:
        logger.warning("Gemini call failed before a response for model %s", model)
        return None
    if response.status_code != 200:
        logger.warning("Gemini call failed: model=%s status=%s", model, response.status_code)
        return None
    try:
        data = response.json()
        return str(data["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("Gemini response had no text for model %s", model)
        return None
