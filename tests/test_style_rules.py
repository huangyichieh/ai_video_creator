from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import locations
from story_channel.costs import CostTracker
from story_channel.style_rules import StyleRules, apply_rules
from story_channel.style_check import candidates, check_style, signature, StyleReport, StyleIssue, REBUILD_MESSAGE
from story_channel.visual_generation import VisualGeneration, CHARACTER_INSTRUCTION
from story_channel.library_media import LibraryMedia
from test_studio import world
from test_visual_generation import image_bytes
from test_ui import finish_background


def ready(world, count=1):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    for index in range(count - 1):
        world.generate_characters("Character " + str(index))
    engine.generate("character_references")
    from story_channel.background_library import BackgroundLibrary
    from story_channel.floor_plan_guides import publish_guides
    for location in locations(engine.creative):
        BackgroundLibrary(engine).configure_references(location.location_id,floor_plan="Fixed camera center",object_layout={"desk":"north wall"})
        publish_guides(engine,location.location_id,engine.load().location_reference_sets[location.location_id])
        engine.generate("backgrounds",subject=location.location_id)
    return engine


def test_rules_can_be_saved_before_initial_characters_exist(world):
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    state = StyleRules(engine).save("Clean ink outlines")
    assert state.project_style_rules == "Clean ink outlines"
    assert not (engine.path / "characters.json").exists()


def test_audio_rules_do_not_turn_visual_style_or_voice_direction_into_sfx(world):
    from story_channel.voice_design import voice_design_context
    from story_channel.sound_prompts import sound_prompt, sound_payload
    from story_channel.render_models import SoundCue
    from story_channel.style_rules import audio_rules
    engine = ready(world)
    text = "Thin outlines and cel shading\nVoice: calm delivery\nSFX: dry natural Foley"
    StyleRules(engine).save(text)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    context = voice_design_context(engine.creative, cid)
    assert context["projectVoiceStyle"] == "Voice: calm delivery"
    prompt = sound_prompt(engine.creative, sound_payload(SoundCue(name="A door closes", duration=2), "cues"))
    assert "dry natural Foley" in prompt and "no intelligible speech" in prompt
    assert "Thin outlines" not in prompt and "calm delivery" not in prompt


def test_rules_persist_and_reach_text_image_and_scene_generation_configs(world):
    engine = ready(world)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    inputs = VisualGeneration(engine)
    state = StyleRules(engine).save("Soft cel shading; thin outlines")
    assert state.project_style_rules == "Soft cel shading; thin outlines"
    assert state.records["character_references:" + cid].status == "stale"
    inputs.update("character_references", cid, mode="text", prompt="A character model sheet")
    state = engine.load()
    prompt, refs = inputs.request("character_references", cid, state)
    assert prompt == "A character model sheet\nProject style rules:\nSoft cel shading; thin outlines" and not refs
    state = engine.set_character_image(cid, image_bytes())
    inputs.update("character_references", cid, mode="image")
    state = engine.load()
    prompt, refs = inputs.request("character_references", cid, state)
    assert prompt == apply_rules(CHARACTER_INSTRUCTION, state.project_style_rules) and len(refs) == 1
    task = next(t for t in engine.tasks(state) if t.subject == cid)
    assert engine._config(task, state).options["project_style_rules"] == state.project_style_rules
    assert AssetPipeline(engine.manager, engine.project_id).load().project_style_rules == state.project_style_rules


def test_rules_count_towards_runway_limit(world):
    engine = ready(world)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    inputs = VisualGeneration(engine)
    inputs.update("character_references", cid, mode="text", prompt="x" * 990)
    StyleRules(engine).save("watercolor")
    with pytest.raises(ValueError, match="including shared Style rules"):
        inputs.request("character_references", cid, engine.load())


def test_openai_character_reference_is_small_front_view_with_rules(world):
    from story_channel.media_providers import DemoImageProvider
    engine = ready(world)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    StyleRules(engine).save("Thin outlines, soft cel shading")
    calls = []
    class Capture(DemoImageProvider):
        def generate(self, prompt, refs, config):
            calls.append((prompt, config.options))
            return super().generate(prompt, refs, config)
    VisualGeneration(engine).design_reference("character_references", cid, "Red jacket", provider=Capture())
    prompt, options = calls[0]
    assert "ONE front-view character only" in prompt and "no model sheet" in prompt
    assert "Red jacket" in prompt and "Thin outlines, soft cel shading" in prompt
    assert options["size"] == "1024x1024" and options["quality"] == "low"
    path = engine.character_image(engine.load(), cid)
    with Image.open(path) as image:
        assert max(image.size) <= 512


def test_rules_reach_props_and_costume_signature(world):
    engine = ready(world)
    StyleRules(engine).save("Watercolor with restrained outlines")
    media = LibraryMedia(engine.manager, engine.project_id)
    media.assets = engine
    calls = []
    original = engine.image_provider.generate
    def capture(prompt, refs, config):
        calls.append(prompt)
        return original(prompt, refs, config)
    engine.image_provider.generate = capture
    media.image("prop:test", "A brass key")
    assert "Watercolor with restrained outlines" in calls[0]


class Reviewer:
    last_usage = {}
    def __init__(self, issues=()):
        self.calls = []
        self.issues = issues
    def generate(self, prompt, model, config):
        self.last_usage = {"input_tokens": 100, "output_tokens": 50}
        self.calls.append(prompt)
        return StyleReport(summary="Visual review completed", issues=list(self.issues))


def test_style_check_uses_real_images_saves_report_records_cost_and_revisions_stale_report(world):
    engine = ready(world)
    entries = candidates(engine, engine.load())
    issue = StyleIssue(asset_key=entries[0]["key"], reason="Painterly texture conflicts with flat cel backgrounds", recommendation="Use matching cel shading")
    provider = Reviewer([issue])
    before = len(CostTracker(engine.manager, engine.project_id).records())
    report = check_style(engine, provider=provider)
    assert len(report["issues"]) == 1 and len(report["assets"]) == 2
    assert report["signature"] == signature(engine, engine.load())
    content = provider.calls[0][0]["content"]
    assert sum(item["type"] == "input_image" for item in content) == 3
    assert all(item["image_url"].startswith("data:image/jpeg;base64,") for item in content if item["type"] == "input_image")
    costs = CostTracker(engine.manager, engine.project_id).records()
    assert len(costs) == before + 1 and costs[-1].step == "style_check" and costs[-1].input_tokens == 100
    cid = world.pipeline.artifact("characters").characters[0].character_id
    engine.generate("character_references", subject=cid, force=True)
    assert report["signature"] != signature(engine, engine.load())


def test_unknown_style_issue_records_failure_and_does_not_save_success(world):
    engine = ready(world)
    provider = Reviewer([StyleIssue(asset_key="unknown", reason="bad", recommendation="fix")])
    with pytest.raises(Exception, match="unknown asset"):
        check_style(engine, provider=provider)
    assert not (engine.path / "style_check.json").exists()
    assert CostTracker(engine.manager, engine.project_id).records()[-1].outcome == "failed"


def test_dedicated_style_check_tab_and_more_than_five_hides_list(world, monkeypatch):
    engine = ready(world, count=6)
    entries = candidates(engine, engine.load())
    provider = Reviewer([StyleIssue(asset_key=e["key"], reason="SHOULD_NOT_DISPLAY", recommendation="Match outlines") for e in entries])
    check_style(engine, provider=provider)
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Style check"
    app.run()
    assert not app.exception
    expected_tabs = ["Character References", "Background", "SFX", "Props", "Style check"]
    assert [tab.label for tab in app.tabs if tab.label in expected_tabs] == expected_tabs
    assert len([button for button in app.button if button.label == "Style check"]) == 1
    assert any(heading.value == "Style rules" for heading in app.subheader)
    assert len([warning for warning in app.warning if warning.value == REBUILD_MESSAGE]) == 1
    assert not any("SHOULD_NOT_DISPLAY" in element.value for element in app.markdown)


def test_small_mismatch_list_names_assets_and_stale_rules_hide_old_findings(world):
    from story_channel.style_rules_ui import style_check_controls
    engine = ready(world)
    entry = candidates(engine, engine.load())[0]
    check_style(engine, provider=Reviewer([StyleIssue(asset_key=entry["key"], reason="Ink differs from watercolor", recommendation="Match the brushwork")]))
    app = AppTest.from_string('''
import streamlit as st
from story_channel.assets import AssetPipeline
from story_channel.style_rules_ui import style_check_controls
engine = AssetPipeline(st.session_state["manager"], st.session_state["pid"], demo=True)
style_check_controls(engine, "characters")
''')
    app.session_state["manager"] = engine.manager
    app.session_state["pid"] = engine.project_id
    app.run()
    assert not app.exception
    assert any(entry["name"] in text.value for text in app.markdown)
    assert any("Ink differs from watercolor" == text.value for text in app.markdown)
    StyleRules(engine).save("New cel shading")
    app.run()
    assert any("awaiting style review" in caption.value for caption in app.caption)
    assert not any("Ink differs from watercolor" == text.value for text in app.markdown)
