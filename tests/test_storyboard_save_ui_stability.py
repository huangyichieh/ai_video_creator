from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from test_scene_production import episode
from test_studio import world


def test_save_finishes_parent_tabs_and_saves_text_committed_by_same_click(episode):
    _,_,pipeline = episode
    def display(pipeline):
        import streamlit as st
        from story_channel.storyboard_editor_ui import render
        tabs = st.tabs(["Outline","Story","Script","Storyboard","Prompts"])
        with tabs[3]:
            render(pipeline)
            st.caption("Storyboard footer remains mounted")
        with tabs[4]:
            st.caption("Following tab remains mounted")
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    assert not app.exception
    # No intervening .run(): the pending text must be collected during the
    # save click's normal render, rather than taken from a stale closure.
    next(w for w in app.text_area if w.label == "Action").set_value("Latest text committed with Save")
    with patch("streamlit.rerun",side_effect=AssertionError("Saving must not interrupt the parent tab tree")):
        next(b for b in app.button if b.label == "Save storyboard changes").click().run()
    assert not app.exception
    assert pipeline.artifact("storyboard").scenes[0].action == "Latest text committed with Save"
    assert any(c.value == "Storyboard footer remains mounted" for c in app.caption)
    assert any(c.value == "Following tab remains mounted" for c in app.caption)
    assert any("changes saved" in item.value for item in app.success)
    app.run()  # A subsequent normal refresh shows canonical data and clears Saving.
    assert not app.exception
    assert not next(b for b in app.button if b.label == "Save storyboard changes").disabled


def test_failed_save_preserves_draft_and_parent_tabs(episode,monkeypatch):
    _,_,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    def display(pipeline):
        import streamlit as st
        from story_channel.storyboard_editor_ui import render
        tabs = st.tabs(["Storyboard","Next tab"])
        with tabs[0]:
            render(pipeline)
        with tabs[1]:
            st.caption("Following tab remains mounted")
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    def fail(*args,**kwargs):
        raise ValueError("Content changed in another session. Reload before saving.")
    monkeypatch.setattr("story_channel.storyboard_editor_ui.save",fail)
    next(w for w in app.text_area if w.label == "Action").set_value("Keep this unsaved draft")
    with patch("streamlit.rerun",side_effect=AssertionError("Failed save must not interrupt the page")):
        next(b for b in app.button if b.label == "Save storyboard changes").click().run()
    assert not app.exception
    assert any("another session" in item.value for item in app.error)
    assert any(c.value == "Following tab remains mounted" for c in app.caption)
    app.run()
    assert not app.exception
    assert next(w for w in app.text_area if w.label == "Action").value == "Keep this unsaved draft"
    assert not next(b for b in app.button if b.label == "Save storyboard changes").disabled
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
