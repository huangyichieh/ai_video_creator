import pytest
from streamlit.testing.v1 import AppTest

from story_channel.costs import CostTracker
from story_channel.storyboard_split import split
from test_storyboard_regeneration import Editor
from test_scene_production import episode
from test_studio import world


def test_split_renumbers_all_scenes_and_preserves_neighbors_and_spoken_content(episode):
    world,project,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    before = pipeline.artifact("storyboard")
    index = min(1,len(before.scenes)-1)
    original = before.scenes[index]
    def change(data):
        data["scenes"][0]["action"] = "Begin the action with a wide view"
        data["scenes"][1]["action"] = "Finish the same action in close-up"
    provider = Editor(change)
    result = split(pipeline,original.scene_id,source,["Begin wide","Finish close"],provider=provider)
    assert len(result.scenes) == len(before.scenes)+1
    assert [scene.scene_id for scene in result.scenes] == [f"scene_{i:02d}" for i in range(1,len(result.scenes)+1)]
    assert [scene.order for scene in result.scenes] == list(range(1,len(result.scenes)+1))
    parts = result.scenes[index:index+2]
    assert sum(scene.duration_sec for scene in parts) == pytest.approx(original.duration_sec)
    assert [line for scene in parts for line in scene.dialogue] == original.dialogue
    assert " ".join(" ".join(scene.narration for scene in parts).split()) == " ".join(original.narration.split())
    neighbors = [*result.scenes[:index],*result.scenes[index+2:]]
    old_neighbors = [*before.scenes[:index],*before.scenes[index+1:]]
    excluded = {"scene_id","order","status","asset_refs"}
    assert [scene.model_dump(exclude=excluded) for scene in neighbors] == [scene.model_dump(exclude=excluded) for scene in old_neighbors]
    assert "storyboard" not in world.manager.load(project.project_id).approvals
    assert world.manager.load(project.project_id).steps["prompts"].status == "stale"
    assert CostTracker(world.manager,project.project_id).records()[-1].outcome == "succeeded"
    assert "Begin wide" in provider.calls[0]["modification_request"]


@pytest.mark.parametrize("change,match",[
    (lambda data:data["scenes"][0].update(durationSec=999),"durations"),
    (lambda data:data["scenes"][0].update(narration="Unrequested new narration"),"narration"),
    (lambda data:data["scenes"][0].update(locationId="another_room"),"background"),
    (lambda data:data["scenes"].pop(),"at least 2 items"),
])
def test_invalid_split_records_failure_without_saving(episode,change,match):
    world,project,pipeline = episode
    pipeline.settings.llm_max_retries = 0
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    with pytest.raises(ValueError,match=match):
        split(pipeline,sid,source,["Start","Finish"],provider=Editor(change))
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
    assert "storyboard" in world.manager.load(project.project_id).approvals
    assert CostTracker(world.manager,project.project_id).records()[-1].outcome == "failed"


def test_split_rejects_stale_source_and_incomplete_prompts_before_provider_call(episode):
    _,_,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    provider = Editor(lambda data:None)
    with pytest.raises(ValueError,match="another session"):
        split(pipeline,sid,source+" ",["Start","Finish"],provider=provider)
    with pytest.raises(ValueError,match="prompt for each"):
        split(pipeline,sid,source,["Start",""],provider=provider)
    assert provider.calls == []


def test_split_dialog_has_dynamic_prompts_and_cancel_preserves_source(episode):
    _,_,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    def display(pipeline):
        from story_channel.dialog_ui import show_pending_dialog
        from story_channel.storyboard_editor_ui import render
        show_pending_dialog()
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    buttons = [button for button in app.button if button.label == "Split scene"]
    assert len(buttons) == len(pipeline.artifact("storyboard").scenes)
    app.button(key=buttons[0].key).click().run()
    assert not app.exception
    assert next(button for button in app.button if button.label == "Split and save").disabled
    count = next(widget for widget in app.number_input if widget.label == "Number of resulting scenes")
    count.set_value(3).run()
    assert not app.exception
    assert len([widget for widget in app.text_area if widget.label.endswith(" prompt")]) == 3
    next(button for button in app.button if button.label == "Cancel").click().run()
    assert not app.exception
    assert app.session_state.get("storyboard-split-target") is None
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source


def test_split_opens_without_interrupting_parent_tab_render(episode,monkeypatch):
    _,_,pipeline = episode
    def display(pipeline):
        import streamlit as st
        from story_channel.dialog_ui import show_pending_dialog
        from story_channel.storyboard_editor_ui import render
        show_pending_dialog()
        tabs = st.tabs(["Outline","Story","Script","Storyboard","Prompts"])
        with tabs[3]:
            render(pipeline)
            st.caption("Storyboard footer remains mounted")
        with tabs[4]:
            st.caption("Prompts tab remains mounted")
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    assert not app.exception
    def interrupted(*args,**kwargs):
        raise AssertionError("Opening Split must not interrupt the parent page with st.rerun")
    monkeypatch.setattr("streamlit.rerun",interrupted)
    next(button for button in app.button if button.label == "Split scene").click().run()
    assert not app.exception
    assert any(widget.label == "Number of resulting scenes" for widget in app.number_input)
    assert any(item.value == "Storyboard footer remains mounted" for item in app.caption)
    assert any(item.value == "Prompts tab remains mounted" for item in app.caption)


def test_split_callback_blocks_unsaved_storyboard_edits(episode):
    _,_,pipeline = episode
    def display(pipeline):
        from story_channel.dialog_ui import show_pending_dialog
        from story_channel.storyboard_editor_ui import render
        show_pending_dialog()
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    next(widget for widget in app.text_area if widget.label == "Action").set_value("Unsaved staging change").run()
    next(button for button in app.button if button.label == "Split scene").click().run()
    assert not app.exception
    assert app.session_state.get("storyboard-split-target") is None
    assert any("Save or revert" in item.value for item in app.warning)
