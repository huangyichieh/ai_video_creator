from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.visual_generation import BACKGROUND_EMPTY
from test_studio import world


def prepare(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.generate("character_references")
    location = BackgroundLibrary(engine).add("Office", "Desk beside a window", reference_type="ONE_TIME",
        floor_plan="Fixed center; north windows and west door", object_layout={"desk": "east wall, two metres from center"})
    return engine, location.location_id


def test_requests_include_saved_layout_exclude_characters_and_regenerate_single_view(world):
    engine, lid = prepare(world)
    calls = []
    original = engine.image_provider.generate
    def generate(prompt, refs, config):
        calls.append((prompt, refs))
        return original(prompt, refs, config)
    engine.image_provider.generate = generate
    state = engine.generate("backgrounds", subject=lid)
    character_paths = {engine.local_path(r.asset.file_path) for r in state.records.values() if r.stage == "character_references" and r.asset}
    assert len(calls) == 3
    assert all("north windows and west door" in text and "east wall, two metres" in text and BACKGROUND_EMPTY in text for text, _ in calls)
    assert all(not character_paths.intersection(refs) for _, refs in calls)
    calls.clear()
    state = engine.generate("backgrounds", subject=lid + "__focal", force=True)
    assert len(calls) == 1
    assert state.records["backgrounds:" + lid + "__focal"].revision == 2
    assert state.records["backgrounds:" + lid + "__master_wide"].revision == 1
    assert state.records["backgrounds:" + lid + "__reverse"].revision == 1
    calls.clear()
    from story_channel.visual_generation import VisualGeneration
    VisualGeneration(engine).design_reference("backgrounds", lid, provider=engine.image_provider)
    assert "north windows and west door" in calls[0][0] and "east wall, two metres" in calls[0][0]
    assert BACKGROUND_EMPTY in calls[0][0]


def test_each_background_image_has_actions_and_revisions_open_in_dialog(world, monkeypatch):
    engine, lid = prepare(world)
    engine.generate("backgrounds", subject=lid)
    for key, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                       "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{engine.project_id}"] = "Background"
    app.run()
    for view in ("master_wide", "reverse", "focal"):
        subject = lid + "__" + view
        assert app.button(key=f"background-revisions-{engine.project_id}-{subject}").label == "View / apply saved revisions"
        assert app.button(key=f"background-regenerate-{engine.project_id}-{subject}").label == "Regenerate"
    subject = lid + "__focal"
    app.button(key=f"background-regenerate-{engine.project_id}-{subject}").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=15)
    app.run()
    assert not app.exception
    app.button(key=f"background-revisions-{engine.project_id}-{subject}").click().run()
    assert not app.exception
    assert app.get("dialog")
    prefix = f"reference-revisions-{engine.project_id}-backgrounds-{subject}"
    assert app.selectbox(key=prefix + "-choice")
    assert any(exp.label == "Revision generation details" for exp in app.expander)
    assert not any(exp.label == "View / apply saved revisions" for exp in app.expander)
    app.button(key=prefix + "-delete").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=15)
    app.run()
    assert not app.exception
    app.button(key="close-background-revisions").click().run()
    assert not app.exception
    assert "background-revisions-target" not in app.session_state
