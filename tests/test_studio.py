from asset_test_helpers import complete_character_parts
import pytest
from story_channel.config import Settings
from story_channel.projects import ProjectManager
from story_channel.pipeline import Pipeline, ApprovalRequired
from story_channel.studio import WorldStudio, Episodes, approve_content, content_digest
from story_channel.files import input_hash


@pytest.fixture
def world(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    manifest = WorldStudio.create(manager, "An island where memories power lighthouses.")
    studio = WorldStudio(manager, manifest.project_id)
    studio.review()
    studio.resolve_base("revert", "", expected=input_hash(studio.load().base_text))
    studio.approve_base(expected=input_hash(studio.load().base_text))
    return studio


def test_world_is_immutable_and_additions_are_reviewed_and_append_only(world):
    with pytest.raises(ValueError, match="immutable"):
        world.edit_base("A different universe")
    with pytest.raises(ApprovalRequired):
        world.pipeline.generate("world", force=True)
    item = world.add_setting("Lighthouses can store memories only at night.")
    with pytest.raises(ValueError, match="review"):
        world.approve_setting(item.setting_id, text=item.text, decision="revert")
    world.review(setting_id=item.setting_id)
    world.approve_setting(item.setting_id, text=item.text, decision="revert")
    assert world.load().additions[0].approval
    with pytest.raises(ValueError):
        world.edit_setting(item.setting_id, remove=True)
    assert world.pipeline.inspect().steps["world"].status == "succeeded"


def test_world_approval_rebuilds_a_preview_after_saved_settings_change(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    manifest = WorldStudio.create(manager, "An island where memories power lighthouses.")
    studio = WorldStudio(manager, manifest.project_id)
    studio.pipeline.generate("world")
    original_revision = manager.load(manifest.project_id).steps["world"].revision
    studio.edit_base("An island where memories power lighthouses. Memories cannot be created or duplicated.")
    studio.review()
    studio.resolve_base("revert", "", expected=input_hash(studio.load().base_text))
    studio.approve_base(expected=input_hash(studio.load().base_text))
    current = manager.load(manifest.project_id).steps["world"]
    assert current.revision == original_revision + 1
    assert current.input_hash == studio.pipeline._fingerprint("world")
    assert studio.require_approved()


def test_episode_inputs_and_approvals_are_isolated_from_shared_world(world):
    world.generate_characters()
    episodes = Episodes(world.manager, world.project_id)
    first = episodes.create("First episode", "The keeper saves a memory.", 120)
    second = episodes.create("Second episode", "The keeper helps a visitor.", 240)
    flow = Pipeline(world.manager, first.project_id)
    with pytest.raises(ApprovalRequired, match="Outline"):
        flow.generate("story")
    approve_content(flow, "outline", expected=content_digest(flow, "outline"))
    flow.generate("story")
    flow.generate("review")
    flow.approve("story")
    flow.generate("script")
    with pytest.raises(ApprovalRequired, match="Script"):
        flow.generate("storyboard")
    approve_content(flow, "script", expected=content_digest(flow, "script"))
    flow.generate("storyboard")
    flow.approve("storyboard")
    flow.generate("prompts")
    episodes.save_outline(first.project_id, "Revised", "A different rescue.", 120)
    manifest = flow.inspect()
    assert manifest.steps["world"].status == manifest.steps["characters"].status == "succeeded"
    assert manifest.steps["story"].status == "stale"
    assert not manifest.content_approvals
    assert world.manager.read_outline(second.project_id) == "The keeper helps a visitor."
    assert world.require_approved()


def test_world_backgrounds_are_available_without_story_and_have_one_panorama_only(world):
    world.generate_characters()
    from story_channel.assets import AssetPipeline
    engine = AssetPipeline(world.manager, world.project_id)
    engine.set_generic_prompt("Storybook animation")
    state = engine.inspect()
    backgrounds = [t for t in engine.tasks(state) if t.stage == "backgrounds"]
    assert len(backgrounds) == len(world.pipeline.artifact("world").locations)
    assert sum(t.operation == "panorama_view" for t in backgrounds) == 0
    engine.generate("character_references")
    complete_character_parts(engine)
    engine.approve("characters")
    engine.generate("backgrounds")
    engine.approve("backgrounds")
    assert engine.inspect().next_stage == "complete"


def test_addition_conflicts_block_approval_and_other_approvals_stale_pending_reviews(world, monkeypatch):
    from story_channel.studio import TextReview, TextAdvice
    item = world.add_setting("Memories can be copied without cost.")
    world.review(setting_id=item.setting_id)
    original = (world.path / "world.json").read_bytes()
    def conflict(pipeline, text, context, **kwargs):
        return TextReview(source_hash=input_hash(text), context_hash=input_hash(context),
            advice=TextAdvice(proposed_text=text, conflicts=["Contradicts the approved memory rules"]))
    with monkeypatch.context() as patch:
        patch.setattr("story_channel.studio.generate_advice", conflict)
        with pytest.raises(ApprovalRequired, match="Conflicts"):
            world.approve_setting(item.setting_id, text=item.text, decision="revert")
    assert not world.load().additions[0].approval
    assert (world.path / "world.json").read_bytes() == original
    second = world.add_setting("Only the keeper can open the lighthouse vault.")
    world.review(setting_id=second.setting_id)
    world.approve_setting(second.setting_id, text=second.text, decision="revert")
    with pytest.raises(ValueError, match="current canon"):
        world.approve_setting(item.setting_id, text=item.text, decision="revert")


def test_additional_locations_preserve_base_and_existing_episode_snapshots(world, monkeypatch):
    from story_channel.studio import WorldDelta, TextReview, TextAdvice
    from story_channel.creative import Location
    world.generate_characters()
    service = Episodes(world.manager, world.project_id)
    old_episode = service.create("Old", "An old rescue", 60)
    old_path = world.manager.project_path(old_episode.project_id)
    old_world = (old_path / "world.json").read_bytes()
    base = (world.path / "world_base.json").read_bytes()
    item = world.add_setting("There is an ancient harbor under the lighthouse.")
    world.review(setting_id=item.setting_id)
    def advice(pipeline, text, context, **kwargs):
        return TextReview(source_hash=input_hash(text), context_hash=input_hash(context), advice=TextAdvice(proposed_text=text))
    world.pipeline.demo = False
    monkeypatch.setattr("story_channel.studio.generate_advice", advice)
    monkeypatch.setattr("story_channel.modification.modify_content", lambda *a, **kw: WorldDelta(locations=[
        Location(location_id="ancient_harbor", name="Ancient harbor", description="A hidden stone harbor below the lighthouse")]))
    world.approve_setting(item.setting_id, text=item.text, decision="revert")
    assert (world.path / "world_base.json").read_bytes() == base
    assert world.require_approved()
    service.sync(old_episode.project_id)
    assert (old_path / "world.json").read_bytes() == old_world
    new_episode = service.create("New", "Explore the ancient harbor", 60)
    assert any(loc.location_id == "ancient_harbor" for loc in Pipeline(world.manager, new_episode.project_id).artifact("world").locations)
    service.refresh_world(old_episode.project_id)
    assert (old_path / "world.json").read_bytes() != old_world
    assert not world.manager.load(old_episode.project_id).content_approvals


def test_shared_canon_keeps_its_model_fingerprint_and_failed_character_addition_preserves_cast(world):
    from story_channel.models import ProviderConfig
    world.generate_characters()
    before = (world.path / "characters.json").read_bytes()
    viewer = Pipeline(world.manager, world.project_id, llm_config=ProviderConfig(provider="demo", model="another-model"))
    assert viewer.inspect().steps["world"].status == "succeeded"
    assert viewer.inspect().steps["characters"].status == "succeeded"
    class Fails:
        last_usage = {}
        def generate(self, *args):
            raise RuntimeError("Injected character request failure")
    world.pipeline.provider = Fails()
    with pytest.raises(RuntimeError, match="character request failure"):
        world.generate_characters("A rival keeper")
    assert (world.path / "characters.json").read_bytes() == before
    assert viewer.inspect().steps["characters"].status == "succeeded"


def test_existing_story_import_keeps_original_outline_files_and_usage(tmp_path):
    from test_pipeline import complete
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    source = tmp_path / "outline.md"
    source.write_text("The keeper restores a lost memory.", encoding="utf-8")
    project = manager.create("Legacy story", source, target_duration_sec=30)
    old = Pipeline(manager, project.project_id)
    complete(old)
    snapshots = {name: (old.path / name).read_bytes() for name in ("story.json", "script.json", "scenes.json", "usage.json")}
    studio = WorldStudio(manager, project.project_id)
    studio.edit_base("A coastal world with memory-powered lighthouses.")
    studio.review()
    studio.resolve_base("revert", "", expected=input_hash(studio.load().base_text))
    studio.approve_base(expected=input_hash(studio.load().base_text))
    episode = Episodes(manager, project.project_id).adopt_existing()
    path = manager.project_path(episode.project_id)
    assert manager.read_outline(episode.project_id) == source.read_text(encoding="utf-8")
    assert all((old.path / name).read_bytes() == content for name, content in snapshots.items())
    assert all((path / name).read_bytes() == snapshots[name] for name in ("story.json", "script.json", "scenes.json"))
    assert not (path / "usage.json").exists()
    assert Pipeline(manager, episode.project_id).inspect().steps["storyboard"].status == "succeeded"
