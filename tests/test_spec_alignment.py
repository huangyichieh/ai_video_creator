import json
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.animation import AnimationDirector, require_voice
from story_channel.animation_models import Performance
from story_channel.cli import main
from story_channel.costs import CostTracker
from story_channel.dialogue_review import DialogueIssue, DialogueReport, review_dialogue
from story_channel.identity import IdentityManager, Prop
from story_channel.pipeline import ApprovalRequired
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from test_assets import assets
from test_character_assets import cast


def add_script_dialogue(pipeline):
    from story_channel.models import Dialogue
    script = pipeline.artifact("script")
    script.scenes[0].dialogue = [Dialogue(character_id="keeper", text="Keep the beacon burning.")]
    pipeline.edit("script", script.model_dump_json(by_alias=True))
    pipeline.generate("storyboard")
    pipeline.approve("storyboard")
    pipeline.generate("prompts")
    return script.scenes[0]


def test_identity_library_before_storyboard_and_optimistic_save(cast):
    manager, project, flow = cast
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    assert not (identity.path / "scenes.json").exists()
    assert set(library.voices) == {c.character_id for c in flow.artifact("characters").characters}
    assert all(not v.voice_id for v in library.voices.values())
    expected = library.model_dump_json(by_alias=True)
    cid = next(iter(library.voices))
    library.voices[cid].voice_id = "nova"
    library.props["lantern"] = Prop(prop_id="lantern", description="Brass lantern", owner_id=cid)
    saved = identity.save(library, expected=expected)
    assert saved.approval is None
    assert (identity.path / "prop_registry.json").exists()
    with pytest.raises(ValueError, match="changed"):
        identity.save(library, expected=expected)
    with pytest.raises(ApprovalRequired, match="explicit Voice ID|preview"):
        require_voice(manager, project.project_id, cid)
    invalid = saved.model_copy(deep=True)
    invalid.props["lantern"].owner_id = "missing"
    with pytest.raises(ValueError, match="valid owner"):
        identity.save(invalid, expected=saved.model_dump_json(by_alias=True))
    costume_id = next(iter(saved.costumes))
    edited = saved.model_copy(deep=True)
    edited.costumes[costume_id].description = "Red weatherproof coat"
    identity.save(edited, expected=saved.model_dump_json(by_alias=True))
    rebuilt = identity.prepare()
    assert rebuilt.costumes[costume_id].description == "Red weatherproof coat"
    assert list((identity.path / "history" / "identity").glob("*.json"))


def test_identity_is_reused_by_animation_and_approval_expires(assets):
    identity = IdentityManager(assets.manager, assets.project_id)
    library = identity.prepare()
    expected = library.model_dump_json(by_alias=True)
    for profile in library.voices.values():
        profile.voice_id = "nova"
    identity.save(library, expected=expected)
    director = AnimationDirector(assets.manager, assets.project_id)
    state = director.prepare()
    assert state.plan.voices == library.voices
    assert all(shot.costume_ids for shot in state.plan.shots)
    for cid in state.plan.voices:
        director.preview_voice(cid, "Keep the beacon burning. We will find a way through this storm together, one step at a time.")
        director.approve_voice(cid)
    director.approve_performance()
    assert assets.run().next_stage == "character_review"
    assets.approve("characters")
    assert assets.run().next_stage == "background_review"
    assets.approve("backgrounds")
    identity.approve()
    identity.require_approved()
    assert assets.run().next_stage == "asset_review"
    assets.approve("assets")
    current = identity.load()
    expected = current.model_dump_json(by_alias=True)
    current.style = "Watercolor"
    identity.save(current, expected=expected)
    with pytest.raises((ApprovalRequired, ValueError)):
        identity.require_approved()
    with pytest.raises(ValueError, match="rebuild"):
        director.validate_plan(director.load().plan)


def test_dialogue_review_is_independent_cached_and_invalidates_gate(assets):
    pipeline = assets.creative
    scene = add_script_dialogue(pipeline)
    pipeline.settings.input_cost_per_million = Decimal("1")
    pipeline.settings.output_cost_per_million = Decimal("2")
    class Reviewer:
        calls = 0
        last_usage = {"input_tokens": 100, "output_tokens": 20}
        def generate(self, prompt, response_model, config):
            self.calls += 1
            assert "exposition" in prompt and "subtext" in prompt
            return DialogueReport(summary="A line needs revision", recommendation="revise", issues=[
                DialogueIssue(scene_id=scene.scene_id, line_index=0, category="exposition", severity="error",
                              description="Exposition dump", suggestion="Give this character a concrete goal")])
    reviewer = Reviewer()
    original = pipeline.artifact("script").model_dump_json()
    review_dialogue(pipeline, provider=reviewer)
    review_dialogue(pipeline, provider=reviewer)
    assert reviewer.calls == 1
    assert pipeline.artifact("script").model_dump_json() == original
    assert "storyboard" not in assets.manager.load(assets.project_id).approvals
    with pytest.raises(ApprovalRequired, match=r"AI Review \(Storyboard\)"):
        pipeline.approve("storyboard")
    assert CostTracker(assets.manager, assets.project_id).records()[-1].step == "dialogue_review"
    assert assets.manager.load(assets.project_id).actual_cost == Decimal(".00014")


def test_dialogue_failed_attempt_records_usage(assets):
    class BrokenReviewer:
        last_usage = {"input_tokens": 10, "output_tokens": 0}
        def generate(self, *args):
            raise RuntimeError("injected failure")
    with pytest.raises(RuntimeError, match="injected"):
        review_dialogue(assets.creative, provider=BrokenReviewer())
    usage = CostTracker(assets.manager, assets.project_id).records()[-1]
    assert usage.step == "dialogue_review" and usage.outcome == "failed"
    assert usage.input_tokens == 10


def test_performance_accepts_spec_milliseconds_and_legacy_seconds():
    line = Performance(sceneId="scene_01", lineIndex=0, characterId="keeper", text="Wait.",
                       emotionIntensity=.8, pauseBeforeMs=300, pauseAfterMs=500, volume=.7,
                       bodyAction="hand over lantern", eyeDirection="toward sailor", cameraReaction="close up", sfxCue="cloth")
    assert line.intensity == .8 and line.pause_before == .3 and line.pause_after == .5
    assert Performance.model_validate_json(line.model_dump_json(by_alias=True)) == line
    with pytest.raises(ValueError, match="seconds or milliseconds"):
        Performance(scene_id="scene", line_index=0, character_id="keeper", text="Wait", pauseBeforeMs=300, pause_before=.2)


def test_full_schema_export_and_dialogue_intent(assets, tmp_path, capsys):
    add_script_dialogue(assets.creative)
    folder = tmp_path / "schemas"
    assert main(["schemas", str(folder), "--production"]) == 0
    for name in ("manifest", "performance", "identity", "animation", "dialogue_review", "render"):
        assert json.loads((folder / f"{name}.schema.json").read_text(encoding="utf-8"))["type"] == "object"
    intent = json.loads((assets.path / "performance_intent.json").read_text(encoding="utf-8"))
    assert intent["status"] == "draft" and intent["performances"]
    assert "bodyAction" in intent["performances"][0]


def test_identity_ui_draft_save_revert_and_navigation(cast):
    manager, project, _ = cast
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    app.button(key=f"identity-build-{project.project_id}").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=20)
        app.run()
    assert not app.exception
    key = f"identity-draft-{project.project_id}"
    baseline = app.text_area(key=key).value
    app.text_area(key=key).set_value(baseline + "\n ").run()
    app.button(key="nav-story-settings").click().run()
    app.button(key="nav-assets").click().run()
    assert app.text_area(key=key).value == baseline + "\n "
    app.button(key=f"identity-revert-{project.project_id}").click().run()
    assert app.text_area(key=key).value == baseline
    data = json.loads(baseline)
    data["style"] = "Hand-drawn animation"
    app.text_area(key=key).set_value(json.dumps(data)).run()
    app.button(key=f"identity-save-{project.project_id}").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=20)
        app.run()
    assert not app.exception
    assert IdentityManager(manager, project.project_id).load().style == "Hand-drawn animation"
