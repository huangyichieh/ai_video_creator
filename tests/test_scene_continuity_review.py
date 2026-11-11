import base64
from io import BytesIO
from threading import Event

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel import scene_continuity_review as review
from story_channel.costs import CostTracker
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from story_channel.scene_production import SceneProduction
from test_animated_scene_cards_ui import prepare
from test_scene_production import episode
from test_studio import world


@pytest.fixture
def videos(episode):
    assets,scenes=prepare(episode)
    renderer=RenderPipeline(assets.manager,assets.project_id)
    renderer.configure(options=RenderOptions(width=320,height=180,fps=12,music=False,sfx=False,subtitles='none'))
    service=SceneProduction(assets.manager,assets.project_id)
    for scene in scenes:
        service.generate_visual(scene.scene_id,renderer.load().options)
    return renderer


class Reviewer:
    last_usage={}
    def __init__(self):
        self.calls=[]
        self.fail_at=None
    def generate(self,prompt,model,config):
        self.calls.append((prompt,config))
        self.last_usage={'input_tokens':1000,'output_tokens':100}
        if len(self.calls)==self.fail_at:
            raise RuntimeError('Injected review failure')
        return model(summary='Reviewed sampled visual continuity.',issues=[])


def test_review_covers_every_video_and_records_usage_without_editing_media(videos):
    engine=videos
    before=(engine.path/'visual_scenes.json').read_bytes()
    provider=Reviewer()
    result=review.review(engine,provider=provider)
    assert review.current(engine)
    assert len(result['coverage'])==len(engine.assets.creative.artifact('storyboard').scenes)
    assert all(row['sampleTimes'][0]==0 and row['sampleTimes'][-1]>row['duration']-.2 for row in result['coverage'])
    images=[part for prompt,_ in provider.calls for part in prompt[0]['content'] if part['type']=='input_image']
    assert images and len(images)>=result['frameCount']
    for part in images:
        with Image.open(BytesIO(base64.b64decode(part['image_url'].split(',',1)[1]))) as image:
            assert max(image.size)<=768
    usage=[r for r in CostTracker(engine.manager,engine.project_id).records() if r.step=='scene_continuity']
    assert len(usage)==len(provider.calls) and all(r.cost>0 and r.outcome=='succeeded' for r in usage)
    assert (engine.path/'visual_scenes.json').read_bytes()==before
    calls=len(provider.calls)
    assert review.review(engine,provider=provider)==result and len(provider.calls)==calls
    source=SceneProduction(engine.manager,engine.project_id)
    source.generate_visual(result['coverage'][0]['sceneId'],engine.load().options,force=True)
    assert not review.current(engine)


def test_failed_review_retains_completed_batches_and_records_failed_usage(videos,monkeypatch):
    monkeypatch.setattr(review,'BATCH_SIZE',3)
    provider=Reviewer()
    provider.fail_at=2
    with pytest.raises(RuntimeError,match='Injected review'):
        review.review(videos,provider=provider)
    assert not review.current(videos)
    first=provider.calls[0][0]
    provider.fail_at=None
    report=review.review(videos,provider=provider)
    assert review.current(videos) and report['batchCount']>1
    assert sum(call[0]==first for call in provider.calls)==1
    rows=[r for r in CostTracker(videos.manager,videos.project_id).records() if r.step=='scene_continuity']
    assert sum(r.outcome=='failed' for r in rows)==1


def test_missing_video_blocks_review_before_api_call(videos):
    source=review.snapshot(videos)
    videos.assets.local_path(source['videos'][0]['file']).unlink()
    provider=Reviewer()
    with pytest.raises(ValueError):
        review.review(videos,provider=provider)
    assert not provider.calls


def test_cost_budget_increases_with_duration_and_formats():
    minute=review.estimate(60,6)
    longer=review.estimate(600,60)
    dual=review.estimate(120,12)
    assert 0<minute['low']<minute['high']<longer['high']
    assert dual['low']>minute['low']


def display(manager,pid):
    import streamlit as st
    from story_channel.scene_continuity_review_ui import render
    st.session_state['generation-context-project']=pid
    render(manager,pid)


def test_ai_review_tab_has_review_button_loading_and_no_manual_scene_controls(videos,monkeypatch):
    started,release=Event(),Event()
    def delayed(engine,**kwargs):
        started.set()
        assert release.wait(15)
        return {}
    monkeypatch.setattr(review,'review',delayed)
    app=AppTest.from_function(display,args=(videos.manager,videos.project_id),default_timeout=30).run()
    assert not app.exception
    assert [b.label for b in app.button]==['Review']
    try:
        app.button[0].click().run()
        assert not app.exception and started.wait(2)
        assert app.button[0].label=='Reviewing...' and app.button[0].disabled
    finally:
        release.set()
        app.session_state['workspace-task']['future'].result(timeout=5)


def test_effect_catalog_filters_old_scenes_and_includes_only_requested_kind(videos):
    from story_channel.episode_effects_ui import catalog
    from story_channel.finishing_models import EffectLayer
    engine=videos
    scenes=engine.assets.creative.artifact('storyboard').scenes
    state=engine.finishing.load()
    state.plan.effects=[EffectLayer(effect_id='rain',scene_id=scenes[0].scene_id),EffectLayer(effect_id='old',scene_id='removed_scene')]
    engine.finishing.save(state)
    rows=catalog(engine,'VFX')
    assert len(rows)==1 and rows[0]['ID']=='rain'
    sounds=catalog(engine,'SFX')
    assert all(row['ID']!='rain' and 'removed_scene' not in row['Scenes'] for row in sounds)


def display_effects(manager,pid):
    import streamlit as st
    from story_channel.episode_effects_ui import render
    tabs=st.tabs(['VFX','SFX'])
    with tabs[0]:
        render(manager,pid,'VFX')
    with tabs[1]:
        render(manager,pid,'SFX')


def test_effect_tabs_are_read_only_episode_lists(videos):
    app=AppTest.from_function(display_effects,args=(videos.manager,videos.project_id),default_timeout=30).run()
    assert not app.exception
    assert not app.button and not app.text_input and not app.text_area
    assert [tab.label for tab in app.tabs]==['VFX','SFX']


def display_final(manager,pid):
    import streamlit as st
    from story_channel.final_ui import final_page
    st.session_state['generation-context-project']=pid
    final_page(manager,pid)


def test_final_contains_composition_and_finished_scene_review(videos):
    engine=videos
    for stage in ('voices','subtitles','consistency'):
        engine.assets.generate(stage)
    engine.assets.approve('assets')
    app=AppTest.from_function(display_final,args=(engine.manager,engine.project_id),default_timeout=30).run()
    assert not app.exception
    assert any(button.key=='generate-animation-scenes' for button in app.tabs[0].button)
    assert not app.button(key='generate-animation-scenes').disabled
    assert app.button(key='render-final-video')
