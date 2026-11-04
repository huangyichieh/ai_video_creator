from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary,locations
from story_channel.assets_workflow import next_assets_action
from story_channel.floor_plan_guides import publish_guides
from story_channel import location_pairs
from story_channel.studio import Episodes,approve_content,content_digest
from story_channel.pipeline import Pipeline
from story_channel.animation import AnimationDirector
from story_channel.rendering import RenderPipeline
from story_channel.animations_workflow import animation_snapshot
from story_channel.scene_production import SceneProduction
from test_studio import world
from asset_test_helpers import complete_character_parts


def test_inventory_satisfies_assets_prerequisite_without_any_approvals(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager,world.project_id,demo=True)
    action = next_assets_action(engine,engine.inspect())
    assert action.stage == "complete"
    assert "At least 1 character and 1 background exist" in action.detail
    assert not world.manager.load(world.project_id).asset_approvals


def test_unused_draft_characters_and_locations_do_not_block_scene_production(world):
    world.generate_characters()
    world.generate_characters("An extra visitor who is not used in this episode")
    root = AssetPipeline(world.manager,world.project_id,demo=True)
    assert len(root.creative.artifact("characters").characters) >= 2
    root.generate("character_references",subject="keeper")
    complete_character_parts(root,subjects=["keeper"])
    root.approve_character("keeper")
    used = next(location for location in locations(root.creative) if location.location_id == "lighthouse")
    library = BackgroundLibrary(root)
    library.configure_references(used.location_id,floor_plan="Fixed room center",object_layout={"window":"north wall"})
    publish_guides(root,used.location_id,root.load().location_reference_sets[used.location_id])
    generated = root.generate("backgrounds",subject=used.location_id)
    location_pairs.approve(root,used.location_id,"masters",expected={name:generated.records[f"backgrounds:{used.location_id}__{name}"].asset.asset_id for name in location_pairs.names("masters")})
    library.approve(used.location_id)
    library.add("Unused room","A pending room not used by the episode")
    assert not {"characters","backgrounds"} <= world.manager.load(world.project_id).asset_approvals.keys()
    service = Episodes(world.manager,world.project_id)
    episode = service.create("Only the keeper","The keeper protects a memory",12)
    pipeline = Pipeline(world.manager,episode.project_id)
    approve_content(pipeline,"outline",expected=content_digest(pipeline,"outline"))
    pipeline.generate("story")
    pipeline.generate("review")
    pipeline.approve("story")
    pipeline.generate("script")
    approve_content(pipeline,"script",expected=content_digest(pipeline,"script"))
    pipeline.generate("storyboard")
    pipeline.approve("storyboard")
    pipeline.generate("prompts")
    approve_content(pipeline,"prompts",expected=content_digest(pipeline,"prompts"))
    service.import_assets(episode.project_id)
    assets = AssetPipeline(world.manager,episode.project_id,demo=True)
    production = assets.inspect()
    assert production.next_stage == "scene_images"
    assert {task.subject for task in assets.tasks(production) if task.stage == "character_references"} == {"keeper"}
    action,rows = animation_snapshot(RenderPipeline(world.manager,episode.project_id))
    prerequisite = next(row for row in rows if row["Stage"] == "Assets prerequisites")
    assert prerequisite["Status"] == "Done"
    assert action.stage == "scene_images" and action.runnable
    assets.generate("scene_images")
    director = AnimationDirector(world.manager,episode.project_id)
    director.prepare()
    scene_service = SceneProduction(world.manager,episode.project_id)
    for scene in pipeline.artifact("storyboard").scenes:
        scene_service.approve_direction(scene.scene_id,expected=scene_service.direction_hash(scene.scene_id))
    director.approve_plan()
    director.approve_performance()
    assert director.require_ready()
