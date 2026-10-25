from decimal import Decimal
import pytest

from story_channel.animation import AnimationDirector, require_voice
from story_channel.animation_rendering import approve_continuity, continuity_signature
from story_channel.costs import CostTracker
from story_channel.media_utils import wav_duration
from story_channel.pipeline import ApprovalRequired
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from story_channel.video_providers import LocalVideoProvider
from test_assets import assets, complete


def prepare_direction(assets):
    director=AnimationDirector(assets.manager,assets.project_id)
    state=director.prepare()
    for cid,profile in state.plan.voices.items():
        profile.voice_id='nova'
        profile.gender_presentation='feminine presentation selected by listening'
    for performance in state.plan.performances:
        performance.emotion='hopeful'
        performance.pause_before=.2
        performance.pause_after=.3
        performance.subtext='Reassure the other speaker without sounding like exposition'
    director.save_plan(state.plan)
    for cid in state.plan.voices:
        director.preview_voice(cid,'Keep the beacon burning. We will find a way through this storm, together.')
        director.approve_voice(cid)
    director.approve_performance()
    return director


def test_voice_casting_fails_fast_and_performance_drives_audio(assets):
    director=AnimationDirector(assets.manager,assets.project_id)
    state=director.prepare()
    cid=next(iter(state.plan.voices))
    with pytest.raises(ApprovalRequired,match='explicit Voice ID'):
        require_voice(assets.manager,assets.project_id,cid)
    director=prepare_direction(assets)
    state=director.load()
    assert 3 <= state.previews[cid].asset.duration <= 5
    complete(assets)
    state=assets.inspect()
    dialogue=next(r for r in state.records.values() if r.stage=='voices' and r.asset.generation_params['speaker']==cid)
    assert dialogue.asset.generation_params['config']['voice']=='nova'
    assert dialogue.asset.generation_params['config']['performance']['emotion']=='hopeful'
    assert wav_duration(assets.local_path(dialogue.asset.file_path))==pytest.approx(len(dialogue.asset.generation_params['prompt'])/10+.5,abs=.01)
    changed=director.load().plan
    changed.voices[cid].pitch_range='lower'
    director.save_plan(changed)
    with pytest.raises(ApprovalRequired,match='preview'):
        require_voice(assets.manager,assets.project_id,cid)


def test_animation_real_encoding_chaining_caching_and_continuity_gate(assets):
    director=prepare_direction(assets)
    complete(assets)
    director.approve_plan()
    engine=RenderPipeline(assets.manager,assets.project_id)
    assets.manager.settings.llm_max_retries=0
    assets.manager.settings.video_cost_per_second=Decimal('.1')
    engine.configure(options=RenderOptions(provider='openai',continuity_animation=True,width=320,height=180,fps=12,video_seconds=4))
    local=LocalVideoProvider(engine.ffmpeg)
    calls=[]
    failed_once=[]
    class FakeAnimatedVideo:
        last_usage={}
        def generate_clip(self,prompt,references,config):
            calls.append((prompt,list(references),config))
            if len(calls)==2 and not failed_once:
                failed_once.append(True)
                self.last_usage={}
                raise RuntimeError('Simulated shot failure')
            asset=local.generate_clip(prompt,references,config)
            asset.provider,asset.model='openai',config.model
            self.last_usage={'video_seconds':config.options['seconds']}
            return asset
    engine.video_provider=FakeAnimatedVideo()
    with pytest.raises(RuntimeError,match='scene'):
        engine.run()
    interrupted=director.load()
    assert sum(r.status=='succeeded' for r in interrupted.shots.values())==1
    successful=next(r for r in interrupted.shots.values() if r.status=='succeeded')
    state=engine.run()
    assert director.load().shots[successful.shot_id].revision==1
    assert state.status=='continuity_review'
    assert not state.final_file
    shots=list(director.load().shots.values())
    assert len(shots)>len(state.scenes)
    assert all(s.status=='succeeded' for s in shots)
    assert any('cut_frame.png' in str(refs[0]) for _,refs,_ in calls[1:])
    assert all(config.options['reference_pack'] for _,_,config in calls)
    original_count=len(calls)
    approve_continuity(director,'Reviewed appearance, set geometry, emotional delivery and cuts')
    final=engine.run()
    assert final.status=='review' and final.final_file
    assert len(calls)==original_count
    assert engine.approve().status=='approved'
    usage=[r for r in CostTracker(assets.manager,assets.project_id).records() if r.step.startswith('animation:shot:')]
    assert len(usage)==len(shots)+1
    assert sum(r.outcome=='failed' for r in usage)==1
    assert sum(r.cost for r in usage)==Decimal('.4')*len(shots)
    # Retry a shot retains earlier shots; later dependent shots follow its new cut frame.
    first_scene=next(iter(state.scenes))
    last=next(s for s in reversed(shots) if s.scene_id==first_scene)
    before={s.shot_id:s.revision for s in shots}
    engine.retry_animation_shot(last.shot_id)
    changed=director.load()
    assert changed.shots[shots[0].shot_id].revision==before[shots[0].shot_id]
    assert changed.shots[last.shot_id].revision==before[last.shot_id]+1
    assert not changed.continuity_approval


def test_direction_preserves_dialogue_and_source_ids(assets):
    director=AnimationDirector(assets.manager,assets.project_id)
    plan=director.prepare().plan
    plan.performances[0].text='An unauthorized rewrite'
    with pytest.raises(ValueError,match='preserve dialogue'):
        director.save_plan(plan)
    with pytest.raises(ValueError,match='image-to-video'):
        RenderOptions(continuity_animation=True)


def test_openai_direction_edit_uses_valid_strict_schema_and_tracks_usage(assets,monkeypatch):
    import json
    import httpx
    from openai import OpenAI
    from story_channel.animation_models import DirectionEdit
    from story_channel.llm import OpenAILLMProvider
    import story_channel.modification as modification
    from test_openai_adapter import response_payload
    director=AnimationDirector(assets.manager,assets.project_id)
    state=director.prepare()
    edited=DirectionEdit(source_hash=state.plan.source_hash,voices=list(state.plan.voices.values()),
        locations=list(state.plan.locations.values()),performances=state.plan.performances,shots=state.plan.shots)
    edited=edited.model_copy(deep=True)
    edited.performances[0].emotion='hopeful'
    def handler(request):
        body=json.loads(request.content)
        assert body['text']['format']['schema']['properties']['voices']['type']=='array'
        payload=response_payload()
        payload['output'][0]['content'][0]['text']=edited.model_dump_json(by_alias=True)
        return httpx.Response(200,json=payload,headers={'x-request-id':'req_director'})
    with OpenAI(api_key='test-only',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        provider=OpenAILLMProvider(client=client)
        monkeypatch.setattr(modification,'OpenAILLMProvider',lambda **kwargs:provider)
        result=director.modify_with_openai('Make the first line hopeful',state.plan.model_dump_json(by_alias=True))
    assert result.plan.performances[0].emotion=='hopeful'
    usage=CostTracker(assets.manager,assets.project_id).records()[-1]
    assert usage.step=='modify:animation_plan'
    assert usage.request_ids==['req_director']


def test_emotion_preview_keeps_baseline_and_requires_new_approval(assets):
    director = prepare_direction(assets)
    cid = next(iter(director.load().plan.voices))
    baseline = director.load().previews[cid].asset.asset_id
    director.preview_voice(cid, "Keep the beacon burning. We will find a way through this storm, together.", emotion="happy")
    state = director.load()
    assert state.previews[cid].asset.asset_id == baseline
    assert state.previews[cid].approval is None
    assert state.emotion_previews[cid]["happy"].asset.duration >= 3
    assert state.plan.voices[cid].baseline_emotion == "neutral"
    with pytest.raises(ApprovalRequired):
        require_voice(assets.manager, assets.project_id, cid)
    director.approve_voice(cid)
    assert director.load().emotion_previews[cid]["happy"].approval
    assert CostTracker(assets.manager, assets.project_id).records()[-1].step == "animation:voice_preview"
    with pytest.raises(ValueError, match="supported preview emotion"):
        director.preview_voice(cid, "sample", emotion="unknown")
