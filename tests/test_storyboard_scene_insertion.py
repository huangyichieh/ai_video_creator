from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.costs import CostTracker
from story_channel.storyboard_scene_insertion import insert
from test_storyboard_regeneration import Editor
from test_scene_production import episode
from test_studio import world


def generated_scene(data):
    data.update(action="A new transition beat",visualDescription="Light from the window falls on the keeper",
        shotType="medium",cameraMovement="static",durationSec=3)


@pytest.mark.parametrize("gap",["first","middle","last"])
def test_insert_at_each_position_preserves_neighbors_and_records_usage(episode,gap):
    _,project,pipeline = episode
    before = pipeline.artifact("storyboard")
    position = {"first":0,"middle":1,"last":len(before.scenes)}[gap]
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    script = (pipeline.path/"script.json").read_bytes()
    provider = Editor(generated_scene)
    after = insert(pipeline,position,source,"Show a quiet transition",provider=provider)
    assert len(after.scenes) == len(before.scenes)+1
    assert after.scenes[position].action == "A new transition beat"
    assert [scene.scene_id for scene in after.scenes] == [f"scene_{i:02d}" for i in range(1,len(after.scenes)+1)]
    assert [scene.order for scene in after.scenes] == list(range(1,len(after.scenes)+1))
    neighbors = [*after.scenes[:position],*after.scenes[position+1:]]
    excluded = {"scene_id","order","status","asset_refs"}
    assert [s.model_dump(exclude=excluded) for s in neighbors] == [s.model_dump(exclude=excluded) for s in before.scenes]
    assert sum(s.duration_sec for s in after.scenes) == pytest.approx(sum(s.duration_sec for s in before.scenes)+3)
    assert (pipeline.path/"script.json").read_bytes() == script
    manifest = pipeline.manager.load(project.project_id)
    assert "storyboard" not in manifest.approvals
    assert manifest.steps["prompts"].status == "stale"
    assert CostTracker(pipeline.manager,project.project_id).records()[-1].outcome == "succeeded"
    context = provider.calls[0]["context"]
    assert context["previousScene"] == (before.scenes[position-1].model_dump(mode="json",by_alias=True) if position else None)
    assert context["nextScene"] == (before.scenes[position].model_dump(mode="json",by_alias=True) if position < len(before.scenes) else None)


def test_insertion_rejects_stale_sources_invalid_gaps_and_invalid_ai_output(episode):
    _,project,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    provider = Editor(generated_scene)
    with pytest.raises(ValueError,match="another session"):
        insert(pipeline,0,source+" ","Add a transition",provider=provider)
    for position in (-1,999,True):
        with pytest.raises(ValueError,match="position"):
            insert(pipeline,position,source,"Add a transition",provider=provider)
    with pytest.raises(ValueError,match="Enter a prompt"):
        insert(pipeline,0,source," ",provider=provider)
    assert provider.calls == []
    pipeline.settings.llm_max_retries = 0
    def invalid(data):
        generated_scene(data)
        data["locationId"] = "invented_room"
    with pytest.raises(ValueError,match="Unknown location"):
        insert(pipeline,1,source,"Add a transition",provider=Editor(invalid))
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
    assert CostTracker(pipeline.manager,project.project_id).records()[-1].outcome == "failed"


def test_insertion_links_open_without_interrupting_tabs_and_confirm_correct_gap(episode,monkeypatch):
    _,_,pipeline = episode
    before = pipeline.artifact("storyboard")
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
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
    links = [b for b in app.button if b.label == "add scene in between"]
    assert len(links) == len(before.scenes)+1
    for position in (0,len(before.scenes)):
        with patch("streamlit.rerun",side_effect=AssertionError("Opening must not interrupt rendering")):
            app.button(key=links[position].key).click().run()
        assert not app.exception
        assert app.session_state["storyboard-scene-insertion-target"]["position"] == position
        assert any(item.value == "Parent tabs remain mounted" for item in app.caption)
        assert next(b for b in app.button if b.label == "Confirm").disabled
        next(b for b in app.button if b.label == "Cancel").click().run()
        assert not app.exception
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
    real_insert = insert
    monkeypatch.setattr("story_channel.storyboard_scene_insertion.insert",
        lambda pipeline,position,expected,instructions:real_insert(pipeline,position,expected,instructions,provider=Editor(generated_scene)))
    def launch(callback,*args,success=None,**kwargs):
        result = callback()
        if success:
            success(result)
    monkeypatch.setattr("story_channel.ui_tasks.launch",launch)
    app.button(key=links[1].key).click().run()
    next(w for w in app.text_area if w.label == "New scene prompt").set_value("Add a quiet transition").run()
    next(b for b in app.button if b.label == "Confirm").click().run()
    assert not app.exception
    assert app.session_state.get("storyboard-scene-insertion-target") is None
    assert pipeline.artifact("storyboard").scenes[1].action == "A new transition beat"


def test_insert_blocks_unsaved_drafts(episode):
    _,_,pipeline = episode
    def display(pipeline):
        from story_channel.dialog_ui import show_pending_dialog
        from story_channel.storyboard_editor_ui import render
        show_pending_dialog()
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    next(w for w in app.text_area if w.label == "Action").set_value("Unsaved staging").run()
    next(b for b in app.button if b.label == "add scene in between").click().run()
    assert not app.exception
    assert app.session_state.get("storyboard-scene-insertion-target") is None
    assert any("Save or revert" in w.value for w in app.warning)
