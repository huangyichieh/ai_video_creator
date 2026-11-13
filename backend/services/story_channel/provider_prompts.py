"""Cost-tracked prompt compilation for provider input limits."""
from pydantic import Field, create_model
from .models import Model
from .modification import modify_content
from .runway_prompt_limits import MAX_PROMPT_UNITS, prompt_units


class CompactImagePrompt(Model):
    text: str = Field(min_length=2, max_length=700)


class CompactVideoPrompt(Model):
    text: str = Field(min_length=2, max_length=MAX_PROMPT_UNITS)


def compile_prompt(pipeline, text, *, video=False, task_key=None, character_visual=False, background=False, validate_output=None, max_units=None):
    # Images reserve room for the provider's contact-sheet reference instruction.
    limit = MAX_PROMPT_UNITS if video else 850
    if max_units is not None:
        limit = min(limit, max_units)
    if prompt_units(text) <= limit:
        return text
    schema = CompactVideoPrompt if video else CompactImagePrompt
    if max_units is not None:
        schema = create_model("CompactBudgetPrompt", __base__=schema,
                              text=(str, Field(min_length=2, max_length=limit)))
    # Keep full source as the instruction context instead of truncating identity
    # and performance directives. Only the provider-facing text is condensed.
    current = schema(text="Create a concise production prompt from the source supplied in the editing instruction.")
    character_guidance = (
        "For character visuals, preserve age, face, hair, build, clothing, colors and visible immutable traits. "
        "Reduce personality to brief observable expression, posture or gaze cues only; omit beliefs, motivations, relationships and backstory. "
        "Keep a neutral model-sheet expression and pose unless explicitly requested otherwise. "
        "Remove repetition and verbose wording before removing essential appearance details. "
    ) if character_visual else ""
    def validate(output):
        if prompt_units(output.text) > limit:
            raise ValueError("Compiled prompt still exceeds the provider limit; shorten the source prompt")
        return validate_output(output) if validate_output is not None else output

    result = modify_content(pipeline, current,
        "Compile this source into concise provider-facing text. Preserve character/location IDs, identity reference ordering, "
        + "action, interactions, performance intent, camera, exact angular coverage and projection when present, and visual style. Never invent full-360 coverage for a panorama sector. Preserve floor-plan anchors and reference-image roles. "
        + "Preserve requested delivery formats and portrait-safe center framing for important characters, faces, dialogue actions and story-critical props throughout motion. "
        +
        "Preserve @design and @style tags and their separate roles. Preserve the distinction between uploaded STYLE-ONLY guides and approved generated identity/layout anchors; never turn a style-only guide into an identity or layout reference. Do not add new content. "
        "When @style is present, retain the literal words STYLE ONLY and never copy the style guide's subject or identity. "
        + character_guidance + ("Preserve the saved floor plan, fixed center, orientation, entrances and named object positions. The environment must be empty: no people or characters, including reflections. " if background else "")
        + f"Keep the result within {limit} UTF-16 code units and the schema length limit. Source:\n" + text, "provider_prompt", task_key=task_key,
        validate_output=validate)
    if prompt_units(result.text) > limit:
        raise ValueError("Compiled prompt still exceeds the provider limit; shorten the source prompt")
    return result.text
