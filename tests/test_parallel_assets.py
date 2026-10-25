from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest

from story_channel.assets import AssetPipeline
from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.media_providers import DemoImageProvider
from story_channel.pipeline import Pipeline
from story_channel.projects import ProjectManager


def cast(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    outline = tmp_path / "outline.md"
    outline.write_text("Eight friends restore the beacon.", encoding="utf-8")
    project = manager.create("Parallel assets", outline, target_duration_sec=30)
    flow = Pipeline(manager, project.project_id)
    flow.generate("world")
    flow.generate("characters")
    characters = flow.artifact("characters")
    first = characters.characters[0]
    characters.characters = [first.model_copy(deep=True, update={"character_id": f"cast_{i}", "voice_id": f"voice_{i}"}) for i in range(8)]
    flow.edit("characters", characters.model_dump_json(by_alias=True))
    return manager, project


def scene_batch(tmp_path, *, backgrounds=1):
    from test_assets_ui import prepare
    manager, project = prepare(tmp_path)
    flow = Pipeline(manager, project.project_id)
    if backgrounds > 1:
        world = flow.artifact("world")
        template = world.locations[0]
        world.locations = [template] + [template.model_copy(update={"location_id": f"place_{i}", "name": f"Place {i}"})
                                        for i in range(1, backgrounds)]
        flow.edit("world", world.model_dump_json(by_alias=True))
    flow.run()
    flow.approve("story")
    flow.run()
    board = flow.artifact("storyboard")
    template = board.scenes[0]
    locations = flow.artifact("world").locations
    board.scenes = [template.model_copy(deep=True, update={"scene_id": f"scene_{i}", "order": i + 1,
        "duration_sec": 30 / 8, "location_id": locations[i % len(locations)].location_id}) for i in range(8)]
    flow.edit("storyboard", board.model_dump_json(by_alias=True))
    flow.approve("storyboard")
    flow.run()
    return manager, project


def test_five_parallel_requests_stop_queue_resume_and_isolate_usage(tmp_path):
    manager, project = scene_batch(tmp_path)
    release, five_started, stop = Event(), Event(), Event()
    guard = Lock()

    class BlockingImages(DemoImageProvider):
        active = peak = 0

        def __init__(self):
            self.calls = []

        def generate(self, prompt, references, config):
            index = int(Path(config.options["output_path"]).parent.name.split("_")[-1])
            self.last_usage = {"image_count": 1, "input_tokens": index + 1, "output_tokens": (index + 1) * 2}
            with guard:
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.calls.append(index)
                if self.active == 5:
                    five_started.set()
            try:
                assert release.wait(timeout=20)
                return super().generate(prompt, references, config)
            finally:
                with guard:
                    self.active -= 1

    prerequisites = AssetPipeline(manager, project.project_id)
    prerequisites.set_generic_prompt("Watercolor adventure")
    prerequisites.generate("character_references")
    prerequisites.approve("characters")
    prerequisites.generate("backgrounds")
    prerequisites.approve("backgrounds")
    provider = BlockingImages()
    engine = AssetPipeline(manager, project.project_id, image_provider=provider, stop_requested=stop.is_set)
    engine.set_generic_prompt("Watercolor adventure")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.generate, "scene_images")
        try:
            assert five_started.wait(timeout=10)
            state = engine.load()
            assert state.generation_active == 5
            assert state.generation_queued == 3
            assert len([r for r in state.records.values() if r.status == "running"]) == 5
            stop.set()
            release.set()
            state = future.result(timeout=20)
        finally:
            release.set()
    assert provider.peak == 5
    assert len(provider.calls) == 5
    assert state.generation_active == 0
    assert state.generation_finished == 5
    assert state.generation_queued == 3
    assert state.stopped
    usage = [r for r in CostTracker(manager, project.project_id).records() if r.step == "assets:scene_images"]
    assert sorted(r.input_tokens for r in usage) == sorted(i + 1 for i in provider.calls)
    assert all(r.output_tokens == r.input_tokens * 2 and r.image_count == 1 for r in usage)
    first_assets = {key: r.asset.asset_id for key, r in state.records.items()}
    stop.clear()
    state = engine.generate("scene_images")
    assert len(provider.calls) == 8
    assert state.next_stage == "voices"
    assert state.generation_finished == 8
    assert state.generation_active == state.generation_queued == 0
    assert all(state.records[key].asset.asset_id == asset for key, asset in first_assets.items())
    assert len([r for r in CostTracker(manager, project.project_id).records() if r.step == "assets:scene_images"]) == 8


def test_parallel_limit_cannot_exceed_five(tmp_path):
    manager, project = cast(tmp_path)
    with pytest.raises(ValueError, match="between 1 and 5"):
        AssetPipeline(manager, project.project_id, max_tasks=6)


@pytest.mark.parametrize("stage", ["character_references", "backgrounds"])
def test_reference_stages_run_one_at_a_time_and_resume_after_stop(tmp_path, stage):
    manager, project = cast(tmp_path) if stage == "character_references" else scene_batch(tmp_path, backgrounds=3)
    first_started, release, stop = Event(), Event(), Event()
    guard = Lock()
    class SerialImages(DemoImageProvider):
        active = peak = calls = 0
        def generate(self, prompt, references, config):
            with guard:
                self.active += 1
                self.calls += 1
                self.peak = max(self.peak, self.active)
            first_started.set()
            try:
                assert release.wait(timeout=20)
                return super().generate(prompt, references, config)
            finally:
                with guard:
                    self.active -= 1
    if stage == "backgrounds":
        prerequisites = AssetPipeline(manager, project.project_id)
        prerequisites.set_generic_prompt("Watercolor adventure")
        prerequisites.generate("character_references")
        prerequisites.approve("characters")
    provider = SerialImages()
    engine = AssetPipeline(manager, project.project_id, image_provider=provider, stop_requested=stop.is_set)
    engine.set_generic_prompt("Watercolor adventure")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.generate, stage)
        try:
            assert first_started.wait(timeout=10)
            state = engine.load()
            total = state.generation_total
            assert total > 1
            assert state.max_parallel_tasks == state.generation_active == 1
            assert state.generation_queued == total - 1
            stop.set()
            release.set()
            state = future.result(timeout=20)
        finally:
            release.set()
    assert provider.peak == provider.calls == state.generation_finished == 1
    assert state.stopped
    stop.clear()
    state = engine.generate(stage)
    assert provider.peak == 1
    assert provider.calls == total
    assert state.generation_active == state.generation_queued == 0
    assert not state.stopped
