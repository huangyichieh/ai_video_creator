import pytest
from streamlit.testing.v1 import AppTest
from story_channel.library_media import LibraryMedia
from story_channel.prop_removal import snapshot
from story_channel.prop_revisions import apply
from story_channel.costs import CostTracker
from test_prop_removal import create
from test_studio import world
from test_scene_production import episode


def test_apply_saved_revision_changes_selection_without_generation_or_cost(world):
    media = create(world)
    media.image("prop:case:open","Second visual")
    state = media.load()
    old = state["visuals"]["prop:case:open"][0]
    old["asset"].update(provider="runway",model="gen4_image")
    media.save(state)
    costs = CostTracker(world.manager,world.project_id).records()
    closed = state["activeVisuals"]["prop:case:closed"]
    version = apply(media,"case","open",1,expected=snapshot(state,"case","open"))
    after = media.load()
    assert version == old
    assert after["activeVisuals"]["prop:case:open"]["revision"] == 1
    assert after["activeVisuals"]["prop:case:closed"] == closed
    assert after["visuals"] == state["visuals"]
    assert CostTracker(world.manager,world.project_id).records() == costs


def test_missing_or_changed_revision_and_concurrent_generation_are_rejected(world):
    media = create(world)
    expected = snapshot(media.load(),"case","open")
    media.image("prop:case:open","Second visual")
    with pytest.raises(ValueError,match="changed"):
        apply(media,"case","open",1,expected=expected)
    state = media.load()
    image = media.assets.local_path(state["visuals"]["prop:case:open"][0]["asset"]["filePath"])
    image.write_bytes(b"changed image")
    with pytest.raises(ValueError,match="missing or modified"):
        apply(media,"case","open",1,expected=snapshot(state,"case","open"))
    assert media.load() == state


@pytest.mark.parametrize("cross_mode",[False,True])
def test_apply_from_check_assets_syncs_episode_and_stays_automatically_ready(episode,cross_mode):
    world,project,pipeline = episode
    from test_storyboard_prop_removal import setup
    from story_channel.check_prop_assets import sync,approve,digest
    from story_channel.storyboard_asset_check import check
    setup(pipeline)
    pipeline.approve("storyboard")
    before = pipeline.manager.load(project.project_id).approvals["storyboard"]
    media = LibraryMedia(world.manager,world.project_id)
    for mode in ("open","closed"):
        media.image("prop:briefcase:"+mode,"Leather briefcase "+mode)
    media.image("prop:briefcase:open","Second open visual")
    sync(media,project.project_id,"briefcase")
    approve(media,project.project_id,"briefcase",["open","closed"],expected=digest(media.load(),"briefcase",["open","closed"]))
    applied = apply(media,"briefcase","open",1,expected=snapshot(media.load(),"briefcase","open"),episode_id=project.project_id,
        source_mode="closed" if cross_mode else "open")
    copied = LibraryMedia(world.manager,project.project_id).load()
    assert copied["activeVisuals"]["prop:briefcase:open"]["revision"] == applied["revision"]
    rows = {row["ID"]:row for row in check(world.manager,project.project_id) if row["Category"] == "Props"}
    assert rows["prop:briefcase:open"]["Ready"] and rows["prop:briefcase:open"]["Approved"]
    assert rows["prop:briefcase:closed"]["Approved"]
    assert pipeline.inspect().approvals["storyboard"] == before


def test_mode_revision_dialog_previews_and_applies_selected_image(world,monkeypatch):
    import streamlit as st
    images = []
    original = st.image
    def image(value,*args,**kwargs):
        images.append(value)
        return original(value,*args,**kwargs)
    monkeypatch.setattr(st,"image",image)
    media = create(world)
    media.image("prop:case:open","Second visual")
    def display(media):
        import streamlit as st
        if st.session_state.get("prop-revisions-target"):
            from story_channel.dialog_ui import show_pending_dialog
            show_pending_dialog()
        from story_channel.check_prop_drafts_ui import render_library
        render_library(media)
    app = AppTest.from_function(display,args=(media,),default_timeout=25).run()
    key = f"asset-prop-editor-{world.project_id}-case"
    app.session_state[key] = True
    app.run()
    app.button(key=key+"-revisions-open").click().run()
    assert not app.exception
    assert next(button for button in app.button if button.label == "Applied").disabled
    choice = next(widget for widget in app.selectbox if widget.label == "Saved revision")
    choice.select(1).run()
    assert not app.exception
    old_file = media.load()["visuals"]["prop:case:open"][0]["asset"]["filePath"]
    assert str(media.assets.local_path(old_file)) in images
    next(button for button in app.button if button.label == "Apply revision").click().run()
    assert not app.exception
    assert "prop-revisions-target" not in app.session_state
    assert media.load()["activeVisuals"]["prop:case:open"]["revision"] == 1


def test_cross_mode_reuse_keeps_history_costs_and_shared_file_after_source_removal(world):
    from story_channel.prop_removal import remove
    media = create(world)
    before = media.load()
    source = before["visuals"]["prop:case:closed"][0]
    costs = CostTracker(world.manager,world.project_id).records()
    reused = apply(media,"case","open",1,expected=snapshot(before,"case","open"),source_mode="closed")
    assert reused["revision"] == 2 and reused["reusedFrom"] == {"mode":"closed","revision":1}
    assert reused["asset"] == source["asset"] and reused["fileHash"] == source["fileHash"]
    state = media.load()
    assert state["visuals"]["prop:case:open"][0] == before["visuals"]["prop:case:open"][0]
    assert state["visuals"]["prop:case:closed"] == before["visuals"]["prop:case:closed"]
    apply(media,"case","open",1,expected=snapshot(state,"case","open"),source_mode="closed")
    assert len(media.load()["visuals"]["prop:case:open"]) == 2
    assert CostTracker(world.manager,world.project_id).records() == costs
    remove(media,"case",mode="closed",expected=snapshot(media.load(),"case","closed"))
    assert media.assets.local_path(reused["asset"]["filePath"]).is_file()
    assert media.load()["activeVisuals"]["prop:case:open"]["revision"] == 2


def test_cross_mode_reuse_rejects_changed_source_preview(world):
    media = create(world)
    before = media.load()
    with pytest.raises(ValueError,match="source revision changed"):
        apply(media,"case","open",1,expected=snapshot(before,"case","open"),source_mode="closed",source_expected="0"*64)
    assert media.load() == before


def test_empty_mode_can_browse_another_mode_and_apply_it(world):
    media = create(world)
    state = media.load()
    state["visuals"].pop("prop:case:open")
    state["activeVisuals"].pop("prop:case:open")
    media.save(state)
    def display(media):
        import streamlit as st
        if st.session_state.get("prop-revisions-target"):
            from story_channel.dialog_ui import show_pending_dialog
            show_pending_dialog()
        from story_channel.check_prop_drafts_ui import render_library
        render_library(media)
    app = AppTest.from_function(display,args=(media,),default_timeout=25).run()
    key = f"asset-prop-editor-{world.project_id}-case"
    app.session_state[key] = True
    app.run()
    button = app.button(key=key+"-revisions-open")
    assert not button.disabled
    button.click().run()
    assert not app.exception
    assert next(widget for widget in app.selectbox if widget.label == "Source mode").value == "closed"
    next(button for button in app.button if button.label == "Apply to open").click().run()
    assert not app.exception
    assert media.load()["visuals"]["prop:case:open"][0]["reusedFrom"] == {"mode":"closed","revision":1}
