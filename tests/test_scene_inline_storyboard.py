from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.storyboard_editor import save_scene
from test_scene_production import episode
from test_studio import world


def test_scene_saves_merge_unrelated_edits_and_reject_target_conflict(episode):
    _,_,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    before = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    first,second = before["scenes"][0]["sceneId"],before["scenes"][1]["sceneId"]
    one,two = deepcopy(before),deepcopy(before)
    one["scenes"][0]["action"] = "First scene saved independently"
    two["scenes"][1]["action"] = "Second scene saved independently"
    save_scene(pipeline,first,one,source)
    result = save_scene(pipeline,second,two,source)
    assert result.scenes[0].action == one["scenes"][0]["action"]
    assert result.scenes[1].action == two["scenes"][1]["action"]
    current = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    with pytest.raises(ValueError,match="changed in another editor"):
        save_scene(pipeline,first,one,source)
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == current


def display(engine):
    from story_channel.scene_image_cards_ui import render
    render(engine)


def test_each_image_card_autosaves_and_approves_only_its_scene(episode):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    scenes = pipeline.artifact("storyboard").scenes
    first,second = scenes[0].scene_id,scenes[1].scene_id
    app = AppTest.from_function(display,args=(engine,),default_timeout=30).run()
    assert not app.exception
    panels = [p for p in app.expander if p.label == "Storyboard"]
    assert len(panels) == len(scenes)
    for sid in (first,second):
        app.session_state[f"scene-image-card-{project.project_id}-{sid}-storyboard-panel"] = True
    app.run()
    assert not app.exception
    assert not any(b.label == "Save storyboard changes" for b in app.button)
    actions = [w for w in app.text_area if w.label == "Action"]
    first_key,second_key = actions[0].key,actions[1].key
    app.text_area(key=second_key).set_value("Second scene action autosaved").run()
    assert not app.exception
    assert pipeline.artifact("storyboard").scenes[1].action == "Second scene action autosaved"
    app.text_area(key=first_key).set_value("New first scene action").run()
    assert not app.exception
    result = pipeline.artifact("storyboard")
    assert result.scenes[0].action == "New first scene action"
    assert result.scenes[1].action == "Second scene action autosaved"
    manifest = pipeline.inspect()
    assert "storyboard" in manifest.approvals
    assert manifest.steps["prompts"].status == "succeeded"
    assert "prompts" in manifest.content_approvals
    assert "New first scene action" in pipeline.artifact("prompts").model_dump_json()
    app.run()
    assert not app.exception
    assert not app.button(key="retry-asset-scene_images:"+first).disabled
    assert not app.button(key="retry-asset-scene_images:"+second).disabled


def test_inline_asset_requirement_is_added_to_scene_and_shared_assets(episode):
    world,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    app = AppTest.from_function(display,args=(engine,),default_timeout=30).run()
    app.session_state[f"scene-image-card-{project.project_id}-{sid}-storyboard-panel"] = True
    app.run()
    next(w for w in app.selectbox if w.label == "Asset type").select("Prop").run()
    next(w for w in app.text_area if w.label == "Short asset description").set_value("A small brass bell with a red ribbon").run()
    next(b for b in app.button if b.label == "Add requirement to scene").click().run()
    assert not app.exception
    board = pipeline.artifact("storyboard")
    prop = next(item for item in board.props if item.description == "A small brass bell with a red ribbon")
    assert prop.prop_id in board.scenes[0].prop_ids
    from story_channel.storyboard_assets import requests
    assert any(row["id"] == prop.prop_id for row in requests(world.manager,world.project_id,"prop"))


def test_inline_edit_is_saved_and_approved_before_same_click_generation(episode,monkeypatch):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    calls = []
    def launch(engine,operation,*args,**kwargs):
        calls.append(pipeline.artifact("storyboard").scenes[0].action)
        assert "storyboard" in pipeline.manager.load(project.project_id).approvals
        assert pipeline.manager.load(project.project_id).steps["prompts"].status == "succeeded"
    monkeypatch.setattr("story_channel.ui_tasks.launch_assets",launch)
    app = AppTest.from_function(display,args=(engine,),default_timeout=30).run()
    app.session_state[f"scene-image-card-{project.project_id}-{sid}-storyboard-panel"] = True
    app.run()
    next(w for w in app.text_area if w.label == "Action").set_value("Unsaved edit committed alongside Generate")
    app.button(key="retry-asset-scene_images:"+sid).click().run()
    assert not app.exception
    assert calls == ["Unsaved edit committed alongside Generate"]
    assert not any("Save or revert" in item.value for item in app.error)


def test_auto_approval_requires_previously_approved_storyboard(episode):
    _,project,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    data = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    sid = data["scenes"][0]["sceneId"]
    data["scenes"][0]["action"] = "Needs ordinary review first"
    save_scene(pipeline,sid,data,source)
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    data["scenes"][0]["action"] = "Cannot bypass initial approval"
    with pytest.raises(ValueError,match="Approve the storyboard"):
        save_scene(pipeline,sid,data,source,auto_approve=True)
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source


def test_invalid_autosave_preserves_canonical_scene_and_blocks_generation(episode,monkeypatch):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    calls = []
    monkeypatch.setattr("story_channel.ui_tasks.launch_assets",lambda *a,**k:calls.append(True))
    app = AppTest.from_function(display,args=(engine,),default_timeout=30).run()
    app.session_state[f"scene-image-card-{project.project_id}-{sid}-storyboard-panel"] = True
    app.run()
    # Unknown character IDs in dialogue must not be silently approved.
    def reject(*args,**kwargs):
        raise ValueError("Scene validation failed")
    monkeypatch.setattr("story_channel.storyboard_editor.save_scene",reject)
    next(w for w in app.text_area if w.label == "Action").set_value("Keep invalid draft visible")
    app.button(key="retry-asset-scene_images:"+sid).click().run()
    assert not app.exception and calls == []
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
    assert any("Scene validation failed" in item.value for item in app.error)
    assert next(w for w in app.text_area if w.label == "Action").value == "Keep invalid draft visible"
    assert app.button(key="retry-asset-scene_images:"+sid).disabled
