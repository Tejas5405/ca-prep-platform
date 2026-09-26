"""The prompt builder must not call a model, and must not invent a grounding."""

from app.services.gemini import build_prompt


def test_an_empty_excerpt_list_does_not_build_a_prompt() -> None:
    assert build_prompt("What is the rate?", []) is None
    assert build_prompt("What is the rate?", ["  ", ""]) is None


def test_a_prompt_uses_the_excerpt_and_forbids_invention() -> None:
    prompt = build_prompt("lease", ["The paragraph mentions a lease."])
    assert prompt is not None
    assert "The paragraph mentions a lease." in prompt
    assert "Do not invent" in prompt
    assert "not a legal authority" in prompt.lower()
