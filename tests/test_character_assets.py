import json
import io
from pathlib import Path
from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.pipeline import Pipeline
from story_channel.projects import ProjectManager
from story_channel.proofreading import TypoReport, check_typos
from story_channel.media_providers import DemoImageProvider


@pytest.fixture
def cast(tmp_path, monkeypatch):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    outline = tmp_path / "outline.md"
    outline.write_text("A keeper and a sailor restore memories.", encoding="utf-8")
    project = manager.create("Main characters", outline, target_duration_sec=30)
    flow = Pipeline(manager, project.project_id)
    flow.generate("world")
    flow.generate("characters")
    characters = flow.artifact("characters")
    second = characters.characters[0].model_copy(deep=True, update={"character_id": "sailor", "name": "Kai", "voice_id": "sailor-voice"})
    characters.characters.append(second)
    flow.edit("characters", characters.model_dump_json(by_alias=True))
    AssetPipeline(manager, project.project_id).set_generic_prompt("Cinematic adventure")
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    return manager, project, flow


def app_for_cast():
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    return app


def finish(app):
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=20)
        app.run()
    assert not app.exception


def test_cards_individual_generate_then_batch_generates_entire_cast(cast):
    manager, project, _ = cast
    app = app_for_cast()
    for cid in ("keeper", "sailor"):
        assert any(f"{cid} · pending · revision 0" == item.value for item in app.caption)
        assert app.button(key=f"retry-asset-character_references:{cid}").label == "Generate"
    style_key = f"visual-generation-{project.project_id}-character_references-keeper-prompt"
    app.text_area(key=style_key).set_value("Watercolor, warm palette").run()
    app.button(key="retry-asset-character_references:sailor").click().run()
    finish(app)
    engine = AssetPipeline(manager, project.project_id)
    first = engine.inspect()
    assert set(first.records) == {"character_references:sailor"}
    image = first.records["character_references:sailor"].asset.asset_id
    assert app.button(key="retry-asset-character_references:sailor").label == "Generate"
    app.button(key="generate-next-asset-stage").click().run()
    finish(app)
    state = engine.inspect()
    assert state.next_stage == "character_review"
    assert set(state.records) == {"character_references:keeper", "character_references:sailor"}
    assert all(r.status == "succeeded" for r in state.records.values())
    assert state.records["character_references:keeper"].asset.generation_params["stylePrompt"] == ""
    assert "Watercolor, warm palette" in state.records["character_references:keeper"].asset.generation_params["prompt"]
    assert state.records["character_references:sailor"].asset.asset_id == image
    assert len(CostTracker(manager, project.project_id).records()) == 4  # world, cast, two images


def test_character_draft_typo_apply_revert_save_and_navigation(cast, monkeypatch):
    manager, project, flow = cast
    original = flow.artifact("characters")
    captured = []

    class Proofreader:
        last_usage = {"input_tokens": 10, "output_tokens": 5}

        def generate(self, prompt, response_model, config):
            return TypoReport(summary="One typo", suggestions=[{
                "source": "characters", "field": "appearance", "original": "Shrot", "replacement": "Short", "explanation": "Spelling"}])

    def proofread(pipeline, drafts, stage, **kwargs):
        captured.append((drafts, stage))
        return check_typos(pipeline, drafts, stage, provider=Proofreader(), **kwargs)

    monkeypatch.setattr("story_channel.character_assets_ui.check_typos", proofread)
    app = app_for_cast()
    prefix = f"asset-character-{project.project_id}-sailor"
    app.toggle(key=prefix + "-edit-mode").set_value(True).run()
    draft = json.loads(app.session_state[prefix + "-draft"])
    draft["appearance"] = "Shrot hair and green eyes"
    text = json.dumps(draft, ensure_ascii=False, indent=2)
    app.text_area(key=prefix + "-field-appearance").set_value(draft["appearance"]).run()
    assert app.button(key="retry-asset-character_references:sailor").disabled
    app.button(key="nav-final").click().run()
    app.button(key="nav-assets").click().run()
    assert app.session_state[prefix + "-draft"] == text
    app.button(key=prefix + "-check").click().run()
    finish(app)
    assert json.loads(captured[0][0]["characters"])["characterId"] == "sailor"
    assert json.loads(captured[0][0]["characters"]) == draft
    assert captured[0][1] == "characters"
    assert any(r.step == "typo_check:characters" for r in CostTracker(manager, project.project_id).records())
    app.button(key=prefix + "-apply-0").click().run()
    assert app.text_area(key=prefix + "-field-appearance").value == "Short hair and green eyes"
    assert flow.artifact("characters") == original
    app.button(key=prefix + "-revert").click().run()
    assert app.text_area(key=prefix + "-field-appearance").value == original.characters[1].appearance
    assert app.button(key=prefix + "-save").disabled
    app.text_area(key=prefix + "-field-appearance").set_value(draft["appearance"]).run()
    app.button(key=prefix + "-save").click().run()
    finish(app)
    updated = flow.artifact("characters")
    assert updated.characters[0] == original.characters[0]
    assert updated.characters[1].appearance == "Shrot hair and green eyes"
    assert app.button(key=prefix + "-save").disabled
    assert not any("changed elsewhere" in w.value for w in app.warning)
    assert (flow.path / "character_bible.md").exists()
    assert list((flow.path / "history" / "characters").glob("*.json"))


def test_single_character_edit_validation_and_concurrent_change(cast):
    _, _, flow = cast
    character = flow.artifact("characters").characters[1]
    baseline = character.model_dump_json(by_alias=True)
    data = json.loads(baseline)
    data["characterId"] = "different"
    with pytest.raises(ValueError, match="characterId"):
        flow.edit_character("sailor", json.dumps(data), expected=baseline)
    data["characterId"] = "sailor"
    data["age"] = -1
    with pytest.raises(ValueError):
        flow.edit_character("sailor", json.dumps(data), expected=baseline)
    data["age"] = 40
    flow.edit_character("sailor", json.dumps(data), expected=baseline)
    with pytest.raises(ValueError, match="changed in another editor"):
        flow.edit_character("sailor", baseline, expected=baseline)
    assert flow.artifact("characters").characters[1].age == 40


def test_saved_appearance_edit_regenerates_only_changed_reference(cast):
    manager, project, flow = cast
    engine = AssetPipeline(manager, project.project_id)
    state = engine.generate("character_references")
    original = {key: record.asset.asset_id for key, record in state.records.items()}
    engine.approve("characters")
    character = flow.artifact("characters").characters[1]
    baseline = character.model_dump_json(by_alias=True)
    character.appearance = "Long silver hair and green eyes"
    flow.edit_character("sailor", character.model_dump_json(by_alias=True), expected=baseline)
    state = engine.inspect()
    assert state.records["character_references:sailor"].status == "stale"
    assert state.records["character_references:keeper"].status == "succeeded"
    assert "characters" not in manager.load(project.project_id).asset_approvals
    state = engine.generate("character_references")
    assert state.records["character_references:sailor"].revision == 2
    assert state.records["character_references:keeper"].asset.asset_id == original["character_references:keeper"]
    assert state.records["character_references:sailor"].asset.asset_id != original["character_references:sailor"]


def test_generic_prompt_optional_for_character_sheets_and_saved_for_batch_generation(cast):
    manager, project, _ = cast
    engine = AssetPipeline(manager, project.project_id)
    engine.set_generic_prompt("")
    assert engine.can_generate("character_references", engine.load())
    app = app_for_cast()
    assert not app.button(key="generate-next-asset-stage").disabled
    assert not app.button(key="retry-asset-character_references:keeper").disabled
    generic_key = f"asset-generic-prompt-{project.project_id}"
    app.text_area(key=generic_key).set_value("Hand-drawn fantasy, hopeful tone").run()
    assert not app.button(key="generate-next-asset-stage").disabled
    app.button(key="nav-final").click().run()
    app.button(key="nav-assets").click().run()
    assert app.text_area(key=generic_key).value == "Hand-drawn fantasy, hopeful tone"
    app.button(key="generate-next-asset-stage").click().run()
    finish(app)
    for record in engine.load().records.values():
        assert record.asset.generation_params["genericPrompt"] == "Hand-drawn fantasy, hopeful tone"
        assert "Hand-drawn fantasy" in record.asset.generation_params["prompt"]
    engine.set_generic_prompt("Clay animation")
    assert all(r.status == "stale" for r in engine.load().records.values())


def test_stop_finishes_current_asset_shows_loading_and_can_resume(cast, monkeypatch):
    manager, project, flow = cast
    characters = flow.artifact("characters")
    first = characters.characters[0]
    characters.characters.extend(first.model_copy(deep=True, update={"character_id": f"extra_{i}", "voice_id": f"extra_voice_{i}"}) for i in range(5))
    flow.edit("characters", characters.model_dump_json(by_alias=True))
    release = Event()
    original = DemoImageProvider.generate
    calls = []

    def delayed(self, *args, **kwargs):
        calls.append(args[0])
        assert release.wait(timeout=20)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DemoImageProvider, "generate", delayed)
    app = app_for_cast()
    task = None
    try:
        app.button(key="generate-next-asset-stage").click().run()
        task = app.session_state["workspace-task"]
        assert not task["future"].done()
        assert "Generating…" in app.dataframe[0].value["Status"].tolist()
        assert any("Running: 1/1 · Queued: 6" in c.value for c in app.caption)
        assert any(s.state == "running" for s in app.status)
        app.button(key="stop-workspace-task").click().run()
        assert task["stop_event"].is_set()
        assert app.button(key="stop-workspace-task").disabled
        assert not task["future"].done()
        app.button(key="nav-final").click().run()
        assert not app.exception
        release.set()
        task["future"].result(timeout=10)
        app.run()
        engine = AssetPipeline(manager, project.project_id)
        state = engine.load()
        assert len(calls) == 1
        assert state.stopped
        assert len(state.records) == 1
        assert next(iter(state.records.values())).status == "succeeded"
        usage = CostTracker(manager, project.project_id).records()
        assert len([r for r in usage if r.step == "assets:character_references"]) == 1
        assert any("Stopped after running tasks" in s.value for s in app.success)
        app.button(key="nav-assets").click().run()
        app.button(key="generate-next-asset-stage").click().run()
        finish(app)
        assert len(calls) == 7
        assert engine.load().next_stage == "character_review"
        assert not engine.load().stopped
    finally:
        release.set()
        if task:
            task["future"].result(timeout=10)


def test_shared_style_image_preview_persists_and_can_be_removed(cast):
    from PIL import Image
    manager, project, _ = cast
    engine = AssetPipeline(manager, project.project_id)
    data = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(data, "PNG")
    engine.set_style_reference(data.getvalue())
    engine.set_generic_prompt("")
    app = app_for_cast()
    assert app.get("file_uploader")
    assert app.get("image")
    assert not app.button(key="generate-next-asset-stage").disabled
    app.button(key="nav-final").click().run()
    app.button(key="nav-assets").click().run()
    assert not app.button(key="generate-next-asset-stage").disabled
    app.button(key="remove-style-reference").click().run()
    assert not app.exception
    assert engine.load().style_reference is None
    assert not app.button(key="generate-next-asset-stage").disabled


def test_three_shared_style_images_persist_and_remove_individually(cast):
    from PIL import Image
    manager, project, _ = cast
    engine = AssetPipeline(manager, project.project_id)
    images = []
    for color in ("red", "green", "blue"):
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), color).save(buffer, "PNG")
        images.append(buffer.getvalue())
    engine.set_style_references(images)
    saved = engine.load().style_references
    app = app_for_cast()
    assert app.get("file_uploader")[0].proto.multiple_files
    assert app.button(key="remove-style-reference-2")
    app.button(key="nav-story-settings").click().run()
    app.button(key="nav-assets").click().run()
    app.button(key="remove-style-reference-1").click().run()
    assert not app.exception
    assert engine.load().style_references == [saved[0], saved[2]]
    app.button(key="remove-style-reference").click().run()
    assert engine.load().style_references == []


def test_individual_generate_buttons_queue_reference_assets_one_at_a_time(cast, monkeypatch):
    manager, project, _ = cast
    release, two_started = Event(), Event()
    calls = []
    original = DemoImageProvider.generate

    def delayed(self, *args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            two_started.set()
        assert release.wait(timeout=20)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DemoImageProvider, "generate", delayed)
    app = app_for_cast()
    task = None
    try:
        app.button(key="retry-asset-character_references:keeper").click().run()
        task = app.session_state["workspace-task"]
        assert app.button(key="retry-asset-character_references:sailor").label == "Queue asset"
        app.button(key="retry-asset-character_references:sailor").click().run()
        assert len(calls) == 1
        app.run()
        assert not app.exception
        assert app.button(key="retry-asset-character_references:sailor").label == "Queuing…", {
            "activity": task["asset_engine"].activity_snapshot(), "captions": [c.value for c in app.caption],
            "errors": [e.value for e in app.error]}
        assert task["asset_engine"].load().generation_queued == 1
        release.set()
        task["future"].result(timeout=10)
        assert two_started.is_set()
        app.run()
        state = AssetPipeline(manager, project.project_id).load()
        assert len(calls) == 2
        assert state.generation_total == state.generation_finished == 2
        assert state.next_stage == "character_review"
    finally:
        release.set()
        if task:
            task["future"].result(timeout=10)
