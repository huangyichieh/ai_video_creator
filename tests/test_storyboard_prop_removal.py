import pytest
from streamlit.testing.v1 import AppTest
from story_channel.storyboard_prop_removal import remove
from story_channel.costs import CostTracker
from test_storyboard_regeneration import Editor
from test_scene_production import episode
from test_studio import world


def setup(pipeline):
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    board = pipeline.artifact("storyboard")
    data = board.model_dump(mode="json",by_alias=True)
    data["props"] = [{"propId":"briefcase","title":"Briefcase","description":"Leather briefcase","modes":["open","closed"]}]
    data["scenes"][0]["propIds"] = ["briefcase"]
    data["scenes"][0]["action"] = "She opens the briefcase."
    from story_channel.storyboard_editor import save
    save(pipeline,data,source)
    return (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("modes",[["open"],["open","closed"]])
def test_removes_only_selected_episode_dependencies_and_adapts_text(episode,modes):
    world,project,pipeline = episode
    source = setup(pipeline)
    pipeline.approve("storyboard")
    approval_before = pipeline.manager.load(project.project_id).approvals["storyboard"]
    before = pipeline.artifact("storyboard")
    shared = (world.path/"library_media.json").read_bytes() if (world.path/"library_media.json").exists() else None
    def edit(board):
        board["scenes"][0]["action"] = "She pauses, then nods in agreement."
        board["scenes"][0]["visualDescription"] = "She stands beside the desk with empty hands."
    result = remove(pipeline,["prop:briefcase:"+mode for mode in modes],source,provider=Editor(edit))
    assert result.scenes[0].action == "She pauses, then nods in agreement."
    assert result.scenes[0].dialogue == before.scenes[0].dialogue
    assert result.scenes[1:] == before.scenes[1:]
    if len(modes) == 1:
        assert result.props[0].modes == ["closed"]
        assert result.scenes[0].prop_ids == ["briefcase"]
    else:
        assert not result.props and not result.scenes[0].prop_ids
    assert (world.path/"library_media.json").read_bytes() == shared if shared is not None else not (world.path/"library_media.json").exists()
    assert pipeline.manager.load(project.project_id).steps["prompts"].status == "stale"
    current = pipeline.inspect()
    assert "storyboard" in current.approvals and current.steps["storyboard"].status == "succeeded"
    assert current.approvals["storyboard"].content_hash != approval_before.content_hash
    assert "retained after scoped prop removal" in current.approvals["storyboard"].note
    # Inspect automatically recompiles prompts for an approved episode board.
    assert current.steps["prompts"].status == "succeeded"
    assert current.content_approvals["prompts"].content_hash == current.steps["prompts"].output_hash
    assert CostTracker(pipeline.manager,project.project_id).records()[-1].outcome == "succeeded"


def test_invalid_ai_edits_or_concurrent_changes_do_not_save(episode):
    _,_,pipeline = episode
    source = setup(pipeline)
    provider = Editor(lambda board:board["scenes"][0].update(durationSec=999))
    with pytest.raises(ValueError,match="protected story"):
        remove(pipeline,["prop:briefcase:open"],source,provider=provider)
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
    with pytest.raises(ValueError,match="text must be adapted"):
        remove(pipeline,["prop:briefcase:open"],source,provider=Editor(lambda board:None))
    with pytest.raises(ValueError,match="another session"):
        remove(pipeline,["prop:briefcase:open"],source+" ",provider=provider)
    assert len(provider.calls) == 1


def test_prop_removal_does_not_grant_approval_to_an_unapproved_storyboard(episode):
    _,_,pipeline = episode
    source = setup(pipeline)
    assert "storyboard" not in pipeline.manager.load(pipeline.project_id).approvals
    provider = Editor(lambda board:board["scenes"][0].update(action="She nods, keeping her hands at her sides."))
    remove(pipeline,["prop:briefcase:open","prop:briefcase:closed"],source,provider=provider)
    assert "storyboard" not in pipeline.inspect().approvals


def test_dialog_selects_modes_and_cancel_does_not_edit(episode,monkeypatch):
    _,_,pipeline = episode
    source = setup(pipeline)
    calls = []
    monkeypatch.setattr("story_channel.storyboard_prop_removal.remove",lambda pipeline,choices,expected:calls.append((choices,expected)))
    monkeypatch.setattr("story_channel.ui_tasks.launch",lambda callback,*args,**kwargs:callback())
    def display(pipeline):
        import streamlit as st
        from story_channel.storyboard_prop_removal_ui import removal_dialog
        if st.session_state.get("storyboard-prop-removal-target"):
            removal_dialog(pipeline,st.session_state["storyboard-prop-removal-target"])
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25)
    app.session_state["storyboard-prop-removal-target"] = {"project_id":pipeline.project_id,"token":"test"}
    app.run()
    assert not app.exception and len(app.checkbox) == 2
    assert app.button(key="prop-removal-test-confirm").disabled
    app.checkbox[0].check().run()
    app.button(key="prop-removal-test-confirm").click().run()
    assert not app.exception and len(calls) == 1
    assert calls[0][0] == ["prop:briefcase:open"] and calls[0][1] == source
    app.button(key="prop-removal-test-cancel").click().run()
    assert not app.exception and "storyboard-prop-removal-target" not in app.session_state
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
