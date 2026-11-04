from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.background_library import BackgroundLibrary
from story_channel.background_request_info import details, hover_text
from test_background_image_actions import prepare
from test_studio import world


def test_info_uses_revision_snapshot_not_current_floor_plan(world):
    engine, lid = prepare(world)
    state = engine.generate("backgrounds", subject=lid)
    key = "backgrounds:" + lid + "__focal"
    record = state.records[key]
    original = details(record)
    assert original["context"]["floorPlan"] == "Fixed center; north windows and west door"
    assert original["context"]["objectLayout"] == {"desk": "east wall, two metres from center"}
    assert original["references"] and original["locationReferences"][0]["file"]
    BackgroundLibrary(engine).configure_references(lid, "ONE_TIME", "New floor plan", {"desk": "south wall"})
    info = details(engine.load().records[key])
    assert info["context"] == original["context"]
    assert "north windows" in hover_text(info) and "east wall" in hover_text(info)
    old = record.model_copy(deep=True)
    old.asset.generation_params.pop("backgroundContext")
    assert details(old)["context"] is None
    assert details(old)["request"] is None  # Demo and older records do not invent a Runway payload.


def test_info_popover_includes_layout_references_and_generation_settings(world, monkeypatch):
    engine, lid = prepare(world)
    engine.generate("backgrounds", subject=lid)
    for key, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                       "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{engine.project_id}"] = "Background"
    app.run()
    assert not any("Source context before" in md.value for md in app.markdown)
    app.session_state[f"background-prompt-info-{engine.project_id}-{lid}__focal"] = True
    app.run()
    assert not app.exception
    labels = [md.value for md in app.markdown]
    assert "**Submitted prompt**" in labels
    assert "**Reference images, in request order**" in labels
    assert "**Floor plan**" in labels and "**Object layout**" in labels and "**Style rules**" in labels
    assert "**App generation settings**" in labels
    assert any("not separate Runway API fields" in caption.value for caption in app.caption)
