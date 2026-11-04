from streamlit.testing.v1 import AppTest

from story_channel.episode_list_ui import episode_status
from story_channel.animation_episode_status import animation_episode_status
from story_channel.assets import AssetPipeline
from story_channel.animation import AnimationDirector
from test_scene_production import episode
from test_studio import world


def test_animation_status_follows_real_scene_progress_instead_of_story_status(episode):
    world,project,_ = episode
    manager = world.manager
    current = manager.load(project.project_id)
    assert episode_status(manager,current) == "READY TO ANIMATE"
    assert animation_episode_status(manager,current) == "NOT STARTED · Scene images"
    assets = AssetPipeline(manager,project.project_id)
    assets.generate("scene_images")
    assert animation_episode_status(manager,manager.load(project.project_id)) == "READY · Prepare animation direction"
    AnimationDirector(manager,project.project_id).prepare()
    assert "AWAITING APPROVAL" in animation_episode_status(manager,manager.load(project.project_id))


def test_running_and_failed_scene_requests_have_animation_specific_status(episode):
    world,project,_ = episode
    manager = world.manager
    assets = AssetPipeline(manager,project.project_id)
    state = assets.inspect()
    from story_channel.asset_models import AssetRecord
    task = next(task for task in assets.tasks(state) if task.stage == "scene_images")
    record = AssetRecord(stage="scene_images",subject=task.subject,status="running")
    state.records[task.key] = record
    assets._save(state)
    assert animation_episode_status(manager,manager.load(project.project_id)) == "GENERATING · Scene images"
    record.status,record.error = "failed","Provider request failed"
    assets._save(state)
    assert animation_episode_status(manager,manager.load(project.project_id)) == "FAILED · Scene images"


def test_episode_list_can_show_animation_status_without_changing_story_list(episode):
    world,project,_ = episode
    def display(manager,world_id):
        from story_channel.episode_list_ui import episode_list
        from story_channel.studio import Episodes
        episodes = Episodes(manager,world_id).list()
        episode_list(world_id,episodes,episodes[0].project_id,manager=manager,status_view="animation")
    app = AppTest.from_function(display,args=(world.manager,world.project_id),default_timeout=25).run()
    assert not app.exception
    assert list(app.dataframe[0].value["Animation status"]) == ["NOT STARTED · Scene images"]
