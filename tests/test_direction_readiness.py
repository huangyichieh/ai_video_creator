import pytest
from streamlit.testing.v1 import AppTest

from story_channel.animation import AnimationDirector
from story_channel.assets import AssetPipeline
from story_channel.animations_workflow import animation_snapshot
from story_channel.scene_production import SceneProduction
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from test_scene_production import episode
from test_studio import world


def test_saved_plan_unlocks_animation_without_three_approvals(episode):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    engine.generate("scene_images")
    director = AnimationDirector(engine.manager,engine.project_id)
    plan = director.prepare()
    assert plan.plan_approval is None and plan.performance_approval is None and not plan.scene_approvals
    assert director.require_ready().plan == plan.plan
    render = RenderPipeline(engine.manager,engine.project_id)
    action,rows = animation_snapshot(render)
    assert action.stage == "visual_scenes" and action.runnable
    assert not {"Direction approval","Performance approval","Scene direction review"} & {r["Stage"] for r in rows}
    service = SceneProduction(engine.manager,engine.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    assert service.direction_ready(sid) and not service.direction_approved(sid)
    options = RenderOptions(width=320,height=180,fps=12,music=False,sfx=False,subtitles="none")
    visual = service.generate_visual(sid,options)
    assert visual["status"] == "succeeded"
    assert not visual.get("approval")  # Generated video review remains separate.
    direction = service.direction(sid)
    direction.shots[0].action += " Finish with a deliberate pause."
    service.save_direction(sid,expected=service.direction_hash(sid),text=direction.model_dump_json(by_alias=True))
    assert service.direction_ready(sid)
    assert service.direction(sid).shots[0].action.endswith("Finish with a deliberate pause.")
    assert not service.visual_current(sid,options)
    assert not director.load().scene_approvals
    # Removing required direction still blocks generation.
    invalid = director.load().plan
    invalid.shots = []
    with pytest.raises(ValueError,match="cover exactly"):
        director.save_plan(invalid)


def display(manager,pid):
    from story_channel.animation_ui import animation_direction
    animation_direction(manager,pid)


def test_direction_ui_is_editable_and_has_no_approval_buttons(episode):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    engine.generate("scene_images")
    director = AnimationDirector(engine.manager,engine.project_id)
    director.prepare()
    app = AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    app.session_state[f"scene-direction-panel-{engine.project_id}-{sid}"] = True
    app.run()
    assert not app.exception
    labels = [b.label for b in app.button]
    assert not any("approve" in label.lower() for label in labels)
    editor = next(w for w in app.text_area if w.label == "Scene direction (editable)")
    assert not editor.disabled
    import json
    value = json.loads(editor.value)
    value["shots"][0]["action"] += " Slowly look away."
    editor.set_value(json.dumps(value)).run()
    app.button(key=f"scene-save-direction-{engine.project_id}-{sid}").click().run()
    from test_ui import finish_background
    finish_background(app)
    assert not app.exception
    assert SceneProduction(engine.manager,engine.project_id).direction(sid).shots[0].action.endswith("Slowly look away.")
    app.run()
    assert next(w for w in app.text_area if w.label == "Animation / performance / voice profile JSON").value == director.load().plan.model_dump_json(by_alias=True,indent=2)
