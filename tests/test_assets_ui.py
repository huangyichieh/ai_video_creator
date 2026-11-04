from pathlib import Path
import json
import io

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.cli import main
from story_channel.config import Settings
from story_channel.pipeline import Pipeline
from story_channel.projects import ProjectManager


def widget(collection, label):
    return next(item for item in collection if item.label == label)


def prepare(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    outline = tmp_path / "outline.md"
    outline.write_text("A keeper restores lost memories.", encoding="utf-8")
    project = manager.create("Production UI", outline, target_duration_sec=30)
    flow = Pipeline(manager, project.project_id)
    flow.run()
    flow.approve("story")
    flow.run()
    flow.approve("storyboard")
    flow.run()
    AssetPipeline(manager, project.project_id).set_generic_prompt("Cinematic adventure")
    return manager, project


def test_asset_page_generate_approve_preview_and_finish(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    app.button(key="generate-next-asset-stage").click().run()
    assert not app.exception
    assert app.button(key="approve-assets-characters").disabled
    app.checkbox[0].check().run()
    app.button(key="approve-assets-characters").click().run()
    widget(app.button, "Generate assets until review").click().run()
    assert not app.exception
    app.checkbox[0].check().run()
    app.button(key="approve-assets-backgrounds").click().run()
    assert widget(app.button, "Generate assets until review").disabled
    assert {tab.label for tab in app.tabs} == {"Character References", "Locations / Backgrounds", "Costumes", "Props", "Voice Profiles", "Style References", "Music / SFX library", "Reusable generated images"}
    from story_channel.identity import IdentityManager
    from story_channel.animation import AnimationDirector
    from test_ui import finish_background
    identity = IdentityManager(manager, project.project_id)
    library = identity.load()
    changed = library.model_copy(deep=True)
    for voice in changed.voices.values():
        voice.voice_id = "nova"
    identity.save(changed, expected=library.model_dump_json(by_alias=True))
    identity.approve()
    director = AnimationDirector(manager, project.project_id)
    for cid in director.prepare().plan.voices:
        director.preview_voice(cid, "Keep the beacon burning. We will find our way through this storm together, one step at a time.")
        director.approve_voice(cid)
    app.button(key="nav-animations").click().run()
    app.button(key=f"animation-input-images-{project.project_id}").click().run()
    finish_background(app)
    app.checkbox(key=f"animation-plan-check-{project.project_id}").check().run()
    app.button(key=f"animation-approve-plan-{project.project_id}").click().run()
    finish_background(app)
    app.checkbox(key=f"animation-performance-check-{project.project_id}").check().run()
    app.button(key=f"animation-performance-approve-{project.project_id}").click().run()
    finish_background(app)
    app.button(key=f"animation-workflow-run-{project.project_id}").click().run()
    finish_background(app)
    assert not app.exception
    assert "Voices & input" in {tab.label for tab in app.tabs}
    assert app.json
    widget(app.checkbox, "I reviewed these assets and approve continuing").check().run()
    app.button(key="approve-assets-assets").click().run()
    finish_background(app)
    assert not app.exception
    assert not app.button(key=f"animation-workflow-next-{project.project_id}").disabled
    assert manager.load(project.project_id).asset_state == "approved"


def test_assets_cli_lifecycle(tmp_path, capsys):
    manager, project = prepare(tmp_path)
    common = ["--data-root", str(manager.root), "assets", project.project_id]
    for command in (["run", "--demo"], ["approve", "characters"], ["run"], ["approve", "backgrounds"],
                    ["run"], ["approve", "assets"], ["show"]):
        assert main(common + command) == 0
        capsys.readouterr()
    assert AssetPipeline(manager, project.project_id).inspect().next_stage == "complete"
    from story_channel.logging_config import configure_logging
    configure_logging()


def test_early_character_assets_ui(tmp_path, monkeypatch):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    outline = tmp_path / "outline.md"
    outline.write_text("A keeper restores memories.", encoding="utf-8")
    project = manager.create("Early assets UI", outline, target_duration_sec=30)
    flow = Pipeline(manager, project.project_id)
    flow.generate("world")
    flow.generate("characters")
    AssetPipeline(manager, project.project_id).set_generic_prompt("Cinematic adventure")
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    assert not app.button(key="generate-next-asset-stage").disabled
    app.button(key="generate-next-asset-stage").click().run()
    assert not app.exception
    app.checkbox[0].check().run()
    app.button(key="approve-assets-characters").click().run()
    assert not app.exception
    assert app.button(key="generate-next-asset-stage").disabled
    assert widget(app.button, "Generate assets until review").disabled
    assert AssetPipeline(manager, project.project_id).inspect().next_stage == "waiting_for_creative"


def test_all_asset_cards_have_sources_and_persistent_styles(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    engine = AssetPipeline(manager, project.project_id)
    tasks = engine.tasks(engine.load())
    for stage in ("backgrounds", "scene_images", "voices"):
        page = "nav-assets" if stage == "backgrounds" else "nav-animations"
        app.button(key=page).click().run()
        task = next(t for t in tasks if t.stage == stage)
        prefix = f"asset-editor-{stage}-{project.project_id}-{task.subject}"
        app.toggle(key=prefix + "-edit-json").set_value(True).run()
        assert app.text_area(key=prefix + "-draft")
        assert app.button(key=f"retry-asset-{task.key}").label == "Generate asset"
        assert app.button(key=f"retry-asset-{task.key}").disabled
        style_key = f"asset-style-{project.project_id}-{task.key}"
        app.text_input(key=style_key).set_value("Gentle, cinematic").run()
        assert not app.exception
        app.button(key="nav-final").click().run()
        app.button(key=page).click().run()
        assert app.text_input(key=style_key).value == "Gentle, cinematic"
        assert engine.load().style_prompts[task.key] == "Gentle, cinematic"
    app.button(key="nav-assets").click().run()
    prefix = f"asset-editor-backgrounds-{project.project_id}-lighthouse"
    source = json.loads(app.text_area(key=prefix + "-draft").value)
    original = source["description"]
    source["description"] = "An ivy-covered lighthouse above a calm bay"
    app.text_area(key=prefix + "-draft").set_value(json.dumps(source)).run()
    app.button(key=prefix + "-revert").click().run()
    assert json.loads(app.text_area(key=prefix + "-draft").value)["description"] == original
    app.text_area(key=prefix + "-draft").set_value(json.dumps(source)).run()
    app.button(key=prefix + "-save").click().run()
    assert not app.exception
    assert engine.creative.artifact("world").locations[0].description == source["description"]
    assert app.button(key=prefix + "-save").disabled


def test_cli_save_text_and_image_style_then_remove_image(tmp_path, capsys):
    from PIL import Image
    manager, project = prepare(tmp_path)
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(output, "PNG")
    reference = tmp_path / "style.png"
    reference.write_bytes(output.getvalue())
    common = ["--data-root", str(manager.root), "assets", project.project_id, "show"]
    assert main(common + ["--style-reference", str(reference), "--generic-prompt", "Watercolor fantasy"]) == 0
    capsys.readouterr()
    engine = AssetPipeline(manager, project.project_id)
    state = engine.load()
    assert state.generic_prompt == "Watercolor fantasy"
    assert engine.local_path(state.style_reference).read_bytes() == output.getvalue()
    assert main(common + ["--remove-style-reference"]) == 0
    capsys.readouterr()
    assert engine.load().style_reference is None
    assert engine.load().generic_prompt == "Watercolor fantasy"


def test_table_checkbox_regenerates_selected_asset(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    state = engine.generate("character_references")
    record = next(iter(state.records.values()))
    before = record.revision
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    assert app.button(key="regenerate-selected-assets").disabled
    key = f"production-selection-{project.project_id}-False"
    app.session_state[key] = {"edited_rows": {0: {"Regenerate": True}}, "added_rows": [], "deleted_rows": []}
    app.run()
    assert not app.exception
    assert not app.button(key="regenerate-selected-assets").disabled
    app.button(key="regenerate-selected-assets").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=20)
        app.run()
    assert not app.exception
    assert engine.load().records[record.stage + ":" + record.subject].revision == before + 1
