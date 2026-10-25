from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from test_assets import assets, complete
from test_animation import prepare_direction


def test_performance_and_shot_cues_reach_separate_ambience_mix(assets):
    director = prepare_direction(assets)
    plan = director.load().plan
    scene = plan.shots[0].scene_id
    plan.shots[0].ambience_cues = ["rain_window"]
    plan.shots[0].sfx_cues = ["footsteps"]
    next(line for line in plan.performances if line.scene_id == scene).sfx_cue = "cloth_movement"
    director.save_plan(plan)
    director.approve_performance()
    complete(assets)
    renderer = RenderPipeline(assets.manager, assets.project_id)
    state = renderer.configure(options=RenderOptions(width=320, height=180, fps=12))
    sound = state.music_plan.scenes[scene]
    assert {"footsteps", "cloth_movement"} <= {cue.name for cue in sound.cues}
    assert [cue.name for cue in sound.ambience] == ["rain_window"]
    assert sound.ambience[0].loop
    result = renderer.run()
    assert result.status == "review"
    assert renderer.ffmpeg.validate(assets.local_path(result.final_file)) > 0
    # Updating only ambience still refreshes the default plan; custom plans remain authoritative.
    plan.shots[0].ambience_cues = ["wind"]
    director.save_plan(plan)
    director.approve_performance()
    changed = renderer.inspect()
    assert [cue.name for cue in changed.music_plan.scenes[scene].ambience] == ["wind"]
    renderer.configure(music_plan=changed.music_plan)
    plan.shots[0].ambience_cues = ["ocean"]
    director.save_plan(plan)
    director.approve_performance()
    assert renderer.inspect().music_plan.scenes[scene].ambience[0].name == "wind"
