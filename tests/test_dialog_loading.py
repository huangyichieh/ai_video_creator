from pathlib import Path
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.visual_generation import VisualGeneration
from story_channel.projects import ProjectManager
from test_studio import world


def test_dialog_shell_opens_before_deferred_data_is_resolved():
    app = AppTest.from_string('''
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx
from story_channel.dialog_ui import dialog, Deferred
def prepare():
    assert get_script_run_ctx().has_dialog_opened
    return "Prepared data"
@dialog("Example")
def example(data):
    st.write(data)
example(Deferred(prepare))
example(Deferred(lambda: (_ for _ in ()).throw(AssertionError("Duplicate dialog must not run"))))
''').run()
    assert not app.exception
    assert any(text.value == "Prepared data" for text in app.markdown)


def test_visual_modify_callback_only_requests_dialog_without_saving_inputs(world, monkeypatch):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.generate("character_references")
    cid = world.pipeline.artifact("characters").characters[0].character_id
    def forbidden(*args, **kwargs):
        raise AssertionError("Input preparation must happen inside the dialog")
    monkeypatch.setattr(VisualGeneration, "update", forbidden)
    app = AppTest.from_string('''
import streamlit as st
from story_channel.visual_generation_ui import generation_section
engine=st.session_state["engine"]
generation_section(engine, engine.load(), "character_references", st.session_state["cid"])
''')
    app.session_state["engine"], app.session_state["cid"] = engine, cid
    app.run()
    app.button(key="modify-asset-character_references:" + cid).click().run()
    assert not app.exception
    assert app.session_state["modify-target"]["pending_generation_input"]["mode"] == "text"


def test_pending_modify_opens_before_project_list_rebuild(world, monkeypatch):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.generate("character_references")
    cid = world.pipeline.artifact("characters").characters[0].character_id
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    original, opened = ProjectManager.list_projects, []
    def list_projects(manager):
        import streamlit as st
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if st.session_state.get("modify-target"):
            opened.append(get_script_run_ctx().has_dialog_opened)
        return original(manager)
    monkeypatch.setattr(ProjectManager, "list_projects", list_projects)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    app.button(key="modify-asset-character_references:" + cid).click().run()
    assert not app.exception
    assert opened and all(opened)
    assert app.button(key="cancel-openai-modification")
    app.button(key="cancel-openai-modification").click().run()
    assert not app.exception
    assert "modify-target" not in app.session_state
    assert not any(button.key == "cancel-openai-modification" for button in app.button)
