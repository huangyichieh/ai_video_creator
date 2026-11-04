from pathlib import Path
from streamlit.testing.v1 import AppTest
from threading import Event

from story_channel.assets import AssetPipeline
from story_channel.render_models import RenderOptions
from story_channel.rendering import RenderPipeline
from test_assets import complete
from test_assets_ui import prepare, widget


def test_final_page_render_review_approve_and_navigation(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    complete(AssetPipeline(manager, project.project_id))
    renderer = RenderPipeline(manager, project.project_id)
    renderer.configure(options=RenderOptions(width=320, height=180, fps=12))
    monkeypatch.setenv('STORY_DATA_ROOT', str(manager.root))
    monkeypatch.setenv('STORY_LLM_PROVIDER', 'demo')
    monkeypatch.setenv('STORY_ASSET_PROVIDER', 'demo')
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key='nav-animations').click().run()
    assert not app.exception
    widget(app.text_input, 'Subtitle font').set_value('Arial').run()
    app.button(key='nav-story-settings').click().run()
    app.button(key='nav-animations').click().run()
    assert widget(app.text_input, 'Subtitle font').value == 'Arial'
    widget(app.button, 'Save video settings').click().run()
    assert renderer.load().options.font == 'Arial'
    app.button(key='generate-animation-scenes').click().run()
    if 'workspace-task' in app.session_state:
        task = app.session_state['workspace-task']
        app.button(key='nav-story-settings').click().run()
        assert not app.exception
        task['future'].result(timeout=30)
        app.run()
        app.button(key='nav-animations').click().run()
    assert not app.exception
    assert renderer.load().status == 'continuity_review'
    assert not renderer.load().final_file
    renderer.approve_scenes(list(renderer.load().scenes))
    app.button(key='nav-final').click().run()
    assert not any(b.key == 'generate-animation-scenes' for b in app.button)
    started, release = Event(), Event()
    original_assemble = RenderPipeline.assemble_final
    def slow_assemble(engine):
        engine.assembly_progress(42, "Assembling video and audio")
        started.set()
        if not release.wait(20):
            raise RuntimeError("Test assembly was not released")
        return original_assemble(engine)
    with monkeypatch.context() as patch:
        patch.setattr(RenderPipeline, 'assemble_final', slow_assemble)
        try:
            app.button(key='render-final-video').click().run()
            assert started.wait(5)
            app.run()
            assert not app.exception
            assert app.button(key='render-final-video').disabled
            assert app.button(key='render-final-video').label == 'Assembling final video…'
            assert any('42%' in progress.proto.text for progress in app.get('progress'))
            assert any(status.label == 'Assembling final video…' for status in app.status)
        finally:
            release.set()
    if 'workspace-task' in app.session_state:
        app.session_state['workspace-task']['future'].result(timeout=30)
        app.run()
    assert any('100%' in progress.proto.text for progress in app.get('progress'))
    assert renderer.load().status == 'review'
    assert app.button(key='approve-final-video').disabled
    widget(app.checkbox, 'I reviewed visuals, voices, subtitles, music and timing').check().run()
    app.button(key='approve-final-video').click().run()
    assert not app.exception
    assert renderer.load().status == 'approved'
    assert manager.load(project.project_id).final_approval is not None
