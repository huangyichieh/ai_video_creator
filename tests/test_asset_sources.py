import pytest

from story_channel.asset_sources import edit_source, read_source
from test_assets_ui import prepare


@pytest.mark.parametrize("stage,subject", [("backgrounds", "lighthouse"), ("scene_images", "scene_1"), ("voices", "scene_1_0000_0000")])
def test_edit_related_source_updates_only_target_and_rejects_stale_edits(tmp_path, stage, subject):
    manager, project = prepare(tmp_path)
    from story_channel.pipeline import Pipeline
    flow = Pipeline(manager, project.project_id)
    source = read_source(flow, stage, subject)
    baseline = source.model_dump_json(by_alias=True)
    if stage == "backgrounds":
        source.description = "A lighthouse beneath winter stars"
    elif stage == "scene_images":
        source.prompt.image_prompt = "Moonlight on the lighthouse, long shadows"
    else:
        source.scene.narration = "The keeper watches the moonrise."
    unrelated = flow.artifact("storyboard").scenes[1].model_copy(deep=True)
    edit_source(flow, stage, subject, source.model_dump_json(by_alias=True), expected=baseline)
    assert read_source(flow, stage, subject) == source
    assert flow.artifact("storyboard").scenes[1] == unrelated
    with pytest.raises(ValueError, match="changed in another editor"):
        edit_source(flow, stage, subject, baseline, expected=baseline)
