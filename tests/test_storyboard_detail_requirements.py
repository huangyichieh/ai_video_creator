from copy import deepcopy

from streamlit.testing.v1 import AppTest

from story_channel.storyboard_editor import reconcile_detail_requirements
from story_channel.storyboard_assets import requests
from story_channel.assets import AssetPipeline
from test_storyboard_detail_selection import add_details
from test_scene_production import episode
from test_studio import world


def test_requirements_follow_exact_parent_detail_pairs_and_preserve_shared_usage():
    old = {"locationId":"office","detailId":"desk","area":"Desk","description":"Keep edited description"}
    data = {"scenes":[{"locationId":"office","targetArea":"desk"},
        {"locationId":"office","targetArea":"desk"}],"detailBackgrounds":[old]}
    catalog = {"detail_backgrounds":{"other__detail_desk":{"area":"Other desk","description":"Another room"}}}
    original = deepcopy(data)
    assert reconcile_detail_requirements(data,catalog) == [old]
    assert data == original
    data["scenes"][0] = {"locationId":"other","targetArea":"desk"}
    assert reconcile_detail_requirements(data,catalog) == [
        {"locationId":"other","detailId":"desk","area":"Other desk","description":"Another room"},old]
    data["scenes"][1]["targetArea"] = ""
    assert len(reconcile_detail_requirements(data,catalog)) == 1
    data["scenes"][0]["targetArea"] = "arbitrary area description"
    assert reconcile_detail_requirements(data,catalog) == []


def test_ui_background_changes_refresh_requirements_and_keep_library_assets(episode):
    world,project,pipeline,lid = add_details(episode)
    shared = AssetPipeline(world.manager,world.project_id)
    original = shared.load().location_reference_sets[lid]["detail_references"]["window_area"]
    def display(pipeline):
        from story_channel.storyboard_editor_ui import render
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    def backgrounds():
        return [field for field in app.selectbox if field.label == "Background"]
    def requirements_table():
        return next(frame.value for frame in app.dataframe if "detailId" in frame.value.columns)
    def save():
        next(button for button in app.button if button.label == "Save storyboard changes").click().run()
        assert not app.exception
    key = lid+"__detail_window_area"
    backgrounds()[0].select(key).run()
    assert not app.exception
    assert requirements_table()["detailId"].tolist() == ["window_area"]
    backgrounds()[1].select(key).run()
    save()
    assert len(pipeline.artifact("storyboard").detail_backgrounds) == 1
    request = next(row for row in requests(world.manager,world.project_id,"detail_background") if row["detailId"] == "window_area")
    assert len(request["sources"][0]["sceneIds"]) == 2
    backgrounds()[0].select(lid).run()
    assert requirements_table()["detailId"].tolist() == ["window_area"]
    save()
    assert pipeline.artifact("storyboard").scenes[0].target_area == ""
    request = next(row for row in requests(world.manager,world.project_id,"detail_background") if row["detailId"] == "window_area")
    assert len(request["sources"][0]["sceneIds"]) == 1
    backgrounds()[1].select(lid).run()
    assert requirements_table().empty
    save()
    assert not pipeline.artifact("storyboard").detail_backgrounds
    assert not requests(world.manager,world.project_id,"detail_background")
    assert shared.load().location_reference_sets[lid]["detail_references"]["window_area"] == original
    assert shared.local_path(original["file"]).is_file()


def test_save_prunes_unselected_new_declarations_without_validation_error(episode):
    from story_channel.storyboard_editor import save
    world,project,pipeline = episode
    data = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    lid = data["scenes"][0]["locationId"]
    data["detailBackgrounds"] = [{"locationId":lid,"detailId":"unused_closeup","area":"Unused close-up","description":"No scene needs this anymore"}]
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    output = save(pipeline,data,source)
    assert not output.detail_backgrounds
    assert not requests(world.manager,world.project_id,"detail_background")
    # The JSON editor uses edit_bundle directly and must behave the same way.
    import json
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    pipeline.edit_bundle({"storyboard":json.dumps(data)},expected={"storyboard":source})
    assert not pipeline.artifact("storyboard").detail_backgrounds
