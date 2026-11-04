from io import BytesIO
import json
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary, locations
from story_channel.costs import CostTracker
from story_channel.media_providers import DemoImageProvider
from story_channel.visual_generation import VisualGeneration, CHARACTER_INSTRUCTION, BACKGROUND_INSTRUCTION, DESIGN_MODEL
from test_studio import world
from test_ui import finish_background


def image_bytes():
    data = BytesIO()
    Image.new("RGB", (640, 640), "blue").save(data, "PNG")
    return data.getvalue()


def setup(world, stage):
    if stage == "character_references":
        world.generate_characters()
        subject = world.pipeline.artifact("characters").characters[0].character_id
    else:
        subject = locations(world.pipeline)[0].location_id
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    return engine, VisualGeneration(engine), subject


@pytest.mark.parametrize("stage", ["character_references", "backgrounds"])
def test_prompt_survives_blank_widget_mode_switches_and_stale_fragment_snapshot(world, stage):
    engine, inputs, subject = setup(world, stage)
    inputs.update(stage, subject, prompt="Original saved prompt")
    snapshot = engine.load()
    inputs.update(stage, subject, prompt="Recovered prompt from disk")
    app = AppTest.from_string('''
import streamlit as st
from story_channel.visual_generation_ui import generation_section
def toggle():
    st.session_state["hidden"] = not st.session_state.get("hidden", False)
st.button("Toggle page", key="toggle", on_click=toggle)
if not st.session_state.get("hidden"):
    generation_section(st.session_state["engine"], st.session_state["snapshot"],
                       st.session_state["stage"], st.session_state["subject"])
''')
    for key, value in {"engine": engine, "snapshot": snapshot, "stage": stage, "subject": subject}.items():
        app.session_state[key] = value
    app.run()
    prefix = f"visual-generation-{engine.project_id}-{stage}-{subject}"
    prompt_key = prefix + "-prompt"
    assert app.text_area(key=prompt_key).value == "Recovered prompt from disk"
    app.text_area(key=prompt_key).set_value("").run()
    assert inputs.get(stage, subject, engine.load())["prompt"] == "Recovered prompt from disk"
    assert app.text_area(key=prompt_key).value == ""
    app.text_area(key=prompt_key).set_value("New complete prompt").run()
    assert inputs.get(stage, subject, engine.load())["prompt"] == "New complete prompt"
    app.button(key=prefix + "-image").click().run()
    assert app.text_area(key=prompt_key).label == "Design description"
    assert app.text_area(key=prompt_key).value == "New complete prompt"
    app.button(key=prefix + "-text").click().run()
    app.button(key="toggle").click().run()
    app.button(key="toggle").click().run()
    assert not app.exception
    assert app.text_area(key=prompt_key).value == "New complete prompt"
    app.text_area(key=prompt_key).set_value("").run()
    inputs.update(stage, subject, prompt="Externally restored prompt")
    app.run()
    assert app.text_area(key=prompt_key).value == "Externally restored prompt"


def test_saved_prompt_cannot_be_erased_by_stale_blank_callback_and_has_history(world):
    engine, inputs, subject = setup(world, "character_references")
    inputs.update("character_references", subject, prompt="First custom character prompt")
    inputs.update("character_references", subject, prompt="Second custom character prompt")
    inputs.update("character_references", subject, prompt="", mode="image")
    saved = inputs.get("character_references", subject, engine.load())
    assert saved == {"mode": "image", "prompt": "Second custom character prompt"}
    archives = [json.loads(path.read_text(encoding="utf-8")) for path in (inputs.path / "history").glob("*.json")]
    assert {"First custom character prompt", "Second custom character prompt"} <= {item["prompt"] for item in archives}


def set_image(engine, stage, subject, data):
    if stage == "character_references":
        return engine.set_character_image(subject, data)
    return BackgroundLibrary(engine).set_image(subject, data)


@pytest.mark.parametrize("stage", ["character_references", "backgrounds"])
def test_text_sends_exact_editable_prompt_without_any_saved_reference(world, stage, monkeypatch):
    engine, inputs, subject = setup(world, stage)
    state = set_image(engine, stage, subject, image_bytes())
    reference = inputs.selected_image(stage, subject, state)
    inputs.update(stage, subject, mode="text", prompt="A precise editable design. 手繪風格。")
    reference.unlink()  # A broken unused reference must not block text mode.
    calls = []
    original = engine.image_provider.generate
    def capture(prompt, refs, config):
        calls.append((prompt, refs))
        return original(prompt, refs, config)
    engine.image_provider.generate = capture
    monkeypatch.setattr("story_channel.provider_prompts.compile_prompt", lambda *a, **k: pytest.fail("Do not rewrite the displayed prompt"))
    state = engine.generate(stage, subject=subject)
    from story_channel.visual_generation import BACKGROUND_EMPTY
    assert calls == [("A precise editable design. 手繪風格。" + ("\n" + BACKGROUND_EMPTY if stage == "backgrounds" else ""), [])]
    record = next(r for r in state.records.values() if r.stage == stage)
    assert record.asset.generation_params["prompt"] == calls[0][0]
    assert record.asset.generation_params["uploadedReferenceRole"] is None
    # Changing the unused image cannot stale a text-mode result.
    state = set_image(engine, stage, subject, image_bytes())
    assert state.records[record.stage + ":" + record.subject].status == "succeeded"


@pytest.mark.parametrize("stage", ["character_references", "backgrounds"])
def test_image_mode_uses_design_identity_not_style_only_or_hidden_character_text(world, stage):
    engine, inputs, subject = setup(world, stage)
    state = set_image(engine, stage, subject, image_bytes())
    path = inputs.selected_image(stage, subject, state)
    inputs.update(stage, subject, mode="image", prompt="THIS TEXT MUST NOT REACH RUNWAY")
    calls = []
    original = engine.image_provider.generate
    def capture(prompt, refs, config):
        calls.append((prompt, refs))
        return original(prompt, refs, config)
    engine.image_provider.generate = capture
    state = engine.generate(stage, subject=subject)
    from story_channel.visual_generation import BACKGROUND_EMPTY
    assert calls == [(CHARACTER_INSTRUCTION if stage == "character_references" else BACKGROUND_INSTRUCTION + "\n" + BACKGROUND_EMPTY, [path])]
    assert "STYLE GUIDE ONLY" not in calls[0][0] and "THIS TEXT" not in calls[0][0]
    record = next(r for r in state.records.values() if r.stage == stage)
    assert record.asset.generation_params["uploadedReferenceRole"] == "design_reference"
    if stage == "backgrounds":
        assert record.subject == subject + "__panorama" and len(state.records) == 1


@pytest.mark.parametrize("stage", ["character_references", "backgrounds"])
def test_modes_validate_before_any_media_call_and_persist_across_instances(world, stage):
    engine, inputs, subject = setup(world, stage)
    inputs.update(stage, subject, mode="text", prompt="x" * 1001)
    engine.image_provider.generate = lambda *args: pytest.fail("Invalid inputs must never call the provider")
    with pytest.raises(Exception, match="999"):
        engine.generate(stage, subject=subject)
    inputs.update(stage, subject, mode="image")
    with pytest.raises(Exception, match="design reference"):
        engine.generate(stage, subject=subject)
    fresh = AssetPipeline(world.manager, world.project_id, demo=True)
    assert VisualGeneration(fresh).get(stage, subject, fresh.load())["mode"] == "image"


@pytest.mark.parametrize("stage", ["character_references", "backgrounds"])
def test_openai_reference_action_records_cost_selects_image_and_does_not_call_runway(world, stage):
    engine, inputs, subject = setup(world, stage)
    inputs.update(stage, subject, mode="image", prompt="A blue design")
    engine.demo = False
    calls = []
    class FakeOpenAI(DemoImageProvider):
        def generate(self, prompt, refs, config):
            calls.append((prompt, refs, config))
            self.last_usage = {"image_count": 1, "input_tokens": 12, "output_tokens": 34}
            return super().generate(prompt, refs, config)
    before = len(CostTracker(engine.manager, engine.project_id).records())
    inputs.design_reference(stage, subject, "Watercolor, warm lighting", provider=FakeOpenAI())
    assert len(calls) == 1 and calls[0][1] == [] and calls[0][2].model == DESIGN_MODEL
    assert "Watercolor, warm lighting" in calls[0][0]
    assert inputs.selected_image(stage, subject, engine.load()).is_file()
    assert not engine.load().records  # Reference generation does not create a Runway output.
    records = CostTracker(engine.manager, engine.project_id).records()
    assert len(records) == before + 1
    assert records[-1].provider == "openai" and records[-1].image_count == 1
    assert records[-1].outcome == "succeeded"


def test_character_mode_ui_and_character_detail_name(world, monkeypatch):
    engine, inputs, subject = setup(world, "character_references")
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    prefix = f"visual-generation-{engine.project_id}-character_references-{subject}"
    assert any(e.label == "Character detail" for e in app.expander)
    assert not app.get("file_uploader") or all(f.proto.label != "Design reference image" for f in app.get("file_uploader"))
    app.text_area(key=prefix + "-prompt").set_value("A clean blue character sheet").run()
    app.button(key="retry-asset-character_references:" + subject).click().run()
    finish_background(app)
    assert not app.exception
    app.button(key=prefix + "-image").click().run()
    assert not app.exception
    assert any(f.proto.label == "Design reference image" for f in app.get("file_uploader"))
    assert app.button(key="retry-asset-character_references:" + subject).disabled
    app.button(key=prefix + "-design").click().run()
    finish_background(app)
    assert not app.exception
    assert not app.button(key="retry-asset-character_references:" + subject).disabled


def test_background_modify_targets_the_panorama_not_a_removed_directional_view(world, monkeypatch):
    engine, inputs, subject = setup(world, "backgrounds")
    inputs.update("backgrounds", subject, mode="text", prompt="One full-circle office panorama")
    state = engine.generate("backgrounds", subject=subject)
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    prefix = f"visual-generation-{engine.project_id}-backgrounds-{subject}"
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    modify_key = "modify-asset-backgrounds:" + subject + "__panorama"
    assert not app.button(key=modify_key).disabled
    app.button(key=modify_key).click().run()
    assert not app.exception
    request = app.session_state["modify-target"]
    assert request["subject"] == subject + "__panorama"


def test_failed_design_reference_records_failure_and_retains_previous_reference(world):
    from story_channel.llm import ProviderError
    engine, inputs, subject = setup(world, "character_references")
    set_image(engine, "character_references", subject, image_bytes())
    before = engine.load().character_images[subject]
    inputs.update("character_references", subject, mode="image", prompt="A clean character design")
    engine.demo = False
    class Failure:
        last_usage = {}
        def generate(self, *args):
            raise ProviderError("Mock image failure")
    with pytest.raises(ProviderError, match="Mock image failure"):
        inputs.design_reference("character_references", subject, provider=Failure())
    assert engine.load().character_images[subject] == before
    usage = CostTracker(engine.manager, engine.project_id).records()[-1]
    assert usage.model == DESIGN_MODEL and usage.outcome == "failed"
