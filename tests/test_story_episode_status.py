from story_channel.models import ProjectStatus
from story_channel.studio import Episodes,approve_content,content_digest
from story_channel.pipeline import Pipeline
from story_channel.episode_list_ui import episode_status
from test_studio import world


def test_story_status_follows_content_workflow_and_never_uses_animation_status(world):
    world.generate_characters()
    manager = world.manager
    project = Episodes(manager,world.project_id).create("Story progress","A memory returns",12)
    pipeline = Pipeline(manager,project.project_id)
    def status():
        return episode_status(manager,manager.load(project.project_id))
    assert status() == "OUTLINE · AWAITING APPROVAL"
    approve_content(pipeline,"outline",expected=content_digest(pipeline,"outline"))
    assert status() == "STORY · NOT STARTED"
    pipeline.generate("story")
    manifest = manager.load(project.project_id)
    manifest.status = ProjectStatus.RENDERED
    manager.save(manifest)
    assert status() == "STORY · AWAITING APPROVAL"
    pipeline.generate("review")
    pipeline.approve("story")
    assert status() == "SCRIPT · NOT STARTED"
    pipeline.generate("script")
    assert status() == "SCRIPT · AWAITING APPROVAL"
    approve_content(pipeline,"script",expected=content_digest(pipeline,"script"))
    assert status() == "STORYBOARD · NOT STARTED"
    pipeline.generate("storyboard")
    assert status() == "STORYBOARD · AWAITING APPROVAL"
    pipeline.approve("storyboard")
    assert status() == "READY TO ANIMATE"
