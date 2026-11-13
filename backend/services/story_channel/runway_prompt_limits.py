"""Shared budget for every text prompt submitted to Runway."""

from .llm import ProviderError

MAX_PROMPT_UNITS = 999


def prompt_units(text):
    # Count supplementary Unicode characters conservatively as two units.
    return len(text.encode("utf-16-le")) // 2


def require_prompt_budget(text):
    if prompt_units(text) > MAX_PROMPT_UNITS:
        raise ProviderError("Runway prompts must be shorter than 1000 characters (maximum 999 UTF-16 units); shorten the prompt before submitting")
    return text
