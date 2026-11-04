import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.creative import Storyboard,Location,StoryboardProp
from story_channel.models import Scene,StepState
from story_channel.modules import validate_output,VisualPromptAdapter
from story_channel.pipeline import Pipeline
from story_channel.studio import Episodes
from story_channel.storyboard_assets import sync,requests,asset_catalog
from story_channel.files import atomic_write,input_hash
from story_channel.identity import IdentityManager
from test_studio import world


@pytest.fixture
def board(world):
    world.generate_characters()
    episode = Episodes(world.manager,world.project_id).create("New places","A station departure",60)
    character = world.pipeline.artifact("characters").characters[0].character_id
    output = Storyboard(scenes=[Scene(scene_id="scene_01",order=1,duration_sec=60,location_id="station_01",
        character_ids=[character],action="Pick up the suitcase",shot_type="wide",camera_movement="static",
        visual_description="A station platform",prop_ids=["suitcase_01"],sfx=["Train wheels on rails"," Train   wheels on rails "])],
        backgrounds=[Location(location_id="station_01",name="Station platform",description="Covered platform, benches and tracks")],
        props=[StoryboardProp(prop_id="suitcase_01",title="Suitcase",description="A worn leather case",modes=["closed","open"])])
    path = world.manager.project_path(episode.project_id)/"scenes.json"
    atomic_write(path,output.model_dump_json(by_alias=True,indent=2))
    episode.steps["storyboard"] = StepState(status="succeeded",output_hash=input_hash(output.model_dump(mode="json",by_alias=True)))
    world.manager.save(episode)
    return world,episode,output


def test_registers_pending_background_props_sfx_with_sources_and_no_canon_edits(board):
    world,episode,output = board
    original = (world.path/"world.json").read_bytes()
    rows = sync(world.manager,world.project_id)
    assert {row["kind"] for row in rows} == {"background","prop","sfx"}
    assert len(rows) == 3
    assert all(row["sources"][0]["episodeId"] == episode.project_id for row in rows)
    catalog = json.loads((world.path/"background_library.json").read_text(encoding="utf-8"))
    assert catalog["added"][0]["locationId"] == "station_01"
    state = AssetPipeline(world.manager,world.project_id,demo=True).inspect()
    assert "station_01" in state.location_reference_sets
    assert not state.background_approvals.get("station_01")
    assert not any(record.asset for key,record in state.records.items() if "station_01" in key)
    library = json.loads((world.path/"library_media.json").read_text(encoding="utf-8"))
    assert library["props"]["suitcase_01"]["modes"] == ["closed","open"]
    assert not library["visuals"]
    identity = IdentityManager(world.manager,world.project_id)
    identity.validate(identity.load())
    assert "suitcase_01" in identity.load().props
    assert (world.path/"world.json").read_bytes() == original


def test_sync_is_idempotent_and_preserves_existing_asset_edits(board):
    world,episode,output = board
    sync(world.manager,world.project_id)
    media_path = world.path/"library_media.json"
    data = json.loads(media_path.read_text(encoding="utf-8"))
    data["props"]["suitcase_01"]["description"] = "User-edited brass fittings"
    atomic_write(media_path,json.dumps(data))
    paths = [world.path/name for name in ("background_library.json","library_media.json","storyboard_asset_requests.json")]
    before = [path.read_bytes() for path in paths]
    sync(world.manager,world.project_id)
    assert before == [path.read_bytes() for path in paths]
    assert len(json.loads(paths[0].read_text(encoding="utf-8"))["added"]) == 1


def test_storyboard_requires_declared_new_location_and_props_and_prompts_support_them(board):
    world,episode,output = board
    context = {"world":world.pipeline.artifact("world").model_dump(mode="json",by_alias=True),
        "characters":world.pipeline.artifact("characters").model_dump(mode="json",by_alias=True),"targetDurationSec":60}
    validate_output("storyboard",output,context)
    invalid = output.model_copy(deep=True)
    invalid.backgrounds = []
    with pytest.raises(ValueError,match="Unknown location"):
        validate_output("storyboard",invalid,context)
    invalid = output.model_copy(deep=True)
    invalid.props = []
    with pytest.raises(ValueError,match="Declare new props"):
        validate_output("storyboard",invalid,context)
    pipeline = Pipeline(world.manager,episode.project_id)
    prompts = pipeline._context("prompts")
    assert any(item["locationId"] == "station_01" for item in prompts["world"]["locations"])
    assert "Covered platform" in VisualPromptAdapter().generate(prompts,"demo","demo").prompts[0].image_prompt
    before = asset_catalog(world.manager,episode.project_id)
    sync(world.manager,world.project_id)
    assert asset_catalog(world.manager,episode.project_id) == before  # Frozen episode catalog is not mutated.


def test_only_committed_storyboards_register_assets(board):
    world,episode,output = board
    episode.steps["storyboard"].status = "running"
    world.manager.save(episode)
    assert sync(world.manager,world.project_id) == []
    episode.steps["storyboard"].status = "succeeded"
    episode.steps["storyboard"].output_hash = "0"*64
    world.manager.save(episode)
    assert sync(world.manager,world.project_id) == []


def test_assets_page_imports_new_drafts_and_shows_sfx_requests(board,monkeypatch):
    world,episode,output = board
    for key,value in {"STORY_DATA_ROOT":str(world.manager.root),"STORY_LLM_PROVIDER":"demo",
                      "STORY_ASSET_PROVIDER":"demo","OPENAI_ADMIN_KEY":""}.items():
        monkeypatch.setenv(key,value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"),default_timeout=25).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    assert len(requests(world.manager,world.project_id)) == 3
    app.session_state[f"page-tab-assets-{world.project_id}"] = "SFX"
    app.run()
    assert not app.exception
    assert any("Title" in frame.value.columns and "Train wheels on rails" in list(frame.value["Title"]) for frame in app.dataframe)
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Props"
    app.run()
    assert not app.exception
    assert any("Suitcase" in item.label for item in app.expander)
