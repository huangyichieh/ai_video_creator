from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.creative import StoryboardDetailBackground
from story_channel.files import atomic_write,input_hash
from story_channel.location_details import add_draft,remove
from story_channel.storyboard_assets import sync,requests
from test_storyboard_assets import board
from test_studio import world


def test_explicit_deletion_survives_storyboard_sync_and_can_be_readded(board):
    world,episode,output = board
    lid = output.scenes[0].location_id
    output.scenes[0].target_area = "office_wide"
    output.detail_backgrounds = [StoryboardDetailBackground(location_id=lid,detail_id="office_wide",
        area="Office wide",description="Wide office view")]
    path = world.manager.project_path(episode.project_id)/"scenes.json"
    atomic_write(path,output.model_dump_json(by_alias=True,indent=2))
    episode.steps["storyboard"].output_hash = input_hash(output.model_dump(mode="json",by_alias=True))
    world.manager.save(episode)
    original = path.read_bytes()
    sync(world.manager,world.project_id)
    engine = AssetPipeline(world.manager,world.project_id,demo=True)
    assert "office_wide" in engine.load().location_reference_sets[lid]["detail_references"]
    add_draft(engine,lid,"window_area","Window area","Keep this draft")
    remove(engine,lid,"office_wide",confirmation="office_wide")
    # Configuration edits must preserve the deletion marker, too.
    BackgroundLibrary(engine).configure_references(lid,floor_plan="A room",object_layout={"desk":"north"})
    for _ in range(2):
        sync(world.manager,world.project_id)
        spec = engine.load().location_reference_sets[lid]
        assert "office_wide" not in spec["detail_references"]
        assert "window_area" in spec["detail_references"]
        assert "office_wide" in spec["removed_detail_ids"]
    assert path.read_bytes() == original
    assert any(row["detailId"] == "office_wide" for row in requests(world.manager,world.project_id,"detail_background"))
    add_draft(engine,lid,"office_wide","Restored office","User explicitly recreated this draft")
    sync(world.manager,world.project_id)
    spec = engine.load().location_reference_sets[lid]
    assert "office_wide" not in spec["removed_detail_ids"]
    assert spec["detail_references"]["office_wide"]["area"] == "Restored office"
