import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from test_assets import complete
from test_assets_ui import prepare


def finish(app):
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=30)
        app.run()


def test_finishing_ui_add_save_revert_navigate_select_and_generate(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    complete(AssetPipeline(manager, project.project_id))
    engine = RenderPipeline(manager, project.project_id)
    engine.configure(options=RenderOptions(width=320, height=180, fps=12))
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    pid = project.project_id + "-video"
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key="nav-animations").click().run()
    assert not app.exception
    app.number_input(key=f"finishing-setting-{pid}-effect-duration").set_value(.5).run()
    app.button(key=f"finishing-add-{pid}").click().run()
    draft = f"finishing-draft-{pid}"
    text = app.text_area(key=draft).value
    assert len(json.loads(text)["effects"]) == 1
    app.button(key="nav-story-settings").click().run()
    app.button(key="nav-animations").click().run()
    assert app.text_area(key=draft).value == text
    app.button(key=f"finishing-save-{pid}").click().run()
    finish(app)
    assert not app.exception
    effect = engine.finishing.load().plan.effects[0]
    baseline = app.text_area(key=draft).value
    app.text_area(key=draft).set_value(baseline + "\n ").run()
    app.button(key=f"finishing-revert-{pid}").click().run()
    assert app.text_area(key=draft).value == baseline
    app.session_state[f"finishing-table-{pid}"] = {"edited_rows": {0: {"Select": True}}, "added_rows": [], "deleted_rows": []}
    app.run()
    assert not app.button(key=f"finishing-retry-{pid}").disabled
    app.button(key=f"finishing-retry-{pid}").click().run()
    finish(app)
    assert not app.exception
    record = engine.finishing.load().records[f"vfx:{effect.effect_id}"]
    assert record.status == "succeeded" and record.revision == 1
    app.button(key=f"finishing-modify-{pid}").click().run()
    assert not app.exception
    assert app.session_state["modify-target"]["kind"] == "finishing_plan"
    app.button(key="cancel-openai-modification").click().run()
    assert not app.exception
