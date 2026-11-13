"""Explicit bulk text-mode model-sheet generation."""
from .visual_generation import VisualGeneration


def generate_all_visuals_text(engine, prompts=None):
    inputs = VisualGeneration(engine)
    characters = engine.creative.artifact("characters").characters
    if not characters:
        raise ValueError("Create a character first.")
    state = engine.load()
    for character in characters:
        cid = character.character_id
        prompt = (prompts or {}).get(cid) or inputs.get("character_references", cid, state)["prompt"]
        inputs.update("character_references", cid, mode="text", prompt=prompt)
    return engine.generate("character_references", force=True)
