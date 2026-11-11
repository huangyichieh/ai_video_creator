import pytest

from story_channel.files import input_hash
from story_channel.episode_list_ui import episode_status
from story_channel.storyboard_editor import save
from story_channel.studio import approved_content
from test_scene_production import episode
from test_studio import world


def edit_board(pipeline):
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    data = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    data["scenes"][0]["visualDescription"] = "A new close view of the keeper by the window"
    save(pipeline,data,source)


def test_storyboard_reapproval_prepares_current_prompts_without_llm_call(episode,monkeypatch):
    _,project,pipeline = episode
    edit_board(pipeline)
    assert not approved_content(pipeline,"prompts")
    before = pipeline.manager.load(project.project_id).steps["prompts"].revision
    monkeypatch.setattr(pipeline,"_generate",lambda *a,**k:pytest.fail("Automatic prompt preparation must be local"))
    from story_channel.review_changes import approve_storyboard_unchanged,storyboard_approval_snapshot
    approve_storyboard_unchanged(pipeline,expected=storyboard_approval_snapshot(pipeline))
    manifest = pipeline.manager.load(project.project_id)
    assert manifest.steps["storyboard"].status == "succeeded"
    assert manifest.steps["prompts"].status == "succeeded"
    assert manifest.steps["prompts"].revision == before+1
    assert approved_content(pipeline,"prompts")
    assert episode_status(pipeline.manager,manifest) == "READY TO ANIMATE"
    prompts = pipeline.artifact("prompts")
    assert "new close view" in prompts.prompts[0].image_prompt
    assert manifest.content_approvals["prompts"].content_hash == input_hash(prompts.model_dump(mode="json",by_alias=True))
    pipeline.inspect()
    pipeline.approve("storyboard")
    assert pipeline.manager.load(project.project_id).steps["prompts"].revision == before+1


def test_existing_approved_episode_gets_missing_internal_prompts_on_inspection(episode):
    _,project,pipeline = episode
    manifest = pipeline.manager.load(project.project_id)
    manifest.steps.pop("prompts",None)
    manifest.content_approvals.pop("prompts",None)
    pipeline.manager.save(manifest)
    manifest = pipeline.inspect()
    assert approved_content(pipeline,"prompts")
    assert episode_status(pipeline.manager,manifest) == "READY TO ANIMATE"


def test_preparation_failure_keeps_storyboard_approval_and_can_retry(episode,monkeypatch):
    _,project,pipeline = episode
    edit_board(pipeline)
    from story_channel.modules import VisualPromptAdapter
    original = VisualPromptAdapter.generate
    calls = []
    def broken(*args,**kwargs):
        calls.append(True)
        raise ValueError("Local prompt compilation failed")
    monkeypatch.setattr(VisualPromptAdapter,"generate",broken)
    with pytest.raises(ValueError,match="compilation failed"):
        pipeline.approve("storyboard")
    manifest = pipeline.inspect()
    assert "storyboard" in manifest.approvals
    assert manifest.steps["prompts"].status == "failed"
    assert not approved_content(pipeline,"prompts")
    assert episode_status(pipeline.manager,manifest) != "READY TO ANIMATE"
    pipeline.inspect()
    assert len(calls) == 1  # No repeated retries or refresh loop while browsing.
    monkeypatch.setattr(VisualPromptAdapter,"generate",original)
    manifest = pipeline.prepare_animation_prompts()
    assert episode_status(pipeline.manager,manifest) == "READY TO ANIMATE"
