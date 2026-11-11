from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.storyboard_scene_assets import add_requirement
from story_channel.storyboard_editor import inventory,save
from story_channel.storyboard_assets import requests
from test_scene_production import episode
from test_studio import world


def test_scene_requirements_link_to_scene_and_sync_to_shared_assets_on_save(episode):
    world,project,pipeline = episode
    catalog = inventory(pipeline)
    before = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    data = deepcopy(before)
    sid = data["scenes"][0]["sceneId"]
    identities = {}
    for kind,description in (("Background","A pale tiled room with blue cabinets"),
                             ("Prop","A small brass key with a round bow"),
                             ("BGM","Quiet piano with slow warm strings, no vocals")):
        original = deepcopy(data)
        result,identity = add_requirement(data,catalog,sid,kind,description)
        assert data == original
        data = result
        identities[kind] = identity
    scene = data["scenes"][0]
    assert scene["locationId"] == identities["Background"]
    assert scene["targetArea"] == ""
    assert scene["propIds"] == [*before["scenes"][0]["propIds"],identities["Prop"]]
    assert scene["bgmId"] == identities["BGM"]
    assert data["props"][-1]["modes"] == ["default"]
    assert data["scenes"][1:] == before["scenes"][1:]
    assert not any(row["id"] in identities.values() for row in requests(world.manager,world.project_id))
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    save(pipeline,data,source)
    rows = {row["id"]:row for row in requests(world.manager,world.project_id)}
    assert set(identities.values()) <= rows.keys()
    assert all(rows[identity]["sources"][0]["sceneIds"] == [sid] for identity in identities.values())
    from story_channel.background_library import locations
    from story_channel.assets import AssetPipeline
    from story_channel.library_media import LibraryMedia
    from story_channel.bgm_library import entries
    assert identities["Background"] in {item.location_id for item in locations(AssetPipeline(world.manager,world.project_id).creative)}
    assert identities["Prop"] in LibraryMedia(world.manager,world.project_id).load()["props"]
    assert identities["BGM"] in entries(world.manager,world.project_id)
    assert "storyboard" not in world.manager.load(project.project_id).approvals
    assert world.manager.load(project.project_id).steps["prompts"].status == "stale"


def test_scene_requirement_rejects_blank_description_unknown_kind_and_missing_scene():
    data = {"scenes":[{"sceneId":"scene_01"}],"backgrounds":[],"props":[],"bgm":[]}
    catalog = {"backgrounds":{},"props":{},"bgm":{}}
    with pytest.raises(ValueError,match="description"):
        add_requirement(data,catalog,"scene_01","Prop"," ")
    with pytest.raises(ValueError,match="Choose"):
        add_requirement(data,catalog,"scene_01","Unknown","Something")
    with pytest.raises(ValueError,match="no longer exists"):
        add_requirement(data,catalog,"missing","Background","Something")


def test_scene_asset_ui_stages_all_types_preserves_edits_and_saves(episode):
    world,_,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    before = pipeline.artifact("storyboard")
    def display(pipeline):
        from story_channel.storyboard_editor_ui import render
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    assert not app.exception
    assert len([panel for panel in app.expander if panel.label == "Add new background, prop or BGM"]) == len(before.scenes)
    next(w for w in app.text_area if w.label == "Action").set_value("An unsaved visual action").run()
    kind_key = next(w.key for w in app.selectbox if w.label == "Asset type")
    description_key = next(w.key for w in app.text_area if w.label == "Short asset description")
    title_key = next(w.key for w in app.text_input if w.label == "Asset title (optional)")
    add_key = next(b.key for b in app.button if b.label == "Add requirement to scene")
    for kind,description in (("Background","A bright stone courtyard"),("Prop","A round blue ceramic cup"),("BGM","Slow plucked strings and soft piano")):
        app.selectbox(key=kind_key).select(kind).run()
        app.text_input(key=title_key).set_value("New "+kind).run()
        app.text_area(key=description_key).set_value(description).run()
        app.button(key=add_key).click().run()
        assert not app.exception
        assert app.text_area(key=description_key).value == ""
        assert app.text_input(key=title_key).value == ""
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
    assert next(w for w in app.text_area if w.label == "Action").value == "An unsaved visual action"
    next(b for b in app.button if b.label == "Save storyboard changes").click().run()
    assert not app.exception
    after = pipeline.artifact("storyboard")
    assert after.scenes[0].location_id.startswith("background_")
    assert any(identity.startswith("prop_") for identity in after.scenes[0].prop_ids)
    assert after.scenes[0].bgm_id.startswith("bgm_")
    assert after.scenes[0].action == "An unsaved visual action"
    assert after.scenes[1:] == before.scenes[1:]
    assert {"background","prop","bgm"} <= {row["kind"] for row in requests(world.manager,world.project_id)}


def test_revert_discards_new_scene_requirements_without_creating_assets(episode):
    world,_,pipeline = episode
    original = pipeline.artifact("storyboard")
    def display(pipeline):
        from story_channel.storyboard_editor_ui import render
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    next(w for w in app.text_area if w.label == "Short asset description").set_value("A new wooden doorway").run()
    next(b for b in app.button if b.label == "Add requirement to scene").click().run()
    assert not app.exception
    next(b for b in app.button if b.label == "Revert changes").click().run()
    assert not app.exception
    selected = next(w for w in app.selectbox if w.label == "Background")
    assert selected.value == original.scenes[0].location_id
    assert pipeline.artifact("storyboard") == original
    assert not any(row["description"] == "A new wooden doorway" for row in requests(world.manager,world.project_id))
