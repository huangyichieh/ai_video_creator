import json
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.files import atomic_write
from story_channel.location_details import add_draft
from test_scene_production import episode
from test_studio import world


def app_for(manager,project_id):
    def display(manager,project_id):
        from story_channel.storyboard_asset_check_ui import render
        render(manager,project_id)
    return AppTest.from_function(display,args=(manager,project_id),default_timeout=25).run()


def test_pending_detail_drafts_are_collapsed_and_edit_shared_asset_only(episode,monkeypatch):
    world,project,pipeline = episode
    shared = AssetPipeline(world.manager,world.project_id)
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    add_draft(shared,lid,"desk_area","Desk","First detail prompt")
    add_draft(shared,lid,"window_area","Window","Second detail prompt")
    board = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    for scene,identity in zip(board["scenes"],("desk_area","window_area")):
        scene["targetArea"] = identity
    atomic_write(pipeline.path/"scenes.json",json.dumps(board))
    original = (pipeline.path/"scenes.json").read_bytes()
    app = app_for(world.manager,project.project_id)
    assert not app.exception
    keys = [f"background-card-detail-{world.project_id}-{lid}-{identity}-{project.project_id}" for identity in ("desk_area","window_area")]
    for key in keys:
        panel = next(panel for panel in app.expander if panel.key == key)
        assert not panel.proto.expanded
    assert not any(field.label == "Detail prompt" for field in app.text_area)
    app.session_state[keys[0]] = True
    app.run()
    assert not app.exception
    fields = [field for field in app.text_area if field.label == "Detail prompt"]
    assert len(fields) == 1 and fields[0].value == "First detail prompt"
    monkeypatch.setattr("story_channel.location_details_ui.launch_background",lambda engine,callback,*args,**kwargs:callback())
    fields[0].set_value("Edited shared detail prompt").run()
    next(button for button in app.button if button.label == "Save detail draft").click().run()
    assert not app.exception
    details = shared.load().location_reference_sets[lid]["detail_references"]
    assert details["desk_area"]["description"] == "Edited shared detail prompt"
    assert details["window_area"]["description"] == "Second detail prompt"
    assert (pipeline.path/"scenes.json").read_bytes() == original
    assert not next(panel for panel in app.expander if panel.key == keys[1]).proto.expanded


def test_missing_background_draft_uses_collapsed_existing_editor(episode):
    world,project,pipeline = episode
    shared = AssetPipeline(world.manager,world.project_id)
    location = BackgroundLibrary(shared).add("Cafe","A quiet cafe",reference_type="SIMPLE")
    board = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    board["scenes"][0]["locationId"] = location.location_id
    atomic_write(pipeline.path/"scenes.json",json.dumps(board))
    app = app_for(world.manager,project.project_id)
    key = f"check-background-editor-{project.project_id}-{location.location_id}"
    assert not app.exception
    assert not next(panel for panel in app.expander if panel.key == key).proto.expanded
    app.session_state[key] = True
    app.run()
    assert not app.exception
    assert any(field.label == "Background description" and field.value == "A quiet cafe" for field in app.text_area)
    assert any(button.label == "Generate master (OpenAI)" for button in app.button)


def test_unregistered_detail_declaration_can_be_added_from_its_collapsed_panel(episode,monkeypatch):
    world,project,pipeline = episode
    from story_channel.files import input_hash
    board = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    lid = board["scenes"][0]["locationId"]
    board["scenes"][0]["targetArea"] = "printer_area"
    board["detailBackgrounds"] = [{"locationId":lid,"detailId":"printer_area","area":"Printer area","description":"A printer close-up"}]
    atomic_write(pipeline.path/"scenes.json",json.dumps(board))
    manifest = world.manager.load(project.project_id)
    manifest.steps["storyboard"].output_hash = input_hash(board)
    world.manager.save(manifest)
    app = app_for(world.manager,project.project_id)
    key = f"check-background-editor-{project.project_id}-{lid}__detail_printer_area"
    assert not app.exception
    assert not next(panel for panel in app.expander if panel.key == key).proto.expanded
    app.session_state[key] = True
    app.run()
    assert not app.exception
    monkeypatch.setattr("story_channel.ui_tasks.launch",lambda callback,*args,**kwargs:callback())
    app.button(key=key+"-add").click().run()
    assert not app.exception
    detail = AssetPipeline(world.manager,world.project_id).load().location_reference_sets[lid]["detail_references"]["printer_area"]
    assert detail["status"] == "draft" and detail["description"] == "A printer close-up"
    assert not detail.get("file")
