from concurrent.futures import Future
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from story_channel.library_media import LibraryMedia
from story_channel.prop_drafts import save
from story_channel.files import input_hash
from test_studio import world


def media(world):
    result = LibraryMedia(world.manager,world.project_id)
    save(result,"case","Briefcase","Leather case",["open"],expected=input_hash(None))
    return result


@pytest.mark.parametrize("approval",[False,True])
def test_preview_reads_new_visual_without_relying_on_parent_snapshot(world,monkeypatch,approval):
    library = media(world)
    images = []
    original = st.image
    def image(value,*args,**kwargs):
        images.append(value)
        return original(value,*args,**kwargs)
    monkeypatch.setattr(st,"image",image)
    def display(library,approval):
        import streamlit as st
        from story_channel.check_prop_drafts_ui import visual_preview
        st.session_state.setdefault("parent-snapshot",library.load())
        st.text_input("Unrelated draft",key="unrelated",persist_state="session")
        visual_preview(library,"case","open",approval)
    app = AppTest.from_function(display,args=(library,approval)).run()
    assert not app.exception and not images
    app.text_input[0].set_value("Keep my edit").run()
    result = library.image("prop:case:open","Open case")
    app.run()
    assert not app.exception
    assert not app.session_state["parent-snapshot"]["visuals"]
    assert images[-1] == str(library.assets.local_path(result.file_path))
    assert any(item.value == "Revision 1" for item in app.caption)
    assert app.text_input[0].value == "Keep my edit"
    if approval:
        assert any(":green[Ready · automatically approved]" in item.value for item in app.caption)


def test_completed_prop_task_shows_image_without_full_app_rerun_or_approval(world,monkeypatch):
    library = media(world)
    images = []
    original = st.image
    def image(value,*args,**kwargs):
        images.append(value)
        return original(value,*args,**kwargs)
    monkeypatch.setattr(st,"image",image)
    def forbidden(*args,**kwargs):
        raise AssertionError("Prop completion must not rebuild the whole app")
    monkeypatch.setattr(st,"rerun",forbidden)
    def display(library):
        from story_channel.check_prop_drafts_ui import _card
        _card(library,library.project_id,"case",["open"],{},"test-prop",approval=True)
    app = AppTest.from_function(display,args=(library,)).run()
    app.session_state["test-prop"] = True
    app.run()
    assert not any(button.label == "Approve prop" for button in app.button)
    asset = library.image("prop:case:open","Open case")
    future = Future()
    future.set_result(asset)
    app.session_state["workspace-task"] = {"id":"completed","future":future,"check_prop_owner":library.project_id,
        "check_prop_id":"case","label":"Generating prop visual prop:case:open","success":None,"failure":None,
        "inline":True,"allow_parallel":True,"queue_enabled":True}
    app.run()
    assert not app.exception and not any(button.label == "Approve prop" for button in app.button)
    assert images[-1] == str(library.assets.local_path(asset.file_path))
    assert "workspace-task" not in app.session_state
