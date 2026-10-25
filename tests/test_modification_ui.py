import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.pipeline import Pipeline
from story_channel.media_providers import DemoImageProvider
from test_parallel_assets import cast
from test_modification import Editor


def workspace(tmp_path, monkeypatch):
    manager, project = cast(tmp_path)
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    return manager, project, app


def finish(app):
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=20)
        app.run()
    assert not app.exception


def test_browse_modification_replaces_dialog_and_returns_to_saved_output(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    editor = Editor(lambda current: current.update(era="Future island"))
    monkeypatch.setattr("story_channel.modification.OpenAILLMProvider", lambda **_: editor)
    app.button(key="browse-world").click().run()
    app.button(key="modify-browser-openai").click().run()
    assert not app.exception
    assert app.button(key="apply-openai-modification").disabled
    prompt = next(w for w in app.text_area if w.label == "Modification prompt")
    prompt.set_value("Change to a futuristic era").run()
    app.button(key="apply-openai-modification").click().run()
    finish(app)
    assert Pipeline(manager, project.project_id).artifact("world").era == "Future island"
    assert app.button(key="modify-browser-openai")
    assert "modify-target" not in app.session_state
    key = f"browser-draft-{project.project_id}-world-world"
    assert json.loads(app.text_area(key=key).value)["era"] == "Future island"


def test_outline_prompt_modification_refreshes_editor_and_cancel_keeps_content(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    editor = Editor(lambda current: current.update(text="The friends find a second beacon."))
    monkeypatch.setattr("story_channel.modification.OpenAILLMProvider", lambda **_: editor)
    app.button(key="nav-story-settings").click().run()
    app.button(key="modify-outline-openai").click().run()
    app.button(key="cancel-openai-modification").click().run()
    assert manager.read_outline(project.project_id) == "Eight friends restore the beacon."
    app.button(key="modify-outline-openai").click().run()
    next(w for w in app.text_area if w.label == "Modification prompt").set_value("Add a second beacon").run()
    app.button(key="apply-openai-modification").click().run()
    finish(app)
    assert app.text_area(key=f"outline-{project.project_id}").value == "The friends find a second beacon."
    assert manager.load(project.project_id).steps["world"].status == "stale"


def test_asset_and_source_modification_dialogs_show_correct_targets(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    engine = AssetPipeline(manager, project.project_id)
    engine.set_generic_prompt("Watercolor")
    engine.generate("character_references", subject="cast_0")
    app.button(key="nav-assets").click().run()
    app.button(key="modify-asset-character_references:cast_0").click().run()
    assert not app.exception
    assert app.session_state["modify-target"]["kind"] == "asset"
    assert app.get("image")
    app.button(key="cancel-openai-modification").click().run()
    editor = Editor(lambda current: current.update(appearance="Long silver hair"))
    monkeypatch.setattr("story_channel.modification.OpenAILLMProvider", lambda **_: editor)
    prefix = f"asset-character-{project.project_id}-cast_0"
    app.button(key=prefix + "-modify").click().run()
    assert not app.exception
    assert app.session_state["modify-target"]["kind"] == "source"
    next(w for w in app.text_area if w.label == "Modification prompt").set_value("Make the hair silver").run()
    app.button(key="apply-openai-modification").click().run()
    finish(app)
    assert json.loads(app.text_area(key=prefix + "-draft").value)["appearance"] == "Long silver hair"


def test_image_dialog_applies_prompt_to_existing_image(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    engine = AssetPipeline(manager, project.project_id)
    engine.set_generic_prompt("Watercolor")
    original = engine.generate("character_references", subject="cast_0").records["character_references:cast_0"].model_copy(deep=True)
    calls = []

    class ImageEditor(DemoImageProvider):
        def generate(self, prompt, references, config):
            calls.append((prompt, references))
            output = super().generate(prompt, references, config)
            output.provider = "openai"
            return output

    monkeypatch.setattr("story_channel.assets.OpenAIImageProvider", lambda **_: ImageEditor())
    app.button(key="nav-assets").click().run()
    app.button(key="modify-asset-character_references:cast_0").click().run()
    next(w for w in app.text_area if w.label == "Modification prompt").set_value("Make lighting warmer").run()
    app.button(key="apply-openai-modification").click().run()
    finish(app)
    assert "Make lighting warmer" in calls[0][0]
    assert calls[0][1][-1] == engine.local_path(original.asset.file_path)
    assert engine.load().records["character_references:cast_0"].revision == 2
