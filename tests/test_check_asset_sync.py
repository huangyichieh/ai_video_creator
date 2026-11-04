import pytest

from story_channel.audio_assets import binding,items,upload
from story_channel.check_asset_sync import synchronize,save_audio_settings,audio_settings,save_audio_details
from story_channel.storyboard_asset_check import check,intact
from story_channel.storyboard_audio import generate
from story_channel.media_utils import silence
from story_channel.episode_list_ui import episode_status
from test_storyboard_audio_generation import Audio
from test_scene_production import episode
from test_studio import world


@pytest.mark.parametrize("kind",["SFX","BGM"])
def test_episode_audio_edits_publish_to_assets_and_shared_replacements_return(episode,kind):
    world,project,pipeline = episode
    manager,pid = world.manager,project.project_id
    before = manager.load(pid)
    row = next(row for row in check(manager,pid) if row["Category"] == kind)
    identity = row["ID"]
    save_audio_details(manager,pid,kind,identity,"Edited reusable sound","A carefully edited sound description")
    save_audio_settings(manager,pid,kind,identity,"Use soft textures",4,True)
    assert items(manager,world.project_id,kind)[identity]["title"] == "Edited reusable sound"
    assert audio_settings(manager,world.project_id,kind,identity)["prompt"] == "Use soft textures"
    provider = Audio()
    relative = generate(manager,pid,kind,identity,"Use soft textures",4,provider=provider)
    assert len(provider.prompts) == 1
    source = binding(manager,world.project_id,kind,identity)
    local = binding(manager,pid,kind,identity)
    assert source["fileHash"] == local["fileHash"]
    assert intact(world.path,source) and intact(pipeline.path,local)
    assert (world.path/relative).read_bytes() == (pipeline.path/relative).read_bytes()
    # Stable SFX IDs survive title edits; the storyboard cue is untouched.
    assert identity in items(manager,world.project_id,kind)
    upload(manager,world.project_id,kind,identity,silence(.7),"new.wav","Original audio, owned by creator")
    replacement = binding(manager,world.project_id,kind,identity)
    assert replacement["fileHash"] != source["fileHash"]
    synchronize(manager,pid)
    assert binding(manager,pid,kind,identity)["fileHash"] == replacement["fileHash"]
    assert intact(pipeline.path,binding(manager,pid,kind,identity))
    after = pipeline.inspect()
    assert episode_status(manager,after) == "READY TO ANIMATE"
    assert after.approvals == before.approvals
    assert after.content_approvals == before.content_approvals


def test_background_refresh_adopts_shared_master_revision_without_story_changes(episode):
    from story_channel.assets import AssetPipeline
    from story_channel.location_pairs import generate as generate_pack
    from story_channel.background_library import BackgroundLibrary
    from story_channel.dual_panorama import MASTERS,pair_ready
    from test_location_master_pack import approve_pack
    world,project,pipeline = episode
    manager,pid = world.manager,project.project_id
    before = manager.load(pid)
    root = AssetPipeline(manager,world.project_id)
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    previous = AssetPipeline(manager,pid).load().records[f"backgrounds:{lid}__{MASTERS[0]}"].asset.asset_id
    generate_pack(root,lid,"masters","Keep the approved layout, refine surface detail")
    approve_pack(root,lid)
    BackgroundLibrary(root).approve(lid)
    synchronize(manager,pid)
    target = AssetPipeline(manager,pid)
    state = target.load()
    assert state.records[f"backgrounds:{lid}__{MASTERS[0]}"].asset.asset_id != previous
    assert pair_ready(target,state,lid)
    assert next(row for row in check(manager,pid) if row["ID"] == lid)["Ready"]
    after = pipeline.inspect()
    assert after.approvals == before.approvals and after.content_approvals == before.content_approvals


def test_publishing_does_not_overwrite_a_newer_shared_audio_revision(episode):
    from story_channel.audio_assets import publish
    from story_channel.check_asset_sync import shared_audio_hash
    world,project,pipeline = episode
    manager,pid = world.manager,project.project_id
    row = next(row for row in check(manager,pid) if row["Category"] == "SFX")
    identity = row["ID"]
    relative = generate(manager,pid,"SFX",identity,"A clock tick",3,provider=Audio())
    expected = shared_audio_hash(manager,pid,"SFX",identity)
    upload(manager,world.project_id,"SFX",identity,silence(.8),"replacement.wav","Original owned audio")
    newer = binding(manager,world.project_id,"SFX",identity)
    with pytest.raises(ValueError,match="changed during the request"):
        publish(manager,pid,"SFX",identity,row["Asset"],relative,shared_expected=expected)
    assert binding(manager,world.project_id,"SFX",identity) == newer
    assert (pipeline.path/relative).is_file()


def test_shared_refresh_does_not_change_episode_cast_when_world_adds_character(episode):
    from story_channel.identity import IdentityManager
    world,project,pipeline = episode
    original = (pipeline.path/"characters.json").read_bytes()
    expected_ids = {c.character_id for c in pipeline.artifact("characters").characters}
    before = world.manager.load(project.project_id)
    world.generate_characters("A visiting guest",single=True)
    assert len(world.pipeline.artifact("characters").characters) > len(expected_ids)
    synchronize(world.manager,project.project_id)
    identity = IdentityManager(world.manager,project.project_id)
    library = identity.load()
    identity.validate(library)
    assert set(library.voices) == expected_ids
    assert (pipeline.path/"characters.json").read_bytes() == original
    after = pipeline.inspect()
    assert after.approvals == before.approvals
    assert after.content_approvals == before.content_approvals
