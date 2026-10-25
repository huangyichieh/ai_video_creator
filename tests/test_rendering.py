from pathlib import Path
import json
from decimal import Decimal

import pytest

from story_channel.assets import AssetPipeline
from story_channel.cli import main
from story_channel.costs import CostTracker
from story_channel.ffmpeg_renderer import FFmpeg
from story_channel.files import atomic_write
from story_channel.models import ProjectStatus
from story_channel.pipeline import ApprovalRequired, Pipeline
from story_channel.render_models import RenderOptions
from story_channel.rendering import RenderPipeline
from story_channel.video_providers import LocalVideoProvider
from test_assets import complete
from test_assets_ui import prepare


@pytest.fixture
def renderer(tmp_path):
    manager, project = prepare(tmp_path)
    complete(AssetPipeline(manager, project.project_id))
    engine = RenderPipeline(manager, project.project_id)
    engine.configure(options=RenderOptions(width=320, height=180, fps=12))
    return engine


def test_real_render_cache_approval_and_invalidation(renderer):
    state = renderer.run()
    assert state.status == 'review'
    assert all(r.status == 'succeeded' for r in state.scenes.values())
    output = renderer.path / 'output'
    assert FFmpeg().validate(output / 'final.mp4') > 0
    timeline = json.loads((output / 'timeline.json').read_text(encoding='utf-8'))
    assert timeline['scenes'][0]['start'] == 0
    assert timeline['scenes'][-1]['end'] == pytest.approx(timeline['duration'])
    for a, b in zip(timeline['scenes'], timeline['scenes'][1:]):
        assert a['end'] == b['start']
    assert 'WEBVTT' in (output / 'final.vtt').read_text(encoding='utf-8')
    assert '-->' in (output / 'final.srt').read_text(encoding='utf-8')
    count = len(CostTracker(renderer.manager, renderer.project_id).records())
    version = state.final_file
    assert renderer.run().final_file == version
    assert len(CostTracker(renderer.manager, renderer.project_id).records()) == count
    approved = renderer.approve('All media reviewed')
    assert approved.approval.content_hash == state.final_input_hash
    Pipeline(renderer.manager, renderer.project_id).inspect()
    renderer.assets.inspect()
    manifest = renderer.manager.load(renderer.project_id)
    assert manifest.status == ProjectStatus.RENDERED
    assert manifest.final_approval.note == 'All media reviewed'
    assert renderer.inspect().status == 'approved'
    # Sound-only changes reuse expensive video clips, but require a new final review.
    options = state.options.model_copy(update={'music_gain': .05})
    changed = renderer.configure(options=options)
    assert changed.status == 'stale' and changed.approval is None
    assert all(r.status == 'stale' for r in changed.scenes.values())
    state = renderer.run()
    assert state.final_file != version and state.final_revision == 2
    assert len(CostTracker(renderer.manager, renderer.project_id).records()) == count
    assert renderer.assets.local_path(version).exists()
    # Tampered sidecars revoke final approval without re-rendering intact scenes.
    renderer.approve()
    caption = renderer.assets.local_path(state.final_file).parent / 'final.srt'
    caption.write_text('tampered', encoding='utf-8')
    assert renderer.inspect().status == 'stale'
    assert renderer.manager.load(renderer.project_id).final_approval is None
    restored = renderer.run()
    assert restored.final_revision == 3
    # Source edits revoke approval and block rendering until asset review.
    creative = Pipeline(renderer.manager, renderer.project_id)
    creative.edit('outline', 'A changed story')
    assert renderer.manager.load(renderer.project_id).final_approval is None
    with pytest.raises(ApprovalRequired):
        renderer.run()


def test_scene_generation_and_approved_final_assembly_are_separate(renderer, monkeypatch):
    state = renderer.generate_scenes()
    assert all(record.status == "succeeded" for record in state.scenes.values())
    assert state.final_file is None
    assert not (renderer.path / "output" / "final.mp4").exists()
    with pytest.raises(ApprovalRequired, match="approve every current scene"):
        renderer.assemble_final()
    scene_ids = list(state.scenes)
    renderer.approve_scenes(scene_ids[:1])
    with pytest.raises(ApprovalRequired):
        renderer.assemble_final()
    renderer.approve_scenes(scene_ids)
    usage_count = len(CostTracker(renderer.manager, renderer.project_id).records())
    def forbidden(*args, **kwargs):
        raise AssertionError("Final assembly must not generate media")
    monkeypatch.setattr(renderer, "_render_scene", forbidden)
    progress_path = renderer.path / "assembly_progress.json"
    def fail_assembly(*args, progress, **kwargs):
        progress(.5)
        raise RuntimeError("Simulated final assembly failure")
    with monkeypatch.context() as patch:
        patch.setattr(renderer.ffmpeg, "assemble", fail_assembly)
        with pytest.raises(RuntimeError, match="Simulated final assembly failure"):
            renderer.assemble_final()
    failed = json.loads(progress_path.read_text())
    assert failed["status"] == "failed" and failed["percent"] == 50
    assert not renderer.load().final_file
    progress_updates = []
    original_progress = renderer.assembly_progress
    def track_progress(percent, stage, **kwargs):
        progress_updates.append(percent)
        original_progress(percent, stage, **kwargs)
    monkeypatch.setattr(renderer, "assembly_progress", track_progress)
    final = renderer.assemble_final()
    assert final.status == "review" and final.final_file
    assert final.error is None
    assert progress_updates == sorted(progress_updates)
    assert 90 in progress_updates and 92 in progress_updates
    assert json.loads(progress_path.read_text())["percent"] == 100
    assert json.loads(progress_path.read_text())["status"] == "completed"
    assert len(CostTracker(renderer.manager, renderer.project_id).records()) == usage_count
    renderer.configure(options=final.options.model_copy(update={"music_gain": .03}))
    assert all(not r.approved_hash for r in renderer.load().scenes.values())
    with pytest.raises(ApprovalRequired):
        renderer.assemble_final()


def test_failed_composition_retries_cached_clip_and_stop(renderer, monkeypatch):
    original = renderer.ffmpeg.compose_scene
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError('Simulated encoding failure')
        return original(*args, **kwargs)

    monkeypatch.setattr(renderer.ffmpeg, 'compose_scene', fail_once)
    with pytest.raises(RuntimeError, match='Completed scenes were retained'):
        renderer.run()
    state = renderer.load()
    bad = next(r for r in state.scenes.values() if r.status == 'failed')
    good = {sid: r.revision for sid, r in state.scenes.items() if r.status == 'succeeded'}
    assert bad.clip is not None
    count = len(CostTracker(renderer.manager, renderer.project_id).records())
    final = renderer.run()
    assert final.status == 'review'
    assert len(CostTracker(renderer.manager, renderer.project_id).records()) == count
    assert {sid: final.scenes[sid].revision for sid in good} == good
    # Stop is cooperative: a finished current scene remains saved.
    original_provider = LocalVideoProvider(renderer.ffmpeg)
    calls = []

    class StoppingProvider:
        last_usage = {}
        def generate_clip(self, *args):
            result = original_provider.generate_clip(*args)
            calls.append(1)
            renderer.stop_requested = lambda: True
            return result

    renderer.video_provider = StoppingProvider()
    stopped = renderer.run(force=True)
    assert stopped.status == 'stopped' and len(calls) == 1
    assert any(r.status == 'succeeded' for r in stopped.scenes.values())
    renderer.stop_requested = lambda: False
    assert renderer.run().status == 'review'


def test_video_failure_cost_recorded_and_partial_scene_state(renderer):
    renderer.manager.settings.video_cost_per_second = Decimal('.10')
    renderer.manager.settings.llm_max_retries = 0
    renderer.configure(options=RenderOptions(provider='openai', width=320, height=180, fps=12))

    class FailedVideo:
        last_usage = {'video_seconds': 8}
        def generate_clip(self, *args):
            raise RuntimeError('Remote failure')

    renderer.video_provider = FailedVideo()
    subject = next(iter(renderer.load().scenes))
    with pytest.raises(RuntimeError, match=r'scene\(s\) failed'):
        renderer.run(subject=subject)
    usage = CostTracker(renderer.manager, renderer.project_id).records()[-1]
    assert usage.cost == Decimal('.80') and usage.outcome == 'failed'
    assert usage.pricing_known
    renderer.configure(options=RenderOptions(width=320, height=180, fps=12))
    renderer.video_provider = LocalVideoProvider(renderer.ffmpeg)
    partial = renderer.run(subject=subject)
    assert partial.status == 'stopped'
    assert partial.scenes[subject].status == 'succeeded'
    # A process interruption can resume rather than appearing permanently active.
    partial.scenes[subject].status = 'running'
    atomic_write(renderer.state_path, partial.model_dump_json(by_alias=True))
    assert renderer.inspect().scenes[subject].status == 'failed'


def test_audio_plan_validation_and_cli(renderer, tmp_path, capsys):
    from story_channel.media_utils import silence
    relative = renderer.import_audio(silence(1), 'ambient.wav', 'Original recording by project owner')
    plan = renderer.load().music_plan
    first = next(iter(plan.scenes))
    plan.scenes[first].bgm_file = relative
    plan.scenes[first].bgm_source = 'Original recording by project owner'
    renderer.configure(music_plan=plan)
    assert renderer.load().music_plan.scenes[first].bgm_file == relative
    plan.scenes[first].cues[0].at = 9999
    with pytest.raises(ValueError, match='fit within'):
        renderer.configure(music_plan=plan)
    with pytest.raises(ValueError, match='license/source'):
        renderer.import_audio(silence(1), 'x.wav', '')
    with pytest.raises(RuntimeError):
        renderer.import_audio(b'bad audio', 'x.wav', 'Own recording')
    options = tmp_path / 'render-options.json'
    options.write_text(RenderOptions(width=320, height=180, fps=12, subtitles='soft').model_dump_json(by_alias=True))
    common = ['--data-root', str(renderer.manager.root), 'render', renderer.project_id]
    for args in (['configure', '--options', str(options)], ['run'], ['show'], ['approve', '--note', 'CLI checked']):
        assert main(common + args) == 0
        capsys.readouterr()
    assert renderer.inspect().status == 'approved'
    from story_channel.logging_config import configure_logging
    configure_logging("WARNING")


def test_render_blocks_unapproved_assets(tmp_path):
    manager, project = prepare(tmp_path)
    with pytest.raises(ApprovalRequired):
        RenderPipeline(manager, project.project_id).run()


def test_fractional_scene_durations_do_not_accumulate_padding(tmp_path):
    from PIL import Image
    from story_channel.media_utils import silence
    image, voice, srt = tmp_path/'image.png', tmp_path/'voice.wav', tmp_path/'captions.srt'
    Image.new('RGB', (320,180), 'navy').save(image)
    duration = .413
    voice.write_bytes(silence(duration))
    srt.write_text('1\n00:00:00,000 --> 00:00:00,400\nTest\n', encoding='utf-8')
    ffmpeg = FFmpeg()
    options = RenderOptions(width=320, height=180, fps=24, subtitles='soft')
    visual = tmp_path/'visual.mp4'
    ffmpeg.visual(image, visual, duration, options)
    clips = []
    for i in range(3):
        work = tmp_path/f'scene{i}'
        work.mkdir()
        dest = work/'scene.mp4'
        ffmpeg.compose_scene(visual, voice, srt, dest, duration, options)
        clips.append(dest)
    work = tmp_path/'final'
    work.mkdir()
    progress = []
    ffmpeg.assemble(clips*4, work/'final.mp4', durations=[duration]*12, fps=24, progress=progress.append)
    assert len(progress) >= 2  # FFmpeg's output time, followed by completion.
    assert progress == sorted(progress) and progress[-1] == 1
    assert not (work/'ffmpeg-progress.txt').exists()
    assert ffmpeg.validate(work/'final.mp4') == pytest.approx(duration*12, abs=.1)


def test_remote_retry_reuses_job_and_video_modification_records_cost(renderer):
    from story_channel.llm import TransientProviderError
    renderer.configure(options=RenderOptions(provider='openai', width=320, height=180, fps=12))
    renderer.manager.settings.video_cost_per_second = Decimal('.10')
    requests = []
    local = LocalVideoProvider(renderer.ffmpeg)

    class RemoteVideo:
        last_usage = {}
        def generate_clip(self, prompt, references, config):
            requests.append((prompt, dict(config.options)))
            if len(requests) == 1:
                config.options['job_update']('saved_job', 'in_progress', 30)
                self.last_usage = {'video_seconds': 8, 'job_id': 'saved_job'}
                raise TransientProviderError('Temporary polling error')
            result = local.generate_clip(prompt, references, config)
            result.provider, result.model = 'openai', 'sora-2'
            result.generation_params['jobId'] = 'saved_job' if config.options.get('job_id') else 'edited_job'
            self.last_usage = {'video_seconds': 0 if config.options.get('job_id') else 8}
            config.options['job_update'](result.generation_params['jobId'], 'completed', 100)
            return result

    renderer.video_provider = RemoteVideo()
    subject = next(iter(renderer.load().scenes))
    state = renderer.run(subject=subject)
    assert requests[1][1]['job_id'] == 'saved_job'
    usage = [r for r in CostTracker(renderer.manager, renderer.project_id).records() if r.step == 'render:video']
    assert [(r.video_seconds, r.cost, r.outcome) for r in usage] == [(8, Decimal('.8'), 'failed'), (0, Decimal(0), 'succeeded')]
    with pytest.raises(ValueError, match='changed'):
        renderer.modify_with_openai(subject, 'Warmer lighting', expected='wrong-id')
    state = renderer.modify_with_openai(subject, 'Warmer lighting', expected=state.scenes[subject].clip.asset_id)
    assert requests[-1][0] == 'Warmer lighting'
    assert requests[-1][1]['edit_source'] == 'saved_job'
    assert state.scenes[subject].revision == 2
    assert CostTracker(renderer.manager, renderer.project_id).records()[-1].cost == Decimal('.80')
