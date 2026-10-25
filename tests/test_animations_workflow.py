import json

import pytest

from story_channel.animation import AnimationDirector
from story_channel.animations_workflow import animation_snapshot, generate_animations
from story_channel.assets import AssetPipeline
from story_channel.identity import IdentityManager
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from test_assets_ui import prepare


def ready_assets(tmp_path):
    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    edited = library.model_copy(deep=True)
    for profile in edited.voices.values():
        profile.voice_id = "nova"
    identity.save(edited, expected=library.model_dump_json(by_alias=True))
    assets = AssetPipeline(manager, project.project_id)
    assets.generate("character_references")
    assets.approve("characters")
    assets.generate("backgrounds")
    assets.approve("backgrounds")
    identity.approve()
    director = AnimationDirector(manager, project.project_id)
    direction = director.prepare()
    for cid in direction.plan.voices:
        director.preview_voice(cid, "Keep the beacon burning. We will find our way through this storm together, one step at a time.")
        director.approve_voice(cid)
    return RenderPipeline(manager, project.project_id), director


def test_pipeline_stops_at_human_reviews_and_hands_approved_scenes_to_final(tmp_path):
    engine, director = ready_assets(tmp_path)
    assert animation_snapshot(engine)[0].stage == "scene_images"
    assert generate_animations(engine, pipeline=True).stage == "direction_review"
    assert director.load().plan_approval is None
    director.approve_plan()
    assert generate_animations(engine, pipeline=True).stage == "performance_review"
    director.approve_performance()
    assert generate_animations(engine).stage == "subtitles"
    assert not any(r.stage == "subtitles" and r.status == "succeeded" for r in engine.assets.load().records.values())
    assert generate_animations(engine, pipeline=True).stage == "input_review"
    assert "assets" not in engine.manager.load(engine.project_id).asset_approvals
    engine.assets.approve("assets")
    engine.configure(options=RenderOptions(width=320, height=180, fps=12, subtitles="none", music=False, sfx=False))
    from story_channel.finishing_models import FinishingPlan, EffectLayer
    sid = next(iter(engine.assets.load().scenes))
    engine.finishing.configure(FinishingPlan(effects=[EffectLayer(effect_id="rain_layer", scene_id=sid, kind="rain", duration=1)]),
                               expected=engine.finishing.load().plan.model_dump_json(by_alias=True))
    assert animation_snapshot(engine)[0].stage == "effects"
    assert generate_animations(engine, pipeline=True).stage == "scene_review"
    assert engine.finishing.load().records["vfx:rain_layer"].status == "succeeded"
    state = engine.load()
    assert state.final_file is None
    assert all(record.status == "succeeded" and not record.approved_hash for record in state.scenes.values())
    engine.approve_scenes(list(state.scenes))
    action, rows = animation_snapshot(engine)
    assert action.stage == "complete" and action.page == "Final" and not action.runnable
    assert all(row["Approval"] == "Approved" for row in rows if row["Stage"] == "Scene video")
    assert engine.load().final_file is None


def test_next_stage_stop_and_failure_leave_approvals_untouched(tmp_path, monkeypatch):
    engine, director = ready_assets(tmp_path)
    engine.stop_requested = engine.assets.stop_requested = lambda: True
    assert generate_animations(engine, pipeline=True).stage == "scene_images"
    assert not any(r.stage == "scene_images" for r in engine.assets.load().records.values())
    engine.stop_requested = engine.assets.stop_requested = lambda: False
    def fail(stage):
        raise RuntimeError("Provider unavailable")
    monkeypatch.setattr(engine.assets, "generate", fail)
    with pytest.raises(RuntimeError, match="Provider unavailable"):
        generate_animations(engine, pipeline=True)
    progress = json.loads((engine.path / "animation_workflow_progress.json").read_text())
    assert progress["status"] == "failed" and progress["stage"] == "scene_images"
    assert director.load().plan_approval is None


def test_ui_status_table_pipeline_buttons_and_review_navigation(tmp_path, monkeypatch):
    from test_identity_ui import open_assets
    from test_ui import finish_background
    engine, _ = ready_assets(tmp_path)
    app = open_assets(engine.manager, monkeypatch)
    app.button(key="nav-animations").click().run()
    pid = engine.project_id
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["1. Scene images", "2. Direction & performance", "3. Voices & input review", "4. Video & effects", "5. Scene review & continuity"]
    assert any(button.key == f"animation-input-images-{pid}" for button in app.tabs[0].button)
    assert not any(button.key == f"animation-input-images-{pid}" for button in app.tabs[2].button)
    status = next(frame.value for frame in app.dataframe if "Step" in frame.value.columns)
    assert list(status["Step"]) == sorted(status["Step"])
    next_button = app.button(key=f"animation-workflow-next-{pid}")
    assert "Scene images" in next_button.label and not next_button.disabled
    assert any("Direction approval" in list(frame.value["Stage"]) for frame in app.dataframe if "Stage" in frame.value.columns)
    next_button.click().run()
    finish_background(app)
    assert not app.exception
    assert app.button(key=f"animation-workflow-run-{pid}").disabled
    assert not app.button(key=f"animation-workflow-next-{pid}").disabled
    app.session_state[f"animation-direction-panel-{pid}"] = False
    app.run()
    app.button(key=f"animation-workflow-next-{pid}").click().run()
    assert app.session_state[f"page-tab-animations-{pid}"] == "2. Direction & performance"
    assert app.session_state[f"animation-direction-panel-{pid}"] is True
    assert app.session_state[f"animation-approval-navigation-{pid}"]["target"] == f"animation-direction-approval-{pid}"
    assert any("story-approval-highlight" in item.proto.body and "sessionStorage" in item.proto.body for item in app.get("html"))
    assert "workspace-task" not in app.session_state


    director = AnimationDirector(engine.manager, pid)
    director.approve_plan()
    app.run()
    assert app.button(key=f"animation-approve-plan-{pid}").label == "Approved"
    assert app.button(key=f"animation-approve-plan-{pid}").disabled
    app.button(key=f"animation-workflow-next-{pid}").click().run()
    assert app.session_state[f"animation-approval-navigation-{pid}"]["target"] == f"animation-performance-approval-{pid}"
    assert director.load().performance_approval is None
    director.approve_performance()
    generate_animations(engine, pipeline=True)
    app.run()
    assert app.button(key=f"animation-performance-approve-{pid}").label == "Approved"
    assert app.button(key=f"animation-performance-approve-{pid}").disabled
    app.button(key=f"animation-workflow-next-{pid}").click().run()
    assert app.session_state[f"page-tab-animations-{pid}"] == "3. Voices & input review"
    assert app.session_state[f"animation-approval-navigation-{pid}"]["target"] == f"animation-input-approval-{pid}"
    assert "assets" not in engine.manager.load(pid).asset_approvals
    engine.assets.approve("assets")
    engine.configure(options=RenderOptions(width=320, height=180, fps=12, subtitles="none", music=False, sfx=False))
    engine.generate_scenes()
    sid = next(iter(engine.load().scenes))
    app.run()
    app.button(key=f"animation-workflow-next-{pid}").click().run()
    assert not app.exception
    assert app.session_state[f"page-tab-animations-{pid}"] == "5. Scene review & continuity"
    assert app.session_state[f"animation-scene-panel-{pid}-{sid}"] is True
    assert app.session_state[f"animation-approval-navigation-{pid}"]["target"] == f"animation-scene-approval-{pid}-{sid}"
    assert not engine.load().scenes[sid].approved_hash
    assert any(button.key == f"approve-scene-{sid}" for button in app.tabs[4].button)
    assert not any(button.key == f"approve-scene-{sid}" for button in app.tabs[3].button)
    total = len(engine.load().scenes)
    assert app.button(key=f"animation-workflow-next-{pid}").label == f"Next stage: Approve generated scenes 0/{total}"
    assert app.button(key=f"animation-workflow-review-{pid}").label == "Approve all scenes"
    record = engine.load().scenes[sid]
    app.checkbox(key=f"render-accept-scene-{pid}-{sid}-{record.file_hash}").check().run()
    app.button(key=f"approve-scene-{sid}").click().run()
    finish_background(app)
    assert not app.exception
    assert app.button(key=f"approve-scene-{sid}").label == "Approved"
    assert app.button(key=f"approve-scene-{sid}").disabled
    assert f"1/{total}" in app.button(key=f"animation-workflow-next-{pid}").label
    app.button(key=f"animation-workflow-review-{pid}").click().run()
    finish_background(app)
    assert not app.exception
    assert all(app.button(key=f"approve-scene-{scene_id}").disabled for scene_id in engine.load().scenes)
    assert animation_snapshot(engine)[0].stage == "complete"
    assert engine.load().final_file is None
    # A new scene revision requires fresh review while other approvals survive.
    engine.generate_scenes(subject=sid, force=True)
    app.run()
    assert app.button(key=f"approve-scene-{sid}").label == "Approve scene"
    assert f"{total - 1}/{total}" in app.button(key=f"animation-workflow-next-{pid}").label
    assert animation_snapshot(engine)[0].stage == "scene_review"


def test_early_project_has_blocked_status_table_and_assets_navigation(tmp_path, monkeypatch):
    from test_workspace_state import workspace
    manager, project, app = workspace(tmp_path, monkeypatch)
    engine = RenderPipeline(manager, project.project_id)
    action, rows = animation_snapshot(engine)
    assert action.stage == "assets" and not action.runnable
    assert {row["Stage"] for row in rows} >= {"Scene video", "Video shots / chaining", "Performance approval", "Scene approval", "Continuity approval"}
    app.button(key="nav-animations").click().run()
    assert not app.exception
    assert app.button(key=f"animation-workflow-run-{project.project_id}").disabled
    app.button(key=f"animation-workflow-review-{project.project_id}").click().run()
    assert not app.exception and app.session_state["workspace_page"] == "Assets"
