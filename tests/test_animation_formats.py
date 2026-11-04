import json

import pytest

from story_channel.animation_formats import LANDSCAPE,PORTRAIT,composition_guidance
from story_channel.render_models import RenderOptions
from story_channel.rendering import RenderPipeline
from story_channel.scene_production import SceneProduction
from story_channel.animation_queue import AnimationQueue
from story_channel.animations_workflow import animation_snapshot
from story_channel.media_utils import file_hash
from test_scene_production import episode
from test_studio import world


def prepare(episode,formats):
    world,project,_ = episode
    engine = RenderPipeline(world.manager,project.project_id)
    options = RenderOptions(width=320,height=180,fps=12,motion="static",delivery_formats=formats)
    engine.configure(options=options.model_dump())
    engine.assets.generate("scene_images")
    service = SceneProduction(world.manager,project.project_id)
    state = service.director.prepare()
    for scene in service.assets.creative.artifact("storyboard").scenes:
        service.approve_direction(scene.scene_id,expected=service.direction_hash(scene.scene_id))
    service.director.approve_plan()
    service.director.approve_performance()
    sid = state.plan.shots[0].scene_id
    return engine,service,sid,options


def dimensions(path):
    from story_channel.ffmpeg_renderer import FFmpeg
    import re
    result = FFmpeg().run(["-i",path,"-map","0:v:0","-frames:v","1","-f","null","-"])
    match = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b",result.stderr)
    assert match,result.stderr
    return dict(width=int(match[1]),height=int(match[2]))


def test_format_options_normalize_and_legacy_sizes_remain_supported():
    both = RenderOptions(delivery_formats=[PORTRAIT,LANDSCAPE,PORTRAIT])
    assert both.delivery_formats == [LANDSCAPE,PORTRAIT] and both.portrait_safe_area
    assert "portraitSafeArea" in both.model_dump(by_alias=True)
    assert "central 31.6%" in composition_guidance(both)
    portrait = RenderOptions(delivery_formats=[PORTRAIT])
    assert portrait.video_size == "720x1280" and (portrait.width,portrait.height) == (720,1280)
    assert RenderOptions(width=320,height=180).width == 320


def test_both_formats_keep_landscape_then_portrait_and_require_both_before_approval(episode,monkeypatch):
    engine,service,sid,options = prepare(episode,[LANDSCAPE,PORTRAIT])
    task = next(t for t in engine.assets.tasks(engine.assets.load()) if t.stage == "scene_images" and t.subject == sid)
    assert "portrait-safe center region" in task.prompt
    action,_ = animation_snapshot(engine)
    queue = AnimationQueue(engine)
    queue.start(action,pipeline=True)
    queue.stage_started("visual_scenes")
    assert any("Landscape" in row["Subject"] for row in queue.rows())
    assert any("Portrait" in row["Subject"] for row in queue.rows())
    import story_channel.animation_formats as formats
    crop = formats.create_portrait
    def check_order(*args):
        rows = [row for row in queue.rows() if row["Subject"].startswith(sid+" · ")]
        assert rows[0]["Status"] == "Completed"
        assert rows[1]["Status"] == "Generating…"
        return crop(*args)
    monkeypatch.setattr(formats,"create_portrait",check_order)
    record = service.generate_visual(sid,options)
    assert list(record["formats"]) == [LANDSCAPE,PORTRAIT]
    paths = [service.assets.local_path(record["formats"][name]["asset"]["filePath"]) for name in (LANDSCAPE,PORTRAIT)]
    assert dimensions(paths[0]) == {"width":320,"height":180}
    assert dimensions(paths[1]) == {"width":180,"height":320}
    assert service.visual_current(sid,options)
    service.approve_visual(sid,options,expected=service.visual_hash(sid,options))
    assert service.visual_current(sid,options,approved=True)
    paths[1].unlink()
    assert not service.visual_current(sid,options,approved=True)


def test_portrait_failure_preserves_landscape_and_retry_does_not_regenerate(episode,monkeypatch):
    import story_channel.animation_formats as formats
    engine,service,sid,options = prepare(episode,[LANDSCAPE,PORTRAIT])
    original = formats.create_portrait
    def fail(*args):
        raise RuntimeError("portrait processing failed")
    monkeypatch.setattr(formats,"create_portrait",fail)
    with pytest.raises(RuntimeError,match="portrait processing"):
        service.generate_visual(sid,options)
    saved = service.visuals()[sid]["formats"][LANDSCAPE]
    assert file_hash(service.assets.local_path(saved["asset"]["filePath"])) == saved["fileHash"]
    monkeypatch.setattr(formats,"create_portrait",original)
    def forbidden(*args,**kwargs):
        pytest.fail("Completed landscape must be reused")
    monkeypatch.setattr("story_channel.video_providers.LocalVideoProvider.generate_clip",forbidden)
    service.generate_visual(sid,options)
    assert service.visual_current(sid,options)


def test_portrait_only_generates_native_portrait(episode):
    engine,service,sid,options = prepare(episode,[PORTRAIT])
    record = service.generate_visual(sid,options)
    assert list(record["formats"]) == [PORTRAIT]
    path = service.assets.local_path(record["asset"]["filePath"])
    assert dimensions(path) == {"width":180,"height":320}
    assert service.visual_current(sid,options)


def test_delivery_settings_ui_saves_combined_flags(episode):
    from streamlit.testing.v1 import AppTest
    world,project,_ = episode
    engine = RenderPipeline(world.manager,project.project_id)
    def display(engine):
        from story_channel.animation_formats_ui import settings
        settings(engine)
    app = AppTest.from_function(display,args=(engine,)).run()
    assert not app.exception
    assert app.selectbox[0].options == ["Portrait and landscape","Portrait only","Landscape"]
    app.selectbox[0].select("Portrait and landscape").run()
    app.button(key="save-animation-delivery-"+project.project_id).click().run()
    assert not app.exception
    options = engine.load().options
    assert options.delivery_formats == [LANDSCAPE,PORTRAIT] and options.portrait_safe_area
