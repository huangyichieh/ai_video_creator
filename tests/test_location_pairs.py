import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from story_channel import location_pairs as pairs
from story_channel.dual_panorama import approved,MASTERS
from story_channel.location_bridges import BRIDGES
from story_channel.media_providers import ThreadLocalUsage,OpenAIImageProvider
from story_channel.models import ProviderConfig
from story_channel.llm import ProviderError
from test_dual_panorama import prepare,Sectors
from test_studio import world
from test_scene_production import episode


def selected(engine,lid,group):
    state = engine.load()
    return {name:state.records[f"backgrounds:{lid}__{name}"].asset.asset_id for name in pairs.names(group)}


def test_pair_prompts_approval_and_revision_application_are_atomic(world):
    engine,lid,provider = prepare(world)
    original = selected(engine,lid,"bridges")
    old_pair = pairs.versions(engine,lid,"bridges")[0]
    state = engine.load()
    state.generation_inputs["backgrounds:"+lid] = {"mode":"text","prompt":"OBSOLETE_HIDDEN_STYLE_PROMPT"}
    engine._save(state)
    state = pairs.generate(engine,lid,"bridges","Soft stylized office, warm palette")
    assert state.location_pair_prompts[lid]["bridges"] == "Soft stylized office, warm palette"
    assert all("Soft stylized office" in prompt for prompt,_ in provider.calls[-2:])
    assert all("OBSOLETE_HIDDEN_STYLE_PROMPT" not in prompt for prompt,_ in provider.calls[-2:])
    assert state.records[f"backgrounds:{lid}__east_bridge"].asset.generation_params["backgroundContext"]["sourcePrompt"] == "Soft stylized office, warm palette"
    assert "STYLE ONLY" in provider.calls[-1][0]
    assert "east_bridge" in provider.calls[-1][1][2].parent.name
    assert pairs.current(engine,state,lid,"bridges")
    assert len(pairs.versions(engine,lid,"bridges")) == 2
    assert all(not approved(engine,state,lid,name) for name in (*BRIDGES,*MASTERS))
    with pytest.raises(ValueError,match="changed"):
        pairs.approve(engine,lid,"bridges",expected=original)
    state = pairs.approve(engine,lid,"bridges",expected=selected(engine,lid,"bridges"))
    assert all(approved(engine,state,lid,name) for name in BRIDGES)
    state = pairs.apply(engine,lid,"bridges",old_pair["id"])
    assert selected(engine,lid,"bridges") == original
    assert all(not approved(engine,state,lid,name) for name in BRIDGES)
    pairs.approve(engine,lid,"bridges",expected=original)


def test_native_sizes_and_master_prefill_protect_known_bridge_pixels(world):
    engine,lid,provider = prepare(world)
    state = engine.load()
    for name in BRIDGES:
        assert Image.open(engine.local_path(state.records[f"backgrounds:{lid}__{name}"].asset.file_path)).size == (1536,1024)
    for name in MASTERS:
        asset = state.records[f"backgrounds:{lid}__{name}"].asset
        assert Image.open(engine.local_path(asset.file_path)).size == (2560,1440)
        metadata = asset.generation_params["masterPrefill"]
        assert metadata["protectedPixelsVerified"]
        with Image.open(engine.local_path(metadata["canvas"])) as image:
            known = np.asarray(image.convert("RGB"))
        with Image.open(engine.local_path(metadata["mask"])) as image:
            protected = np.asarray(image.getchannel("A")) == 255
        actual = np.asarray(Image.open(engine.local_path(asset.file_path)))
        assert .34 < protected.mean() < .36
        assert metadata["featherPixelCount"] > 0
        assert np.array_equal(actual[protected],known[protected])
        assert asset.generation_params["config"]["size"] == "2560x1440"
    # The first input is the prefilled canvas; both requests carry the same bridge anchors.
    assert provider.calls[2][1][3:5] == provider.calls[3][1][3:5]


def test_partial_pair_cannot_be_approved_and_resume_reuses_successful_image(world):
    engine,lid,_ = prepare(world)
    class Failing(ThreadLocalUsage):
        def __init__(self):
            self.calls = []
            self.fail = True
            self.base = Sectors()
        def generate(self,prompt,refs,config):
            self.calls.append(Path(config.options["output_path"]).parent.name)
            self.last_usage = {"image_count":1}
            if self.fail and self.calls[-1].endswith("west_bridge"):
                raise ProviderError("Mock west failure")
            return self.base.generate(prompt,refs,config)
    provider = Failing()
    engine.panorama_image_provider = provider
    with pytest.raises(ProviderError,match="Mock west failure"):
        pairs.generate(engine,lid,"bridges","New pair style")
    assert not pairs.current(engine,engine.load(),lid,"bridges")
    assert engine.load().next_stage == "backgrounds"
    with pytest.raises(ValueError,match="incomplete"):
        pairs.approve(engine,lid,"bridges",expected=selected(engine,lid,"bridges"))
    assert len(pairs.versions(engine,lid,"bridges")) == 1
    successful_id = selected(engine,lid,"bridges")["east_bridge"]
    provider.fail = False
    state = engine.generate("backgrounds",subjects=[lid+"__"+name for name in BRIDGES])
    assert pairs.current(engine,state,lid,"bridges")
    assert len(provider.calls) == 3
    assert selected(engine,lid,"bridges")["east_bridge"] == successful_id


def test_failed_revision_write_does_not_expose_an_incomplete_pair(world,monkeypatch):
    engine,lid,_ = prepare(world)
    import story_channel.assets as assets
    original = assets.atomic_write
    def fail_west_history(path,text):
        if "history/assets/backgrounds" in path.as_posix() and path.parent.name.endswith("west_bridge"):
            raise OSError("Mock revision storage failure")
        return original(path,text)
    monkeypatch.setattr(assets,"atomic_write",fail_west_history)
    with pytest.raises(ProviderError,match="Mock revision storage failure"):
        pairs.generate(engine,lid,"bridges","Same style for both")
    assert not pairs.current(engine,engine.load(),lid,"bridges")
    assert len(pairs.versions(engine,lid,"bridges")) == 1


def test_older_independent_revisions_can_be_selected_and_applied_as_a_pair(world):
    from story_channel.assets import AssetPipeline
    from story_channel.background_library import BackgroundLibrary
    from story_channel.floor_plan_guides import publish_guides
    world.generate_characters()
    engine = AssetPipeline(world.manager,world.project_id,demo=True,image_provider=Sectors())
    lid = BackgroundLibrary(engine).add("Office","Office",reference_type="KEY_360",floor_plan="Fixed center",
        object_layout={"desk":"east wall"}).location_id
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    original = {}
    for name in BRIDGES:
        state = engine.generate("backgrounds",subject=lid+"__"+name)
        original[name] = state.records[f"backgrounds:{lid}__{name}"].asset.asset_id
    for name in BRIDGES:
        engine.generate("backgrounds",subject=lid+"__"+name,force=True)
    pairs.generate(engine,lid,"bridges","A new paired style")
    state = pairs.apply_legacy(engine,lid,"bridges",original)
    assert selected(engine,lid,"bridges") == original
    assert pairs.current(engine,state,lid,"bridges")
    assert all(not approved(engine,state,lid,name) for name in BRIDGES)


def test_image_adapter_forwards_native_size_and_mask(tmp_path):
    canvas,mask,output = [tmp_path/name for name in ("canvas.png","mask.png","output.png")]
    Image.new("RGB",(2560,1440)).save(canvas)
    Image.new("RGBA",(2560,1440),(255,255,255,0)).save(mask)
    buffer = BytesIO()
    Image.new("RGB",(2560,1440)).save(buffer,format="PNG")
    seen = []
    def edit(**kwargs):
        assert kwargs["size"] == "2560x1440"
        assert Path(kwargs["mask"].name) == mask
        assert Path(kwargs["image"][0].name) == canvas
        seen.append(kwargs["prompt"])
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(buffer.getvalue()).decode())],usage=None)
    client = SimpleNamespace(images=SimpleNamespace(edit=edit))
    provider = OpenAIImageProvider(client=client)
    provider.generate("Fill only missing regions",[canvas],ProviderConfig(provider="openai",model="gpt-image-2.5-flare",
        options={"output_path":str(output),"size":"2560x1440","edit_mask":str(mask)}))
    assert seen == ["Fill only missing regions"] and output.is_file()


def test_pair_ui_has_one_prompt_and_shared_approval_revision_controls(world,monkeypatch):
    from streamlit.testing.v1 import AppTest
    engine,lid,_ = prepare(world)
    original = selected(engine,lid,"bridges")
    for key,value in {"STORY_DATA_ROOT":str(world.manager.root),"STORY_LLM_PROVIDER":"demo",
                      "STORY_ASSET_PROVIDER":"demo","OPENAI_ADMIN_KEY":""}.items():
        monkeypatch.setenv(key,value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"),default_timeout=30).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    prompt_key = f"location-pair-prompt-{world.project_id}-{lid}-bridges"
    assert len([field for field in app.text_area if field.key == prompt_key]) == 1
    assert not any(button.key == f"approve-location-master-{world.project_id}-{lid}-east_bridge" for button in app.button)
    app.text_area(key=prompt_key).set_value("Paired blue palette")
    app.button(key=f"generate-location-pair-{lid}-bridges").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=25)
    app.run()
    assert not app.exception
    assert all(selected(engine,lid,"bridges")[name] != original[name] for name in BRIDGES)
    review = next(item for item in app.checkbox if item.key and item.key.startswith(f"location-pair-review-{world.project_id}-{lid}-bridges-"))
    review.check().run()
    app.button(key=f"approve-location-pair-{lid}-bridges").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=25)
    app.run()
    assert not app.exception
    assert app.button(key=f"approve-location-pair-{lid}-bridges").label == "Approved"
    app.button(key=f"revisions-location-pair-{lid}-bridges").click().run()
    assert not app.exception and len(app.get("dialog")[0].image) == 2
    key = f"location-pair-revision-choice-{world.project_id}-{lid}-bridges"
    app.selectbox(key=key).set_value(1).run()
    app.button(key="apply-location-pair-revision").click().run()
    assert not app.exception
    assert selected(engine,lid,"bridges") == original
    assert app.button(key="apply-location-pair-revision").label == "Applied"


def test_episode_import_retains_shared_pair_prompts_and_prefill_metadata(episode):
    from story_channel.assets import AssetPipeline
    from story_channel.background_library import BackgroundLibrary
    from story_channel.floor_plan_guides import publish_guides
    from story_channel.dual_panorama import approve_views
    from story_channel.studio import Episodes
    from test_floor_plan_guides import plan
    world,child,pipeline = episode
    engine = AssetPipeline(world.manager,world.project_id,demo=True)
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    library = BackgroundLibrary(engine)
    library.configure_references(lid,"KEY_360","North printer; south door; east window",
        {"printer":"north","door":"south","window":"east"},camera_plan=plan().model_dump())
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    for group in ("bridges","masters"):
        pairs.generate(engine,lid,group,"Consistent muted blue palette for "+group)
        pairs.approve(engine,lid,group,expected=selected(engine,lid,group))
    engine.generate("backgrounds",subject=lid)
    approve_views(engine,lid)
    Episodes(world.manager,world.project_id).refresh_assets(child.project_id)
    target = AssetPipeline(world.manager,child.project_id,demo=True)
    state = target.inspect()
    assert state.location_pair_prompts == engine.load().location_pair_prompts
    assert target._stage_done("backgrounds",state,target.tasks(state))
    prefill = state.records[f"backgrounds:{lid}__front_panorama"].asset.generation_params["masterPrefill"]
    assert target.local_path(prefill["canvas"]).is_file()
    assert target.local_path(prefill["mask"]).is_file()
