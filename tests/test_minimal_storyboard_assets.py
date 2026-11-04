from story_channel.creative import Location,StoryboardProp,StoryboardBGM
from story_channel.storyboard_assets import sync,asset_catalog
from story_channel.files import atomic_write,input_hash
from story_channel.modules import build_prompt
from test_storyboard_assets import board
from test_scene_production import episode
from test_studio import world


def test_unused_declarations_do_not_create_reusable_asset_drafts(board):
    world,project,output = board
    output.backgrounds.append(Location(location_id="unused_room",name="Unused room",description="A decorative room"))
    output.props.append(StoryboardProp(prop_id="unused_vase",title="Vase",description="An unused vase"))
    output.bgm.append(StoryboardBGM(bgm_id="unused_music",title="Unused music",description="Optional piano"))
    atomic_write(world.manager.project_path(project.project_id)/"scenes.json",output.model_dump_json(by_alias=True))
    project.steps["storyboard"].output_hash = input_hash(output.model_dump(mode="json",by_alias=True))
    world.manager.save(project)
    rows = sync(world.manager,world.project_id)
    assert {row["kind"] for row in rows} == {"background","prop","sfx"}
    assert not any(row["id"].startswith("unused_") for row in rows)


def test_new_planning_sees_parent_library_without_invalidating_approved_context(episode):
    world,project,pipeline = episode
    from story_channel.library_media import LibraryMedia
    from story_channel.prop_drafts import save
    from story_channel.audio_assets import add_sfx
    context = pipeline._context("storyboard")
    approvals = pipeline.manager.load(project.project_id).approvals
    save(LibraryMedia(world.manager,world.project_id),"existing_compass","Compass","Brass compass",["default"],expected=input_hash(None))
    sid = add_sfx(world.manager,world.project_id,"Soft click","A soft mechanical click")
    fresh = pipeline._context("storyboard",fresh_assets=True)
    assert fresh["assetCatalog"]["props"]["existing_compass"]["modes"] == ["default"]
    assert fresh["assetCatalog"]["sfx"][sid]["cue"] == "Soft click"
    assert pipeline._context("storyboard") == context
    assert pipeline.inspect().approvals == approvals


def test_storyboard_prompt_requests_minimal_assets_without_a_hard_quota():
    prompt = build_prompt("storyboard",{})
    assert "MINIMUM NECESSARY ASSETS" in prompt
    assert "do not create variants just for different shots" in prompt
    assert "Furniture, decor and fixed objects already represented by the background" in prompt
    assert "Only declare assets that are referenced by at least one scene" in prompt
    assert "do not remove essential story content" in prompt
    assert "leave bgmId null and bgmMood empty" in prompt


def test_storyboard_prompt_separates_visual_and_audio_asset_descriptions():
    prompt = build_prompt("storyboard",{})
    assert "New prop descriptions are VISUAL ONLY" in prompt
    assert "Every new scene sfx entry is SOUND ONLY" in prompt
    assert "Keep character interaction and action in scene fields" in prompt
    assert "Keep duration and loop settings in sfxTiming" in prompt
    assert "rewrite narrative/visual labels into sound-only descriptions" in prompt
