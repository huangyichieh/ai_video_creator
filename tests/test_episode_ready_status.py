import pytest
from streamlit.testing.v1 import AppTest

from story_channel.creative import Prompts,PromptItem
from story_channel.models import ApprovalRecord,StepState
from story_channel.studio import Episodes
from story_channel.files import atomic_write,input_hash
from story_channel.episode_list_ui import episode_status
from test_studio import world


def prepared(world):
    episode = Episodes(world.manager,world.project_id).create("Approved episode","An outline",60)
    prompts = Prompts(prompts=[PromptItem(scene_id="scene_01",provider="demo",model="demo",
        image_prompt="A lighthouse",video_prompt="A slow pan",negative_prompt="No text")])
    file = world.manager.project_path(episode.project_id)/"prompts"/"prompts.json"
    atomic_write(file,prompts.model_dump_json(by_alias=True))
    episode.steps["prompts"] = StepState(status="succeeded")
    episode.content_approvals["prompts"] = ApprovalRecord(content_hash=input_hash(prompts.model_dump(mode="json",by_alias=True)))
    world.manager.save(episode)
    return episode,file


def test_episode_list_shows_ready_when_current_prompts_are_approved(world):
    episode,_ = prepared(world)
    assert not episode.asset_approvals
    assert episode_status(world.manager,episode) == "READY TO ANIMATE"
    def display(manager,world_id):
        from story_channel.episode_list_ui import episode_list
        from story_channel.studio import Episodes
        episodes = Episodes(manager,world_id).list()
        episode_list(world_id,episodes,episodes[0].project_id,manager=manager)
    app = AppTest.from_function(display,args=(world.manager,world.project_id),default_timeout=20).run()
    assert not app.exception
    assert list(app.dataframe[0].value["Story/Episode status"]) == ["READY TO ANIMATE"]


@pytest.mark.parametrize("change",["unapproved","stale","modified","missing"])
def test_unapproved_or_outdated_prompts_never_show_ready(world,change):
    episode,file = prepared(world)
    if change == "unapproved":
        episode.content_approvals.clear()
    elif change == "stale":
        episode.steps["prompts"].status = "stale"
    elif change == "modified":
        atomic_write(file,file.read_text(encoding="utf-8").replace("A lighthouse","A different location"))
    else:
        file.unlink()
    assert episode_status(world.manager,episode) != "READY TO ANIMATE"
