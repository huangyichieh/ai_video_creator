import pytest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.creative import StoryboardDetailBackground
from story_channel.location_details import add_draft
from story_channel.storyboard_assets import sync,requests
from story_channel.modules import validate_output
from story_channel.storyboard_asset_check import check
from test_scene_production import episode
from test_studio import world
from test_location_details import png


def test_generation_receives_existing_details_and_creates_only_new_drafts(episode,monkeypatch):
    world,project,pipeline = episode
    shared = AssetPipeline(world.manager,world.project_id)
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    state = BackgroundLibrary(shared).add_detail_reference(lid,"Window area",png(),detail_id="window_area",description="A close-up of the window")
    original = state.location_reference_sets[lid]["detail_references"]["window_area"].copy()
    captured = []
    generate = pipeline._generate
    def generated(stage,context):
        result = generate(stage,context)
        if stage == "storyboard":
            captured.append(context)
            result.scenes[0].location_id = lid
            result.scenes[0].target_area = "window_area"
            result.scenes[1].location_id = lid
            result.scenes[1].target_area = "printer_area"
            result.detail_backgrounds = [StoryboardDetailBackground(location_id=lid,detail_id="printer_area",area="Printer area",
                description="Scene-ready printer close-up, preserving the room layout and materials")]
        return result
    monkeypatch.setattr(pipeline,"_generate",generated)
    pipeline.generate("storyboard",force=True)
    assert captured[0]["detailBackgrounds"][lid]["window_area"]["hasImage"]
    state = shared.load()
    details = state.location_reference_sets[lid]["detail_references"]
    assert details["window_area"] == original
    assert details["printer_area"]["status"] == "draft"
    assert "file" not in details["printer_area"]
    assert details["printer_area"]["description"].startswith("Scene-ready printer")
    assert pipeline.inspect().steps["storyboard"].status == "succeeded"
    sync(world.manager,world.project_id)
    assert len([row for row in requests(world.manager,world.project_id,"detail_background") if row["detailId"] == "printer_area"]) == 1
    add_draft(shared,lid,"printer_area","Edited area","Keep the manually edited prompt")
    sync(world.manager,world.project_id)
    assert shared.load().location_reference_sets[lid]["detail_references"]["printer_area"]["description"] == "Keep the manually edited prompt"
    assert pipeline.inspect().steps["storyboard"].status == "succeeded"
    row = next(r for r in check(world.manager,project.project_id) if r.get("ID") == lid+"__detail_printer_area")
    assert not row["Ready"]


def test_new_detail_declarations_validate_parent_id_and_usage(episode):
    world,project,pipeline = episode
    board = pipeline.artifact("storyboard").model_copy(deep=True)
    context = pipeline._context("storyboard",fresh_assets=True)
    board.detail_backgrounds = [StoryboardDetailBackground(location_id="unknown_room",detail_id="desk_area",area="Desk",description="Close-up")]
    with pytest.raises(ValueError,match="parent location"):
        validate_output("storyboard",board,context)
    board.detail_backgrounds[0].location_id = board.scenes[0].location_id
    with pytest.raises(ValueError,match="selected by a scene"):
        validate_output("storyboard",board,context)
    board.scenes[0].target_area = "desk_area"
    validate_output("storyboard",board,context)


def test_old_storyboards_remain_valid_without_empty_new_fields(episode):
    world,project,pipeline = episode
    assert "detailBackgrounds" not in pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    (pipeline.path/"storyboard_reference_context.json").unlink()
    manifest = world.manager.load(project.project_id)
    manifest.steps["storyboard"].input_hash = pipeline._fingerprint("storyboard")
    manifest.steps["storyboard"].retained_input_hash = ""
    world.manager.save(manifest)
    assert pipeline.inspect().steps["storyboard"].status == "succeeded"
