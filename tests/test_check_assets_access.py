import pytest
from story_channel.storyboard_asset_check import available
from test_animation_episode_list import open_app
from test_scene_production import episode
from test_studio import world


def test_approved_storyboard_opens_check_assets_without_generated_prompts(episode,monkeypatch):
    world,project,pipeline = episode
    manifest = world.manager.load(project.project_id)
    manifest.steps["prompts"].status = "stale"
    manifest.content_approvals.pop("prompts",None)
    world.manager.save(manifest)
    (pipeline.path/"prompts"/"prompts.json").unlink()
    assert available(world.manager,project.project_id)
    app = open_app(world,monkeypatch,project.project_id)
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Check assets"]
    assert not app.button(key="check-assets-refresh-"+project.project_id).disabled
    assert not any(button.key == "animation-workflow-run-"+project.project_id for button in app.button)
    assert any("unlock the remaining animation stages" in message.value for message in app.info)


@pytest.mark.parametrize("reason",["unapproved","stale","changed_file","bad_receipt"])
def test_check_assets_requires_current_approved_storyboard(episode,monkeypatch,reason):
    world,project,pipeline = episode
    assert available(world.manager,project.project_id)
    manifest = world.manager.load(project.project_id)
    if reason == "unapproved":
        manifest.approvals.pop("storyboard",None)
    elif reason == "stale":
        manifest.steps["storyboard"].status = "stale"
    elif reason == "changed_file":
        (pipeline.path/"scenes.json").write_text('{"incomplete":',encoding="utf-8")
    else:
        manifest.approvals["storyboard"].content_hash = "0"*64
    world.manager.save(manifest)
    assert not available(world.manager,project.project_id)
    app = open_app(world,monkeypatch,project.project_id)
    assert not app.exception
    assert not any(tab.label == "Check assets" for tab in app.tabs)
    assert any("Approve this episode's Storyboard" in message.value for message in app.info)
