from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from threading import Event

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.locking import project_lock
from test_studio import world
from test_character_visual_costumes import image_bytes


def test_visual_inputs_save_while_project_is_locked_and_survive_worker_writes(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    state = engine.inspect()
    before = engine.state_path.read_bytes()
    with project_lock(engine.path):
        updated = engine.set_style_prompt("character_references", cid, "Watercolor")
        assert updated.style_prompts["character_references:" + cid] == "Watercolor"
        updated = engine.set_character_image(cid, image_bytes())
        reference = updated.character_images[cid]
        assert engine.character_image(updated, cid).read_bytes() == image_bytes()
        assert engine.state_path.read_bytes() == before
        # A worker finishing with its original snapshot must not lose saved edits.
        engine._save(state)
        assert engine.load().character_images[cid] == reference
        assert engine.load().style_prompts["character_references:" + cid] == "Watercolor"
        updated = engine.set_character_image(cid, None)
        assert cid not in updated.character_images
    assert cid not in engine.inspect().character_images


def test_running_request_uses_original_inputs_and_next_request_uses_saved_edits(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    engine.set_style_prompt("character_references", cid, "Original pencil style")
    engine.set_character_image(cid, image_bytes("blue"))
    original_reference = engine.load().character_images[cid]
    started, release = Event(), Event()
    calls = []
    original = engine.image_provider.generate
    def generate(prompt, references, config):
        calls.append((prompt, list(references)))
        if len(calls) == 1:
            started.set()
            if not release.wait(15):
                raise RuntimeError("Test did not release generation")
        return original(prompt, references, config)
    engine.image_provider.generate = generate
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.generate, "character_references", subject=cid)
        try:
            assert started.wait(10)
            editor = AssetPipeline(world.manager, world.project_id, demo=True)
            editor.set_style_prompt("character_references", cid, "New watercolor style")
            edited = editor.set_character_image(cid, image_bytes("red"))
            new_reference = edited.character_images[cid]
            assert new_reference != original_reference
        finally:
            release.set()
        result = future.result(timeout=10)
    record = result.records["character_references:" + cid]
    assert record.status == "stale"
    assert record.asset.generation_params["characterImage"] == original_reference
    # Image mode uses the captured design image, not the legacy style text.
    assert "from @design" in calls[0][0]
    assert engine.local_path(original_reference["file"]) in calls[0][1]
    result = engine.generate("character_references", subject=cid)
    assert result.records["character_references:" + cid].status == "succeeded"
    assert "from @design" in calls[1][0]
    assert engine.local_path(new_reference["file"]) in calls[1][1]


def test_character_visual_fields_remain_enabled_during_background_work(world, monkeypatch):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    engine.set_character_image(cid, image_bytes())
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    pending = Future()
    app.session_state["workspace-task"] = {"future": pending, "label": "Generating test asset", "inline": True,
        "dialog_owner": None, "progress_path": None}
    try:
        app.run()
        prefix = f"visual-generation-{world.project_id}-character_references-{cid}"
        app.button(key=prefix + "-text").click().run()
        assert not app.text_area(key=prefix + "-prompt").disabled
        with project_lock(engine.path):
            app.text_area(key=prefix + "-prompt").set_value("Changed while generating").run()
            app.button(key=prefix + "-image").click().run()
            uploads = [element for element in app.get("file_uploader") if element.proto.label == "Design reference image"]
            assert uploads and all(not element.proto.disabled for element in uploads)
            assert not app.button(key=prefix + "-remove").disabled
            app.button(key=prefix + "-remove").click().run()
        assert not app.exception
        assert engine.load().generation_inputs["character_references:" + cid]["prompt"] == "Changed while generating"
        assert cid not in engine.load().character_images
    finally:
        app.session_state["workspace-task"] = None


def test_newly_queued_character_uses_edits_saved_during_another_request(world):
    world.generate_characters()
    world.generate_characters("Guest")
    first, second = [c.character_id for c in world.pipeline.artifact("characters").characters]
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    started, release = Event(), Event()
    calls = []
    original = engine.image_provider.generate
    def generate(prompt, references, config):
        calls.append((prompt, list(references)))
        if len(calls) == 1:
            started.set()
            if not release.wait(15):
                raise RuntimeError("Test did not release generation")
        return original(prompt, references, config)
    engine.image_provider.generate = generate
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.generate, "character_references", subject=first)
        try:
            assert started.wait(10)
            editor = AssetPipeline(world.manager, world.project_id, demo=True)
            editor.set_style_prompt("character_references", second, "Queued watercolor style")
            reference = editor.set_character_image(second, image_bytes("red")).character_images[second]
            assert engine.enqueue("character_references", second)
        finally:
            release.set()
        result = future.result(timeout=10)
    assert len(calls) == 2
    assert "from @design" in calls[1][0]
    assert engine.local_path(reference["file"]) in calls[1][1]
    assert all(result.records["character_references:" + cid].status == "succeeded" for cid in (first, second))
