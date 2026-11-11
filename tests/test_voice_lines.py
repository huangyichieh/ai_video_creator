import pytest
from streamlit.testing.v1 import AppTest

from story_channel import voice_lines
from story_channel.media_providers import DemoTTSProvider
from story_channel.subtitles_ui import subtitle_progress
from story_channel.modules import MODULES
from test_animations_workflow import ready_assets


@pytest.fixture
def speech(tmp_path):
    renderer,_=ready_assets(tmp_path)
    engine=renderer.assets
    engine.generate('scene_images')
    return engine


def test_saved_character_and_line_feed_speech_and_subtitles_without_changing_visual_sources(speech):
    engine=speech
    state=engine.inspect()
    task=next(t for t in engine.tasks(state) if t.stage=='voices')
    cast=engine.creative.artifact('characters').characters
    selected=next(c for c in cast if c.character_id!=task.speaker)
    storyboard_path=engine.path/MODULES['storyboard'].filename
    storyboard=storyboard_path.read_bytes()
    direction=(engine.path/'animation.json').read_bytes()
    before={t.key:engine._hash(t,state) for t in engine.tasks(state) if t.stage=='voices'}
    saved=voice_lines.save(engine,task.subject,selected.character_id,'A newly edited spoken line.',expected=voice_lines.signature(task))
    updated=voice_lines.track(engine,saved,task.subject)
    assert updated.speaker==selected.character_id and updated.voice_id==selected.voice_id
    assert updated.prompt=='A newly edited spoken line.'
    assert storyboard_path.read_bytes()==storyboard
    assert (engine.path/'animation.json').read_bytes()==direction
    assert all(engine._hash(t,saved)==before[t.key] for t in engine.tasks(saved) if t.stage=='voices' and t.subject!=task.subject)
    config=engine._config(updated,saved)
    assert config.options['voice']==saved.voice_map[selected.voice_id]
    assert config.options['performance']['text']==updated.prompt
    assert config.options['performance']['character_id']==selected.character_id
    class Capture(DemoTTSProvider):
        def generate(self,text,voice,config):
            assert text==updated.prompt and voice==saved.voice_map[selected.voice_id]
            return super().generate(text,voice,config)
    engine.tts_provider=Capture()
    result=engine.generate('voices',subject=task.subject)
    assert result.records['voices:'+task.subject].status=='succeeded'
    subtitle=next(t for t in engine.tasks(result) if t.stage=='subtitles' and t.subject==task.subject)
    assert subtitle.prompt==updated.prompt and subtitle.speaker==updated.speaker
    rows,_=subtitle_progress(engine,result)
    assert next(row for row in rows if row['Track']==task.subject)['Speaker']==updated.speaker
    with pytest.raises(ValueError,match='another editor'):
        voice_lines.save(engine,task.subject,task.speaker,'Overwrite',expected=voice_lines.signature(task))


def test_invalid_voice_line_does_not_change_saved_data(speech):
    engine=speech
    task=next(t for t in engine.tasks(engine.inspect()) if t.stage=='voices')
    before=engine.state_path.read_bytes()
    for character,text in [('missing_character','Hello'),(task.speaker,' '),(task.speaker,'x'*3001)]:
        with pytest.raises(ValueError):
            voice_lines.save(engine,task.subject,character,text,expected=voice_lines.signature(task))
    assert engine.state_path.read_bytes()==before


def display(manager,pid,subject):
    from story_channel.assets import AssetPipeline
    from story_channel.voice_lines_ui import editor
    engine=AssetPipeline(manager,pid)
    editor(engine,engine.load(),subject)


def test_character_lines_form_saves_and_resets_unsaved_edits(speech):
    engine=speech
    task=next(t for t in engine.tasks(engine.inspect()) if t.stage=='voices')
    app=AppTest.from_function(display,args=(engine.manager,engine.project_id,task.subject),default_timeout=30).run()
    assert not app.exception
    assert app.selectbox[0].label=='Character' and app.text_input[0].label=='Line'
    app.text_input[0].set_value('Discard this draft')
    next(b for b in app.button if b.label=='Reset').click().run()
    assert not app.exception and app.text_input[0].value==task.prompt
    selected=next(c.character_id for c in engine.creative.artifact('characters').characters if c.character_id!=task.speaker)
    app.selectbox[0].select(selected)
    app.text_input[0].set_value('Save this spoken text')
    next(b for b in app.button if b.label=='Save').click().run()
    assert not app.exception
    stored=voice_lines.track(engine,engine.load(),task.subject)
    assert stored.speaker==selected and stored.prompt=='Save this spoken text'
    app.text_input[0].set_value('Another draft')
    next(b for b in app.button if b.label=='Reset').click().run()
    assert not app.exception and app.text_input[0].value=='Save this spoken text'
