from asset_test_helpers import complete_character_parts
import pytest
from story_channel.assets import AssetPipeline
from story_channel.identity import IdentityManager
from story_channel.library_media import LibraryMedia
from story_channel.studio import Episodes, approve_content, content_digest
from story_channel.pipeline import Pipeline
from story_channel.animation import AnimationDirector
from story_channel.scene_production import SceneProduction
from story_channel.rendering import RenderPipeline
from story_channel.render_models import RenderOptions
from test_studio import world


@pytest.fixture
def episode(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id)
    identity = IdentityManager(world.manager, world.project_id)
    library = identity.load()
    baseline = library.model_dump_json(by_alias=True)
    for profile in library.voices.values():
        profile.voice_id = "nova"
    identity.save(library, expected=baseline)
    engine.generate("character_references")
    complete_character_parts(engine)
    engine.approve("characters")
    from story_channel.background_library import BackgroundLibrary,locations
    from story_channel.floor_plan_guides import publish_guides
    from story_channel import location_pairs
    for location in locations(engine.creative):
        library_service = BackgroundLibrary(engine)
        library_service.configure_references(location.location_id,floor_plan="Fixed room center",object_layout={"window":"north wall"})
        publish_guides(engine,location.location_id,engine.load().location_reference_sets[location.location_id])
        state = engine.generate("backgrounds",subject=location.location_id)
        location_pairs.approve(engine,location.location_id,"masters",expected={name:state.records[f"backgrounds:{location.location_id}__{name}"].asset.asset_id for name in location_pairs.names("masters")})
    engine.approve("backgrounds")
    media = LibraryMedia(world.manager, world.project_id)
    for cid in library.voices:
        if media.voice_approved(cid):
            continue
        media.talking_sample(cid, "Keep the beacon burning. We will find our way home together.", video=False)
        media.approve_voice(cid)
    # Shared readiness follows character/background approvals; no extra gate.
    assert identity.load().approval is None
    identity.require_approved()
    service = Episodes(world.manager, world.project_id)
    episode = service.create("First episode", "The keeper saves the island's memory.", 12)
    pipeline = Pipeline(world.manager, episode.project_id)
    approve_content(pipeline, "outline", expected=content_digest(pipeline, "outline"))
    pipeline.generate("story")
    pipeline.generate("review")
    pipeline.approve("story")
    pipeline.generate("script")
    approve_content(pipeline, "script", expected=content_digest(pipeline, "script"))
    pipeline.generate("storyboard")
    pipeline.approve("storyboard")
    pipeline.generate("prompts")
    approve_content(pipeline, "prompts", expected=content_digest(pipeline, "prompts"))
    service.import_assets(episode.project_id)
    from asset_test_helpers import complete_storyboard_assets
    complete_storyboard_assets(world.manager,episode.project_id)
    return world, episode, pipeline


def test_shared_approvals_scene_direction_visual_before_voices_and_reuse(episode):
    world, episode, pipeline = episode
    manager = world.manager
    engine = AssetPipeline(manager, episode.project_id)
    assert {"characters", "backgrounds"} <= manager.load(episode.project_id).asset_approvals.keys()
    assert engine.inspect().next_stage == "scene_images"
    engine.generate("scene_images")
    director = AnimationDirector(manager, episode.project_id)
    state = director.prepare()
    assert all(preview.approval for preview in state.previews.values())
    service = SceneProduction(manager, episode.project_id)
    board = pipeline.artifact("storyboard").scenes
    for scene in board:
        service.approve_direction(scene.scene_id, expected=service.direction_hash(scene.scene_id))
    director.approve_plan()
    director.approve_performance()
    render = RenderPipeline(manager, episode.project_id)
    options = RenderOptions(width=320, height=180, fps=12, music=False, sfx=False, subtitles="none")
    render.configure(options=options)
    from story_channel.animations_workflow import animation_snapshot
    assert animation_snapshot(render)[0].stage == "visual_scenes"
    scene = board[0]
    visual = service.generate_visual(scene.scene_id, options)
    assert visual["status"] == "succeeded"
    assert not engine.load().scenes  # No voice export required to generate motion.
    service.approve_visual(scene.scene_id, options, expected=service.visual_hash(scene.scene_id, options))
    assert service.visual_current(scene.scene_id, options, approved=True)
    work = service.path / "videos" / "fitted.mp4"
    fitted = service.fitted_visual(scene.scene_id, options, scene.duration_sec / 2, work)
    assert fitted.generation_params["fittedToVoiceSeconds"] == scene.duration_sec / 2
    assert work.exists()


def test_episode_freezes_approved_shared_bundle_and_refreshes_explicitly(episode):
    from story_channel.media_utils import file_hash
    world, episode, pipeline = episode
    manager = world.manager
    service = Episodes(manager, world.project_id)
    assert manager.load(episode.project_id).shared_assets_hash
    child = AssetPipeline(manager, episode.project_id)
    root = AssetPipeline(manager, world.project_id)
    initial = child.load()
    key = next(key for key in initial.records if key.startswith("character_references:"))
    saved = initial.records[key]
    path = child.local_path(saved.asset.file_path)
    timestamp = path.stat().st_mtime_ns
    approvals = manager.load(episode.project_id).asset_approvals
    service.import_assets(episode.project_id)
    assert path.stat().st_mtime_ns == timestamp
    assert manager.load(episode.project_id).asset_approvals == approvals
    # Repair missing local media from the original immutable revision.
    path.unlink()
    service.import_assets(episode.project_id)
    assert file_hash(path) == saved.file_hash
    samples = LibraryMedia(manager, episode.project_id).load()["samples"]
    cid = next(iter(samples))
    audio_path = child.local_path(samples[cid][0]["audio"])
    audio_path.unlink()
    assert not LibraryMedia(manager, episode.project_id).voice_approved(cid)
    service.import_assets(episode.project_id)
    assert LibraryMedia(manager, episode.project_id).voice_approved(cid)
    # A new root revision must not replace an episode's selected revision.
    root.generate("character_references", force=True)
    complete_character_parts(root)
    root.approve("characters")
    audio = next(iter(LibraryMedia(manager, world.project_id).load()["samples"].values()))[0]["audio"]
    licensed = RenderPipeline(manager, world.project_id).import_audio(root.local_path(audio).read_bytes(), "music.wav", "Own test recording")
    service.import_assets(episode.project_id)
    assert child.load().records[key].asset.asset_id == saved.asset.asset_id
    assert not child.local_path(licensed).exists()
    service.refresh_assets(episode.project_id)
    assert child.load().records[key].asset.asset_id == root.load().records[key].asset.asset_id
    assert child.load().records[key].asset.asset_id != saved.asset.asset_id
    assert child.local_path(licensed).exists()
    assert list((child.path / "audio" / "library").glob("*.source.json"))


def test_world_inventory_and_voice_gate_follow_current_files_without_an_episode_plan(episode):
    from story_channel.assets_status import assets_status_rows
    from story_channel.assets_workflow import next_assets_action
    world, _, _ = episode
    engine = AssetPipeline(world.manager, world.project_id)
    state = engine.inspect()
    rows = assets_status_rows(engine, state)
    assert {"voice_profiles", "voice_samples", "voice_approval", "narrator", "costumes", "audio_library"} <= {r["Stage"] for r in rows}
    assert not any(r["Stage"] == "style_references" for r in rows)
    assert not any(row["Stage"] == "identity_approval" for row in rows)
    assert all(r["Tab"] not in ("Voice Profiles", "Character References", "Locations / Backgrounds") for r in rows)
    assert next_assets_action(engine, state).stage == "complete"
    media = LibraryMedia(world.manager, world.project_id)
    cid, samples = next(iter(media.load()["samples"].items()))
    path = engine.local_path(samples[0]["audio"])
    original = path.read_bytes()
    try:
        path.write_bytes(b"changed outside the app")
        assert not media.voice_approved(cid)
        assert next_assets_action(engine, state).stage == "complete"
        changed = assets_status_rows(engine, state)
        assert next(row for row in changed if row["Stage"] == "voice_approval" and row["Subject"] == cid)["Approval"] == "Not approved"
    finally:
        path.write_bytes(original)


def test_background_view_sources_resolve_world_location_but_cannot_edit_approved_canon(episode, monkeypatch):
    from story_channel.asset_sources import read_source, modify_source_with_openai
    world, project, pipeline = episode
    location = world.pipeline.artifact("world").locations[0]
    subject = location.location_id + "__left"
    assert read_source(world.pipeline, "backgrounds", subject) == location
    calls = []
    monkeypatch.setattr("story_channel.modification.modify_content", lambda *a, **kw: calls.append(kw))
    with pytest.raises(ValueError, match="immutable"):
        modify_source_with_openai(world.pipeline, "backgrounds", subject, "Change the geography", expected=location.model_dump_json(by_alias=True))
    assert not calls  # Reject immutable edits before a paid request.
    with pytest.raises(ValueError, match="parent World"):
        AssetPipeline(world.manager, project.project_id).generate("character_references", force=True)


def test_props_modes_and_generated_visuals_are_separate_and_cost_tracked(world):
    world.generate_characters()
    media = LibraryMedia(world.manager, world.project_id)
    pid = media.add_prop("Memory compass", "A brass compass containing a blue crystal", ["closed", "active"])
    first = media.image("prop:" + pid + ":closed", "A closed brass memory compass")
    second = media.image("prop:" + pid + ":active", "An active compass with a glowing blue crystal")
    assert first.file_path != second.file_path
    assert len(media.load()["visuals"]) == 2
    from story_channel.costs import CostTracker
    assert len([r for r in CostTracker(world.manager, world.project_id).records() if r.step.startswith("library:image:")]) == 2


def test_episode_animations_ui_has_asset_check_and_ordered_scene_tabs(episode, monkeypatch):
    world, episode, pipeline = episode
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-animations").click().run()
    assert not app.exception
    labels = [tab.label for tab in app.tabs]
    assert labels[:8] == ["Check assets", "Scene images", "Direction & performance", "Animated scenes", "Voices & input", "Video effects", "Sound effects", "Scene review & continuity"]
    assert app.button(key=f"animation-input-images-{episode.project_id}")


def test_episode_visuals_voices_composition_and_final_remain_separate(episode):
    world, episode, pipeline = episode
    engine = AssetPipeline(world.manager, episode.project_id)
    engine.generate("scene_images")
    director = AnimationDirector(world.manager, episode.project_id)
    director.prepare()
    service = SceneProduction(world.manager, episode.project_id)
    scenes = pipeline.artifact("storyboard").scenes
    with pytest.raises(ValueError, match="every scene"):
        director.approve_plan()
    for scene in scenes:
        service.approve_direction(scene.scene_id, expected=service.direction_hash(scene.scene_id))
    director.approve_plan()
    director.approve_performance()
    renderer = RenderPipeline(world.manager, episode.project_id)
    options = RenderOptions(width=320, height=180, fps=12, music=False, sfx=False, subtitles="none")
    renderer.configure(options=options)
    with pytest.raises(ValueError, match="animated scenes"):
        engine.generate("voices")
    for scene in scenes:
        service.generate_visual(scene.scene_id, options)
        service.approve_visual(scene.scene_id, options, expected=service.visual_hash(scene.scene_id, options))
    for stage in ("voices", "subtitles", "consistency"):
        engine.generate(stage)
    engine.approve("assets")
    from story_channel.costs import CostTracker
    video_calls = [r.record_id for r in CostTracker(world.manager, episode.project_id).records() if r.video_seconds]
    state = renderer.generate_scenes()
    assert state.final_file is None
    assert all(sound.bgm_file for sound in state.music_plan.scenes.values())
    assert all(cue.file for sound in state.music_plan.scenes.values() for cue in sound.cues if cue.name == "waves")
    assert all(record.status == "succeeded" for record in state.scenes.values())
    assert [r.record_id for r in CostTracker(world.manager, episode.project_id).records() if r.video_seconds] == video_calls
    renderer.approve_scenes(list(state.scenes))
    final = renderer.assemble_final()
    assert final.final_file and renderer.assets.local_path(final.final_file).exists()
    assert final.status == "review" and not final.approval


def test_talking_sample_reuses_audio_after_failed_video_and_generates_single_portrait(episode, monkeypatch):
    world, project, pipeline = episode
    from story_channel.media_providers import DemoImageProvider
    from story_channel.video_providers import LocalVideoProvider
    from story_channel.costs import CostTracker
    from pathlib import Path
    media = LibraryMedia(world.manager, world.project_id)
    cid = next(iter(media.load()["samples"]))
    baseline = media.load()["samples"][cid][0]
    count = lambda: len([r for r in CostTracker(world.manager, world.project_id).records() if r.step.startswith("library:voice_sample:")])
    before = count()
    def broken(*args, **kwargs):
        raise RuntimeError("Injected video failure")
    with monkeypatch.context() as patch:
        patch.setattr(LocalVideoProvider, "generate_clip", broken)
        with pytest.raises(RuntimeError, match="video failure"):
            media.talking_sample(cid, baseline["text"])
    assert count() == before
    assert media.load()["samples"][cid][-1]["status"] == "failed"
    assert media.load()["samples"][cid][-1]["audio"] == baseline["audio"]
    assert cid in media.load()["voiceApprovals"]
    # Exercise the production routing with local adapters; no real API requests.
    portraits = []
    class Image(DemoImageProvider):
        def generate(self, prompt, references, config):
            portraits.append(prompt)
            return super().generate(prompt, references, config)
    class Video(LocalVideoProvider):
        def __init__(self, **kwargs):
            super().__init__()
        def generate_clip(self, prompt, references, config):
            assert Path(references[0]).name == "portrait.png"
            assert config.options["seconds"] == config.options["duration"] == 3
            return super().generate_clip(prompt, references, config)
    media.assets.demo = False
    # Seed production-attributed speech while keeping all adapters local.
    state = media.load()
    for samples in state["samples"].values():
        for sample in samples:
            sample["audioAsset"]["provider"] = world.manager.settings.tts_provider
            sample["audioAsset"]["model"] = world.manager.settings.tts_model
    media.save(state)
    media.assets.image_provider = Image()
    monkeypatch.setattr("story_channel.balanced_providers.KlingVideoProvider", Video)
    monkeypatch.setattr("story_channel.voice_validation.validate_provider_voice", lambda *a, **kw: None)
    compiled = []
    def compile_sample(pipeline, text, **kwargs):
        compiled.append(kwargs)
        return "Brief talking animation." if kwargs.get("video") else text
    monkeypatch.setattr("story_channel.provider_prompts.compile_prompt", compile_sample)
    result = media.talking_sample(cid, baseline["text"])
    assert count() == before
    assert result["status"] == "succeeded" and media.assets.local_path(result["video"]).exists()
    from story_channel.ffmpeg_renderer import FFmpeg
    assert FFmpeg().validate(media.assets.local_path(result["video"])) <= 3
    assert portraits and "No reference sheet" in portraits[0]
    assert any(call.get("video") and call["task_key"].startswith("library:talking_sample:") for call in compiled)
    assert result["prompt"] == "Brief talking animation."


def test_scene_image_uses_matching_imported_detail_as_primary_background(episode,monkeypatch):
    from story_channel.background_library import BackgroundLibrary
    from test_location_details import png
    world,project,pipeline = episode
    board = pipeline.artifact("storyboard")
    scene = board.scenes[0]
    parent = AssetPipeline(world.manager,world.project_id)
    BackgroundLibrary(parent).add_detail_reference(scene.location_id,"Window area",png(),
        detail_id="window_area",object_ids=["window"])
    Episodes(world.manager,world.project_id).refresh_assets(project.project_id)
    engine = AssetPipeline(world.manager,project.project_id)
    scene.target_area = "window_area"
    original = engine.creative.artifact
    monkeypatch.setattr(engine.creative,"artifact",lambda name:board if name == "storyboard" else original(name))
    state = engine.load()
    task = next(task for task in engine.tasks(state) if task.stage == "scene_images" and task.subject == scene.scene_id)
    backgrounds = [key for key in task.refs if key.startswith("backgrounds:")]
    assert backgrounds[0] == f"backgrounds:{scene.location_id}__detail_window_area"
    assert len(backgrounds) >= 2 and "panorama" in backgrounds[1]
    assert "primary background plate" in task.prompt
    assert engine.local_path(state.records[backgrounds[0]].asset.file_path).is_file()
