import pytest
from streamlit.testing.v1 import AppTest

from story_channel.storyboard_scene_removal import remove
from test_scene_production import episode
from test_studio import world


def test_remove_renumbers_preserves_remaining_content_and_allows_further_edits(episode):
    _,project,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    script = (pipeline.path/"script.json").read_bytes()
    before = pipeline.artifact("storyboard")
    after = remove(pipeline,before.scenes[0].scene_id,source)
    assert len(after.scenes) == len(before.scenes)-1
    excluded = {"scene_id","order","status","asset_refs"}
    assert [s.model_dump(exclude=excluded) for s in after.scenes] == [s.model_dump(exclude=excluded) for s in before.scenes[1:]]
    assert [s.scene_id for s in after.scenes] == [f"scene_{i:02d}" for i in range(1,len(after.scenes)+1)]
    assert [s.order for s in after.scenes] == list(range(1,len(after.scenes)+1))
    assert (pipeline.path/"script.json").read_bytes() == script
    manifest = pipeline.manager.load(project.project_id)
    assert "storyboard" not in manifest.approvals
    assert manifest.steps["prompts"].status == "stale"
    from story_channel.storyboard_regeneration import regenerate
    from test_storyboard_regeneration import Editor
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    regenerate(pipeline,after.scenes[0].scene_id,source,provider=Editor(lambda data:data.update(action="A closer look")))
    from story_channel.storyboard_split import split
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    result = split(pipeline,after.scenes[0].scene_id,source,["Begin","Finish"],provider=Editor(lambda data:None))
    assert len(result.scenes) == len(after.scenes)+1


def test_removal_rejects_stale_source_unknown_scene_and_last_scene(episode):
    _,_,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    with pytest.raises(ValueError,match="another session"):
        remove(pipeline,sid,source+" ")
    with pytest.raises(ValueError,match="no longer exists"):
        remove(pipeline,"not_a_scene",source)
    while len(pipeline.artifact("storyboard").scenes) > 1:
        remove(pipeline,pipeline.artifact("storyboard").scenes[0].scene_id,
            (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig"))
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    with pytest.raises(ValueError,match="at least one"):
        remove(pipeline,pipeline.artifact("storyboard").scenes[0].scene_id,source)
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source


def test_remove_dialog_cancel_and_confirm_keep_parent_tabs_mounted(episode):
    _,_,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    original_count = len(pipeline.artifact("storyboard").scenes)
    def display(pipeline):
        import streamlit as st
        from story_channel.dialog_ui import show_pending_dialog
        from story_channel.storyboard_editor_ui import render
        show_pending_dialog()
        tabs = st.tabs(["Storyboard","Prompts"])
        with tabs[0]:
            render(pipeline)
        with tabs[1]:
            st.caption("Parent tabs remain mounted")
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    next(b for b in app.button if b.key.endswith("remove-scene")).click().run()
    assert not app.exception
    assert any(item.value == "Parent tabs remain mounted" for item in app.caption)
    next(b for b in app.button if b.label == "Cancel").click().run()
    assert not app.exception
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
    next(b for b in app.button if b.key.endswith("remove-scene")).click().run()
    next(b for b in app.button if b.key.endswith("-confirm")).click().run()
    assert not app.exception
    assert len(pipeline.artifact("storyboard").scenes) == original_count-1
    if original_count == 2:
        assert next(b for b in app.button if b.key.endswith("remove-scene")).disabled


def test_remove_blocks_unsaved_edits(episode):
    _,_,pipeline = episode
    def display(pipeline):
        from story_channel.dialog_ui import show_pending_dialog
        from story_channel.storyboard_editor_ui import render
        show_pending_dialog()
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    next(w for w in app.text_area if w.label == "Action").set_value("Unsaved action").run()
    next(b for b in app.button if b.key.endswith("remove-scene")).click().run()
    assert not app.exception
    assert app.session_state.get("storyboard-scene-removal-target") is None
    assert any("Save or revert" in item.value for item in app.warning)
