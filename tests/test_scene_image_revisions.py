import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.costs import CostTracker
from test_scene_production import episode
from test_studio import world


def test_scene_revision_selection_preserves_upstream_and_numbers(episode):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    key = "scene_images:"+sid
    first = engine.generate("scene_images",subject=sid).records[key]
    second = engine.generate("scene_images",subject=sid,force=True).records[key]
    approvals = pipeline.manager.load(project.project_id).approvals
    costs = len(CostTracker(engine.manager,engine.project_id).records())
    history_path = engine.path/"history"/"assets"/"scene_images"/sid/(first.asset.asset_id+".json")
    history = history_path.read_bytes()
    state = engine.apply_revision("scene_images",sid,first.asset.asset_id)
    assert state.records[key].asset.asset_id == first.asset.asset_id
    assert state.records[key].revision == 1 and state.records[key].status == "succeeded"
    assert pipeline.manager.load(project.project_id).approvals == approvals
    assert len(CostTracker(engine.manager,engine.project_id).records()) == costs
    assert history_path.read_bytes() == history
    assert second.asset.asset_id in {r.asset.asset_id for r in engine.revisions("scene_images",sid)}
    third = engine.generate("scene_images",subject=sid,force=True).records[key]
    assert third.revision == 3
    engine.local_path(first.asset.file_path).write_bytes(b"modified")
    with pytest.raises(ValueError,match="missing or modified"):
        engine.apply_revision("scene_images",sid,first.asset.asset_id)
    assert engine.load().records[key].asset.asset_id == third.asset.asset_id


def display(engine,sid):
    from story_channel.scene_image_cards_ui import card
    card(engine,sid,"test-scene-revision-card")


def test_scene_card_previews_without_applying_then_applies(episode):
    from test_ui import finish_background
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    key = "scene_images:"+sid
    first = engine.generate("scene_images",subject=sid).records[key]
    second = engine.generate("scene_images",subject=sid,force=True).records[key]
    prefix = f"reference-revisions-{project.project_id}-scene_images-{sid}"
    app = AppTest.from_function(display,args=(engine,sid),default_timeout=30)
    app.session_state[prefix+"-panel"] = True
    app.run()
    assert not app.exception
    app.selectbox(key=prefix+"-choice").select(first.asset.asset_id).run()
    assert not app.exception
    assert engine.load().records[key].asset.asset_id == second.asset.asset_id
    assert not app.button(key=prefix+"-apply").disabled
    app.button(key=prefix+"-apply").click().run()
    finish_background(app)
    app.run()
    assert not app.exception
    assert engine.load().records[key].asset.asset_id == first.asset.asset_id
    assert app.button(key=prefix+"-apply").disabled
    assert app.button(key=prefix+"-apply").label == "Applied revision 1"
    assert any("revision 1" in item.value for item in app.markdown)
