import pytest

from story_channel.assets import AssetPipeline
from story_channel.costs import CostTracker
from story_channel.media_providers import DemoImageProvider
from story_channel.visual_generation import VisualGeneration
from story_channel.style_rules import StyleRules
from story_channel.character_visual_compaction import cached_prompt, cache_path
from test_studio import world
from test_character_visual_costumes import image_bytes


def setup(world, monkeypatch, *, style=False, unsafe=False):
    world.generate_characters()
    cid = world.pipeline.artifact("characters").characters[0].character_id
    captures = {"shortening": [], "images": []}
    class Shortener:
        last_usage = {}
        def __init__(self, **kwargs):
            pass
        def generate(self, prompt, model, config):
            captures["shortening"].append(prompt)
            self.last_usage = {"input_tokens": 900, "output_tokens": 60}
            result = "37-year-old man, short black hair, blue eyes, green jacket. Reserved posture, gentle expression. Neutral background, full-body front, side, back, three-quarter model sheet. No text."
            if style and not unsafe:
                result += " Match @style for STYLE ONLY: rendering and palette, not subject or identity."
            return model(text=result)
    monkeypatch.setattr("story_channel.modification.OpenAILLMProvider", Shortener)
    class Image(DemoImageProvider):
        def generate(self, prompt, refs, config):
            captures["images"].append((prompt, refs))
            result = super().generate(prompt, refs, config)
            return result.model_copy(update={"provider": "runway", "model": "gen4_image"})
    engine = AssetPipeline(world.manager, world.project_id, demo=False, image_provider=Image())
    if style:
        StyleRules(engine).set_image(image_bytes())
    return engine, cid, captures


def test_overlong_character_prompt_is_shortened_before_image_request_preserving_details_and_cost(world, monkeypatch):
    engine, cid, captures = setup(world, monkeypatch)
    inputs = VisualGeneration(engine)
    original = "A 37-year-old man with blue eyes, short black hair, green jacket. Personality: reserved but kind; " + "loyal to family and committed to his work. " * 60
    inputs.update("character_references", cid, mode="text", prompt=original)
    canon = (engine.path / "characters.json").read_bytes()
    state = engine.generate("character_references", subject=cid)
    assert len(captures["shortening"]) == 1 and len(captures["images"]) == 1
    text = captures["images"][0][0]
    assert len(text.encode("utf-16-le")) // 2 < 1000
    assert "green jacket" in text and "Reserved posture" in text and "loyal to family" not in text
    assert "observable expression" in captures["shortening"][0] and "backstory" in captures["shortening"][0]
    assert (engine.path / "characters.json").read_bytes() == canon
    assert state.generation_inputs["character_references:" + cid]["prompt"] == original
    assert state.records["character_references:" + cid].asset.generation_params["prompt"] == text
    assert cached_prompt(engine, original) == text
    usage = [u for u in CostTracker(engine.manager, engine.project_id).records() if u.step == "modify:provider_prompt"]
    assert len(usage) == 1 and usage[0].input_tokens == 900
    engine.generate("character_references", subject=cid, force=True)
    assert len(captures["shortening"]) == 1 and len(captures["images"]) == 2


def test_style_reference_roles_survive_shortening_and_source_edits_invalidate_cache(world, monkeypatch):
    engine, cid, captures = setup(world, monkeypatch, style=True)
    inputs = VisualGeneration(engine)
    inputs.update("character_references", cid, mode="text", prompt="A character. " * 200)
    state = engine.generate("character_references", subject=cid)
    text, refs = captures["images"][0]
    assert "@style" in text and "STYLE ONLY" in text
    assert refs == [StyleRules(engine).image(state)]
    inputs.update("character_references", cid, prompt="A different character. " * 150)
    engine.generate("character_references", subject=cid)
    assert len(captures["shortening"]) == 2


def test_short_prompt_does_not_call_shortener(world, monkeypatch):
    engine, cid, captures = setup(world, monkeypatch)
    VisualGeneration(engine).update("character_references", cid, mode="text", prompt="A clean character model sheet")
    engine.generate("character_references", subject=cid)
    assert captures["shortening"] == []
    assert captures["images"][0][0] == "A clean character model sheet"


def test_invalid_shortening_does_not_call_image_provider(world, monkeypatch):
    engine, cid, captures = setup(world, monkeypatch, style=True, unsafe=True)
    VisualGeneration(engine).update("character_references", cid, mode="text", prompt="Long personality description. " * 100)
    with pytest.raises(Exception, match="reference tag"):
        engine.generate("character_references", subject=cid)
    assert len(captures["shortening"]) == 1 and captures["images"] == []
    assert [u for u in CostTracker(engine.manager, engine.project_id).records() if u.step == "modify:provider_prompt"][-1].outcome == "failed"


def test_oversized_prompt_ui_enables_production_generate_and_shows_cached_request(world, monkeypatch):
    from streamlit.testing.v1 import AppTest
    engine, cid, captures = setup(world, monkeypatch)
    VisualGeneration(engine).update("character_references", cid, mode="text", prompt="Long character description. " * 100)
    app = AppTest.from_string('''
import streamlit as st
from story_channel.visual_generation_ui import generation_section
engine = st.session_state["engine"]
generation_section(engine, engine.load(), "character_references", st.session_state["cid"])
''')
    app.session_state["engine"] = engine
    app.session_state["cid"] = cid
    app.run()
    assert not app.exception
    assert not app.button(key="retry-asset-character_references:" + cid).disabled
    app.button(key="retry-asset-character_references:" + cid).click().run()
    app.session_state["workspace-task"]["future"].result(timeout=20)
    app.run()
    assert not app.exception
    assert any("Automatically shortened Runway prompt" in item.value for item in app.caption)
