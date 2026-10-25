"""Cost-tracked prompt compilation for provider input limits."""
from pydantic import Field
from .models import Model
from .modification import modify_content


class CompactImagePrompt(Model):
    text: str = Field(min_length=2, max_length=700)


class CompactVideoPrompt(Model):
    text: str = Field(min_length=2, max_length=2000)


def compile_prompt(pipeline, text, *, video=False):
    limit = 2500 if video else 850
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    schema = CompactVideoPrompt if video else CompactImagePrompt
    # Keep full source as the instruction context instead of truncating identity
    # and performance directives. Only the provider-facing text is condensed.
    current = schema(text="Create a concise production prompt from the source supplied in the editing instruction.")
    result = modify_content(pipeline, current,
        "Compile this source into concise provider-facing text. Preserve character/location IDs, identity reference ordering, "
        "action, interactions, performance intent, camera and shared style. Do not add new content. "
        "Keep the result within the schema length limit. Source:\n" + text, "provider_prompt")
    if len(result.text.encode("utf-16-le")) // 2 > limit:
        raise ValueError("Compiled prompt still exceeds the provider limit; shorten the source prompt")
    return result.text
