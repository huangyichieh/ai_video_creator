"""Cache automatic character prompt shortening without editing character canon."""
import re

from .files import atomic_write, input_hash, read_text
from .models import Model, Nonempty


class PreparedCharacterPrompt(Model):
    source_hash: str
    source: Nonempty
    prompt: Nonempty


def cache_path(engine, prompt):
    from .generation_preferences import openai_config
    model = openai_config(engine.creative, "provider_prompt", force=True).model
    digest = input_hash({"source": prompt, "model": model, "version": "character-visual-v1"})
    return engine.path / "prompts" / "compiled_character_visuals" / (digest + ".json")


def cached_prompt(engine, prompt):
    path = cache_path(engine, prompt)
    if not path.exists():
        return None
    try:
        saved = PreparedCharacterPrompt.model_validate_json(read_text(path))
        if saved.source_hash == input_hash(prompt) and saved.source == prompt:
            validate_shortened(prompt, saved.prompt)
            return saved.prompt
    except (OSError, ValueError):
        pass
    return None


def validate_shortened(source, prompt):
    if not prompt.strip() or len(prompt.encode("utf-16-le")) // 2 > 999:
        raise ValueError("Automatic shortening could not fit the 999-character Runway budget. Shorten the prompt or Style rules manually.")
    for tag in ("@design", "@style"):
        if tag in source and tag not in prompt:
            raise ValueError("Shortening lost a required reference tag. Retry shortening or edit the prompt manually.")
    if "@style" in source and not re.search(r"style[ -]+only", prompt, re.I):
        raise ValueError("Shortening lost the style-only reference instruction. Retry or shorten the prompt manually.")
    return prompt


def shorten_character_prompt(engine, subject, prompt):
    saved = cached_prompt(engine, prompt)
    if saved is not None:
        return saved
    if engine.demo:
        raise ValueError("The character prompt exceeds 999 characters. Automatic OpenAI shortening is available in Production mode; shorten it manually for offline Demo.")
    from .provider_prompts import compile_prompt
    def validate(output):
        validate_shortened(prompt, output.text)
        return output
    shortened = compile_prompt(engine.creative, prompt, task_key=f"assets:character_references:{subject}:prompt",
                               character_visual=True, validate_output=validate)
    validate_shortened(prompt, shortened)
    saved = PreparedCharacterPrompt(source_hash=input_hash(prompt), source=prompt, prompt=shortened)
    atomic_write(cache_path(engine, prompt), saved.model_dump_json(by_alias=True, indent=2))
    return shortened
