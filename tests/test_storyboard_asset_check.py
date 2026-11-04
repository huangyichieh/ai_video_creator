import json
import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.animation import AnimationDirector
from story_channel.animations_workflow import animation_snapshot,TABS
from story_channel.rendering import RenderPipeline
from story_channel.storyboard_asset_check import check,require,bind_sound,sound_id
from story_channel.files import atomic_write
from story_channel.pipeline import ApprovalRequired
from test_scene_production import episode
from test_studio import world


def test_missing_required_audio_blocks_all_animation_entry_points(episode):
    world,project,pipeline = episode
    assert all(row["Ready"] for row in check(world.manager,project.project_id))
    (pipeline.path/"storyboard_sfx.json").unlink()
    rows = check(world.manager,project.project_id)
    assert any(row["Category"] == "SFX" and not row["Ready"] for row in rows)
    assert all(row["Ready"] for row in rows if row["Category"] != "SFX")
    action,table = animation_snapshot(RenderPipeline(world.manager,project.project_id))
    assert action.stage == "check_assets" and action.tab == "Check assets" and not action.runnable
    assert any(row["Tab"] == "Check assets" for row in table)
    with pytest.raises(ApprovalRequired,match="Check assets"):
        AssetPipeline(world.manager,project.project_id).generate("scene_images")
    with pytest.raises(ApprovalRequired,match="Check assets"):
        AnimationDirector(world.manager,project.project_id).prepare()


def test_linked_sounds_unlock_gate_and_broken_files_relock_it(episode):
    world,project,pipeline = episode
    file = pipeline.path/"storyboard_sfx.json"
    old = json.loads(file.read_text())["entries"]
    file.unlink()
    for item in old.values():
        atomic_write(pipeline.path/(item["file"]+".source.json"),json.dumps({"file":item["file"],"source":"Test licensed sound"}))
        bind_sound(world.manager,project.project_id,item["name"],item["file"])
    from story_channel.storyboard_asset_check import approve
    for row in check(world.manager,project.project_id):
        if row["Category"] == "SFX":
            approve(world.manager,project.project_id,"SFX",row["ID"],expected=row["ContentHash"])
    require(world.manager,project.project_id)
    damaged = next(iter(old.values()))
    (pipeline.path/damaged["file"]).write_bytes(b"broken")
    with pytest.raises(ApprovalRequired,match="Check assets"):
        require(world.manager,project.project_id)


def test_check_assets_is_first_tab_and_other_controls_are_hidden_when_missing(episode):
    world,project,pipeline = episode
    (pipeline.path/"storyboard_sfx.json").unlink()
    def display(manager,project_id):
        from story_channel.animations_ui import animations_page
        animations_page(manager,project_id)
    app = AppTest.from_function(display,args=(world.manager,project.project_id),default_timeout=25).run()
    assert not app.exception
    assert [tab.label for tab in app.tabs][:8] == list(TABS)
    assert app.tabs[0].label == "Check assets"
    for category in ("Background","Props","BGM","SFX"):
        assert any(w.value == "### "+category for w in app.markdown)
    assert not any("Generate / resume scene images" == b.label for b in app.button)
    assert app.button(key=f"animation-workflow-next-{project.project_id}").disabled


def test_prop_drafts_do_not_count_as_created_visuals(episode):
    world,project,pipeline = episode
    from story_channel.media_utils import file_hash
    from test_location_details import png
    board = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    board["props"] = [{"propId":"test_printer","title":"Printer","description":"Office printer","modes":["default"]}]
    board["scenes"][0]["propIds"] = ["test_printer"]
    atomic_write(pipeline.path/"scenes.json",json.dumps(board))
    row = next(row for row in check(world.manager,project.project_id) if row["Category"] == "Props")
    assert not row["Ready"]
    path = pipeline.path/"images"/"test_printer.png"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(png())
    file = pipeline.path/"library_media.json"
    media = json.loads(file.read_text(encoding="utf-8"))
    media["visuals"]["prop:test_printer:default"] = [{"revision":1,"fileHash":file_hash(path),
        "asset":{"filePath":path.relative_to(pipeline.path).as_posix()}}]
    atomic_write(file,json.dumps(media))
    assert next(row for row in check(world.manager,project.project_id) if row["Category"] == "Props")["Ready"]


def test_background_and_detail_are_separate_deduplicated_items(episode):
    world,project,pipeline = episode
    from story_channel.background_library import BackgroundLibrary
    from story_channel.location_details import add_draft
    from test_location_details import png
    shared = AssetPipeline(world.manager,world.project_id)
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    BackgroundLibrary(shared).add_detail_reference(lid,"Window area",png(),detail_id="window_area")
    add_draft(shared,lid,"unused_area","Unused area")
    from story_channel.studio import Episodes
    Episodes(world.manager,world.project_id).refresh_assets(project.project_id)
    board = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    for scene in board["scenes"][:2]:
        scene["locationId"],scene["targetArea"] = lid,"window_area"
    atomic_write(pipeline.path/"scenes.json",json.dumps(board))
    rows = [r for r in check(world.manager,project.project_id) if r["Category"] == "Background"]
    background = next(r for r in rows if r["ID"] == lid)
    detail = next(r for r in rows if r["ID"] == lid+"__detail_window_area")
    assert background["Type"] == "Background"
    assert detail["Type"] == "Detail background"
    assert "window_area" in detail["Asset"]
    assert sum(r["ID"] == detail["ID"] for r in rows) == 1
    assert all(scene["sceneId"] in detail["Scenes"] for scene in board["scenes"][:2])
    assert not any("unused_area" in r["ID"] for r in rows)
    assert detail["Ready"]
