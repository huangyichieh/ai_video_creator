from decimal import Decimal
import json
from pathlib import Path
from threading import Event
import shutil

from PIL import Image
import pytest

from story_channel.costs import CostTracker
from story_channel.files import input_hash
from story_channel.finishing_models import FinishingPlan, EffectLayer, FaceTarget
from story_channel.llm import TransientProviderError
from story_channel.media_utils import wav_duration
from story_channel.models import GeneratedAsset
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from test_assets import assets, complete


def render_engine(assets, **kwargs):
    complete(assets)
    engine = RenderPipeline(assets.manager, assets.project_id, **kwargs)
    engine.configure(options=RenderOptions(width=320, height=180, fps=12, transition="cut", subtitles="none", music=False))
    return engine


def configure(engine, **updates):
    old = engine.finishing.load().plan
    plan = old.model_copy(update=updates, deep=True)
    return engine.finishing.configure(plan, expected=old.model_dump_json(by_alias=True))


class CopyLip:
    def __init__(self):
        self.calls = []
        self.last_usage = {}
    def generate(self, video, audio, config):
        self.calls.append((video, audio, dict(config.options)))
        destination = Path(config.options["output_path"])
        shutil.copyfile(video, destination)
        self.last_usage = {"video_seconds": config.options["duration"]}
        return GeneratedAsset(type="video", provider="sync", model=config.model, prompt_hash=input_hash("test"),
                              file_path=str(destination), duration=wav_duration(audio))


def test_dialogue_only_lipsync_reuses_audio_and_cached_jobs(assets):
    lip = CopyLip()
    engine = render_engine(assets, lip_provider=lip)
    configure(engine, lip_sync=True)
    result = engine.run()
    assert result.status == "review" and lip.calls
    production = assets.load()
    tasks = [task for task in assets.tasks(production) if task.stage == "voices"]
    dialogue_files = {assets.local_path(production.records[task.key].asset.file_path) for task in tasks if task.speaker != "narrator"}
    narration_files = {assets.local_path(production.records[task.key].asset.file_path) for task in tasks if task.speaker == "narrator"}
    assert {audio for _, audio, _ in lip.calls} == dialogue_files
    assert not narration_files & {audio for _, audio, _ in lip.calls}
    calls = len(lip.calls)
    engine.run()
    assert len(lip.calls) == calls
    record = next(iter(engine.finishing.load().records.values()))
    base_ids = {sid: r.clip.asset_id for sid, r in result.scenes.items()}
    engine.finishing.generate_selected([record.key], force=True)
    engine.run()
    assert len(lip.calls) == calls + 1
    assert base_ids == {sid: r.clip.asset_id for sid, r in engine.load().scenes.items()}
    assert engine.finishing.load().records[record.key].revision == 2


def test_local_vfx_is_transparent_and_composited_without_replacing_base(assets):
    engine = render_engine(assets)
    sid = next(iter(engine.load().scenes))
    effect = EffectLayer(effect_id="rain_layer", scene_id=sid, duration=.8, at=.1, opacity=.6)
    configure(engine, effects=[effect])
    result = engine.run()
    assert result.status == "review"
    record = engine.finishing.load().records["vfx:rain_layer"]
    assert record.asset.provider == "local" and record.asset.generation_params["alpha"]
    folder = assets.local_path(record.asset.file_path).parent
    with Image.open(folder / "frames" / "000000.png") as frame:
        low, high = frame.getchannel("A").getextrema()
        assert low == 0 and high > 0
    original = result.scenes[sid].clip.asset_id
    old_final = assets.local_path(result.final_file)
    engine.finishing.generate_selected(["vfx:rain_layer"], force=True)
    assert engine.load().status == "stale"
    assert engine.run().scenes[sid].clip.asset_id == original
    assert old_final.exists()
    assert engine.finishing.load().records["vfx:rain_layer"].revision == 2


def test_ai_vfx_is_an_independent_chroma_key_layer(assets):
    class GreenLayer:
        last_usage = {}
        def generate_clip(self, prompt, refs, config):
            assert not refs and "green" in prompt and "isolated" in prompt
            out = Path(config.options["output_path"])
            engine.ffmpeg.run(["-f", "lavfi", "-i", "color=c=0x00ff00:s=320x180:r=12:d=4",
                "-vf", "drawbox=x=100:y=50:w=30:h=30:color=red:t=fill", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out])
            self.last_usage = {"video_seconds": 4}
            return GeneratedAsset(type="video", provider="openai", model=config.model, file_path=str(out), duration=4, prompt_hash=input_hash(prompt))
    engine = render_engine(assets, vfx_provider=GreenLayer())
    sid = next(iter(engine.load().scenes))
    configure(engine, effects=[EffectLayer(effect_id="ai_layer", scene_id=sid, kind="openai", prompt="a red energy square", duration=1, opacity=1)])
    result = engine.run()
    assert result.status == "review"
    picture = assets.path / "composited.png"
    engine.ffmpeg.cut_frame(assets.local_path(result.scenes[sid].file), picture, .5)
    with Image.open(picture) as image:
        r, g, b = image.convert("RGB").getpixel((115, 65))
        assert r > 140 and g < 100 and b < 100
        rgb = image.convert("RGB")
        pixels = [rgb.getpixel((x, y)) for y in range(rgb.height) for x in range(rgb.width)]
        green = sum(g > 150 and g > r * 1.5 and g > b * 1.5 for r, g, b in pixels)
        assert green < len(pixels) * .05


def test_generated_sfx_costs_cache_and_prompt_changes(assets):
    from story_channel.ffmpeg_renderer import synthesized_sound
    class Sound:
        def __init__(self):
            self.calls = []
        def generate(self, prompt, config):
            self.calls.append(prompt)
            out = Path(config.options["output_path"])
            synthesized_sound(out, prompt, config.options["duration"])
            self.last_usage = {"audio_seconds": config.options["duration"], "billing_units": 12, "billing_unit": "characters"}
            return GeneratedAsset(type="audio", provider="elevenlabs", model=config.model, file_path=str(out),
                                  duration=config.options["duration"], prompt_hash=input_hash(prompt))
    provider = Sound()
    engine = render_engine(assets, sfx_provider=provider)
    assets.manager.settings.sfx_cost_per_second = Decimal(".2")
    configure(engine, sound_provider="elevenlabs")
    assert engine.run().status == "review"
    calls = len(provider.calls)
    assert calls > 0
    usage = [r for r in CostTracker(assets.manager, assets.project_id).records() if r.provider == "elevenlabs"]
    assert all(r.cost > 0 and r.billing_units == 12 for r in usage)
    cost = sum(r.cost for r in usage)
    assert assets.manager.load(assets.project_id).actual_cost == cost
    timeline = json.loads((assets.local_path(engine.load().final_file).parent / "timeline.json").read_text(encoding="utf-8"))
    generated = [cue for scene in timeline["scenes"] for cue in scene["sound"]["cues"] if cue["file"]]
    assert generated and all("elevenlabs" in cue["source"] for cue in generated)
    engine.run()
    assert len(provider.calls) == calls
    music = engine.load().music_plan
    next(sound for sound in music.scenes.values() if sound.cues).cues[0].prompt = "Heavy wet footsteps on stone"
    engine.configure(music_plan=music)
    engine.run()
    assert len(provider.calls) == calls + 1
    assert any("Heavy wet footsteps on stone" in prompt for prompt in provider.calls)
    assert all("no intelligible speech" in prompt for prompt in provider.calls)
    cue = next(sound for sound in engine.load().music_plan.scenes.values() if sound.cues).cues[0]
    record = next(r for r in engine.finishing.load().records.values()
                  if r.asset.generation_params["sourceInputs"]["prompt"] == cue.prompt)
    assert engine.finishing.task_intact(record, ("sfx", "cues", 0, cue))
    changed_cue = cue.model_copy(update={"prompt": "A wooden door shutting softly"})
    assert not engine.finishing.task_intact(record, ("sfx", "cues", 0, changed_cue))


def test_async_lip_retry_records_charge_once_and_resumes_saved_job(assets):
    class Resumable(CopyLip):
        failed = False
        def generate(self, video, audio, config):
            if not self.failed:
                self.failed = True
                self.last_usage = {"video_seconds": config.options["duration"]}
                config.options["job_update"]("job-saved", "PROCESSING", 50)
                raise TransientProviderError("resume saved job")
            if config.options.get("job_id"):
                assert config.options["job_id"] == "job-saved" and config.options["charge_recorded"]
                result = super().generate(video, audio, config)
                self.last_usage = {"video_seconds": 0}
                return result
            return super().generate(video, audio, config)
    lip = Resumable()
    engine = render_engine(assets, lip_provider=lip)
    assets.manager.settings.llm_max_retries = 1
    assets.manager.settings.lip_sync_cost_per_second = Decimal(".1")
    configure(engine, lip_sync=True)
    engine.run()
    usage = [r for r in CostTracker(assets.manager, assets.project_id).records() if r.provider == "sync"]
    assert usage[0].outcome == "failed" and usage[0].cost > 0
    assert usage[1].outcome == "succeeded" and usage[1].cost == 0
    assert usage[0].provider_job_id == usage[1].provider_job_id == "job-saved"
    assert assets.manager.load(assets.project_id).actual_cost == sum(r.cost for r in usage)


def test_finishing_stop_preserves_completed_dialogue_segments(assets):
    event = Event()
    class StopLip(CopyLip):
        def generate(self, *args):
            result = super().generate(*args)
            event.set()
            return result
    lip = StopLip()
    engine = render_engine(assets, lip_provider=lip, stop_requested=event.is_set)
    configure(engine, lip_sync=True)
    assert engine.run().status == "stopped"
    assert len(lip.calls) == 1
    completed = [r for r in engine.finishing.load().records.values() if r.status == "succeeded"]
    assert len(completed) == 1
    engine.stop_requested = lambda: False
    result = engine.run()
    assert result.status == "review"
    assert engine.finishing.load().records[completed[0].key].revision == 1


def test_multi_character_face_selection_fails_before_generation(assets):
    lip = CopyLip()
    engine = render_engine(assets, lip_provider=lip)
    configure(engine, lip_sync=True)
    scene = assets.creative.artifact("storyboard").scenes[0].model_copy(update={"character_ids": ["keeper", "other"]})
    with pytest.raises(ValueError, match="face target"):
        engine.finishing.preflight(scene, assets.load(), engine.load().music_plan.scenes[scene.scene_id])
    assert not lip.calls


def test_finishing_plan_modify_uses_openai_and_retains_ids(assets, monkeypatch):
    engine = render_engine(assets)
    sid = next(iter(engine.load().scenes))
    configure(engine, effects=[EffectLayer(effect_id="rain", scene_id=sid, duration=.8)])
    saved = engine.finishing.load().plan
    class Edit:
        last_usage = {"input_tokens": 30, "output_tokens": 20}
        def generate(self, prompt, model, config):
            assert config.provider == "openai"
            result = saved.model_copy(deep=True)
            result.effects[0].opacity = .8
            return result
    monkeypatch.setattr("story_channel.modification.OpenAILLMProvider", lambda **_: Edit())
    engine.finishing.modify_plan("Make the rain stronger", expected=saved.model_dump_json(by_alias=True))
    assert engine.finishing.load().plan.effects[0].opacity == .8
    assert engine.finishing.load().plan.effects[0].effect_id == "rain"
    assert CostTracker(assets.manager, assets.project_id).records()[-1].step == "modify:finishing_plan"


def test_vfx_modification_edits_existing_remote_layer_and_preserves_base(assets):
    class EditLayer:
        calls = []
        last_usage = {}
        def generate_clip(self, prompt, refs, config):
            self.calls.append(dict(config.options))
            destination = Path(config.options["output_path"])
            engine.ffmpeg.run(["-f", "lavfi", "-i", "color=c=0x00ff00:s=320x180:r=12:d=4", "-an", "-c:v", "libx264", destination])
            self.last_usage = {"video_seconds": 4}
            return GeneratedAsset(type="video", provider="openai", model=config.model, file_path=str(destination), duration=4,
                prompt_hash=input_hash(prompt), generation_params={"jobId": f"vfx_job_{len(self.calls)}"})
    provider = EditLayer()
    engine = render_engine(assets, vfx_provider=provider)
    sid = next(iter(engine.load().scenes))
    configure(engine, effects=[EffectLayer(effect_id="ai", scene_id=sid, kind="openai", prompt="isolated sparks", duration=.7)])
    result = engine.run()
    before = result.scenes[sid].clip.asset_id
    record = engine.finishing.load().records["vfx:ai"]
    engine.finishing.modify_effect("ai", "More sparks", expected=record.asset.asset_id)
    assert provider.calls[-1]["edit_source"] == "vfx_job_1"
    assert engine.finishing.load().records["vfx:ai"].revision == 2
    assert engine.run().scenes[sid].clip.asset_id == before


@pytest.mark.parametrize("invalid_media", [False, True])
def test_sfx_failures_record_cost_without_automatic_duplicate_requests(assets, invalid_media):
    class BrokenSound:
        calls = 0
        last_usage = {"audio_seconds": 99}
        def generate(self, prompt, config):
            self.calls += 1
            if not invalid_media:
                raise TransientProviderError("Sound request outcome is uncertain")
            self.last_usage = {"audio_seconds": config.options["duration"]}
            out = Path(config.options["output_path"])
            out.write_bytes(b"not a valid audio response")
            return GeneratedAsset(type="audio", provider="elevenlabs", model=config.model, file_path=str(out), prompt_hash=input_hash(prompt))
    provider = BrokenSound()
    engine = render_engine(assets, sfx_provider=provider)
    assets.manager.settings.llm_max_retries = 5
    assets.manager.settings.sfx_cost_per_second = Decimal(".2")
    configure(engine, sound_provider="elevenlabs")
    production, board = engine._input()
    key = next(key for key in engine.finishing.tasks(production, board, engine.load()) if key.startswith("sfx:"))
    with pytest.raises((TransientProviderError, RuntimeError)):
        engine.finishing.generate_selected([key], force=False)
    assert provider.calls == 1
    usage = [r for r in CostTracker(assets.manager, assets.project_id).records() if r.provider == "elevenlabs"]
    assert len(usage) == 1 and usage[0].outcome == "failed"
    assert usage[0].pricing_known == invalid_media
    assert (usage[0].cost > 0) == invalid_media
    assert assets.manager.load(assets.project_id).actual_cost == usage[0].cost
