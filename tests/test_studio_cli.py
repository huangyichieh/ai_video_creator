import json
import pytest

from story_channel.cli import main
from story_channel.config import Settings
from story_channel.logging_config import configure_logging
from story_channel.projects import ProjectManager
from story_channel.pipeline import Pipeline, ApprovalRequired
from story_channel.studio import Episodes
from story_channel.studio_cli import duration
from test_studio import world


def test_cli_world_first_episode_gates_and_separate_final(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    source = tmp_path / "settings.md"
    source.write_text("An island where memories power lighthouses.", encoding="utf-8")
    common = ["--data-root", str(tmp_path / "channel")]
    def call(*args):
        assert main(common + list(args)) == 0
        return json.loads(capsys.readouterr().out)
    try:
        manifest = call("create", str(source))
        wid = manifest["projectId"]
        assert manifest["workflowVersion"] == "world_episode"
        call("world", wid, "review", "--demo")
        call("world", wid, "approve", "--decision", "revert", "--demo")
        call("world", wid, "characters", "--demo")
        outline = tmp_path / "outline.md"
        outline.write_text("The keeper rescues a forgotten visitor.", encoding="utf-8")
        episode = call("episode", wid, "create", "--title", "First episode", "--file", str(outline), "--duration", "00:02:15")
        eid = episode["projectId"]
        assert episode["targetDurationSec"] == 135
        assert call("episode", wid, "list")[0]["projectId"] == eid
        assert not call("run", eid)["steps"].get("story")
        call("approve", eid, "outline")
        assert call("run", eid)["steps"]["review"]["status"] == "succeeded"
        call("approve", eid, "story")
        state = call("resume", eid)
        assert state["steps"]["script"]["status"] == "succeeded"
        assert not state["steps"].get("storyboard")
        call("approve", eid, "script")
        assert call("resume", eid)["steps"]["storyboard"]["status"] == "succeeded"
        call("approve", eid, "storyboard")
        assert call("resume", eid)["steps"]["prompts"]["status"] == "succeeded"
        call("approve", eid, "prompts")
        from story_channel.render_models import RenderState
        calls = []
        monkeypatch.setattr("story_channel.rendering.RenderPipeline.run", lambda self, **kwargs: calls.append(kwargs) or RenderState())
        call("render", eid, "run")
        assert calls == [{"subject": None, "force": False, "assemble": False}]
    finally:
        configure_logging()


def test_current_world_cannot_generate_a_standalone_story(world):
    world.generate_characters()
    with pytest.raises(ApprovalRequired, match="episode"):
        world.pipeline.generate("story")
    result = world.pipeline.run()
    assert not result.steps.get("story")


@pytest.mark.parametrize("text,expected", [("01:02:03", 3723), ("150", 150), ("00:00:01", 1)])
def test_cli_duration(text, expected):
    assert duration(text) == expected


@pytest.mark.parametrize("text", ["00:60:00", "-1:00:01", "00:00:00", "bad", "1:30"])
def test_cli_rejects_invalid_duration(text):
    from argparse import ArgumentTypeError
    with pytest.raises(ArgumentTypeError):
        duration(text)
