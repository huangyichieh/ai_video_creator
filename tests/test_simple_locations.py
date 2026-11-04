from io import BytesIO

from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.background_library import BackgroundLibrary
from story_channel.location_references import LocationReferenceResolver, view_names
from story_channel.simple_locations import save
from test_location_master_pack import master_pack
from test_studio import world


def test_simple_generates_one_master_without_layout_and_requires_approval(master_pack):
    engine,_,provider,_ = master_pack
    library = BackgroundLibrary(engine)
    lid = library.add("Cafe", "A cozy cafe with blue stools", reference_type="SIMPLE").location_id
    state = engine.inspect()
    assert state.location_reference_sets[lid]["reference_type"] == "SIMPLE"
    assert view_names(state.location_reference_sets[lid]) == ["simple_master"]
    assert not state.location_reference_sets[lid]["floor_plan"]
    state = engine.generate("backgrounds",subject=lid)
    assert len(provider.calls) == 1
    assert "blue stools" in provider.calls[0][0]
    assert not provider.calls[0][1]
    resolver = LocationReferenceResolver(engine,state)
    assert resolver.resolve(lid,0) == []
    state = library.approve(lid)
    assert library.approved(lid,state)
    from story_channel.assets_status import world_assets_status_rows
    rows = world_assets_status_rows(engine,state)
    assert any(row["Subject"] == lid and row["Stage"] == "simple_master_approval" and row["Status"] == "Approved" for row in rows)
    resolver = LocationReferenceResolver(engine,state)
    assert resolver.resolve(lid,180) == ["backgrounds:"+lid+"__simple_master"]
    record = state.records["backgrounds:"+lid+"__simple_master"]
    assert record.asset.generation_params["config"]["size"] == "1536x864"
    save(engine,lid,prompt="A cafe with red stools")
    assert not library.approved(lid,engine.inspect())
    assert LocationReferenceResolver(engine,engine.inspect()).resolve(lid,0) == []


def test_simple_revision_application_reopens_approval(master_pack):
    engine,_,provider,_ = master_pack
    library = BackgroundLibrary(engine)
    lid = library.add("Cafe","A cozy cafe",reference_type="SIMPLE").location_id
    subject = lid+"__simple_master"
    state = engine.generate("backgrounds",subject=lid)
    original = state.records["backgrounds:"+subject].asset.asset_id
    save(engine,lid,prompt="A cafe with wooden stools")
    engine.generate("backgrounds",subject=lid)
    library.approve(lid)
    state = engine.apply_revision("backgrounds",subject,original)
    assert len(provider.calls) == 2
    assert not library.approved(lid,state)
    assert state.records["backgrounds:"+subject].asset.asset_id == original
    state = library.approve(lid)
    assert LocationReferenceResolver(engine,state).resolve(lid) == ["backgrounds:"+subject]


def test_simple_approved_reference_is_available_in_episode(master_pack):
    engine,_,_,_ = master_pack
    library = BackgroundLibrary(engine)
    lid = "lighthouse"
    save(engine,lid,reference_type="SIMPLE",prompt="A lighthouse interior")
    engine.generate("backgrounds",subject=lid)
    library.approve(lid)
    from story_channel.studio import Episodes, approve_content, content_digest
    from story_channel.assets import AssetPipeline
    from story_channel.pipeline import Pipeline
    episodes = Episodes(engine.manager,engine.project_id)
    episode = episodes.create("Cafe episode","A visitor enters the cafe.",60)
    pipeline = Pipeline(engine.manager,episode.project_id)
    approve_content(pipeline,"outline",expected=content_digest(pipeline,"outline"))
    pipeline.generate("story")
    pipeline.generate("review")
    pipeline.approve("story")
    pipeline.generate("script")
    approve_content(pipeline,"script",expected=content_digest(pipeline,"script"))
    pipeline.generate("storyboard")
    pipeline.approve("storyboard")
    pipeline.generate("prompts")
    approve_content(pipeline,"prompts",expected=content_digest(pipeline,"prompts"))
    episodes.import_assets(episode.project_id)
    target = AssetPipeline(engine.manager,episode.project_id,demo=True)
    state = target.inspect()
    assert BackgroundLibrary(target).approved(lid,state)
    assert LocationReferenceResolver(target,state).resolve(lid) == ["backgrounds:"+lid+"__simple_master"]
    assert any("backgrounds:"+lid+"__simple_master" in task.refs for task in target.tasks(state) if task.stage == "scene_images")


def test_simple_guidance_style_and_type_switch_preserve_media(master_pack):
    engine,_,provider,_ = master_pack
    library = BackgroundLibrary(engine)
    lid = library.add("Cafe","A cafe",reference_type="SIMPLE").location_id
    buffer = BytesIO()
    Image.new("RGB",(32,32),"blue").save(buffer,"PNG")
    library.set_image(lid,buffer.getvalue())
    from story_channel.style_rules import StyleRules
    StyleRules(engine).save("Soft watercolor")
    StyleRules(engine).set_image(buffer.getvalue())
    state = engine.generate("backgrounds",subject=lid)
    prompt,refs = provider.calls[0]
    assert "Soft watercolor" in prompt
    assert "STYLE ONLY" in prompt
    assert refs[0] == library.image(state,lid)
    assert len(refs) == 2
    path = engine.local_path(state.records["backgrounds:"+lid+"__simple_master"].asset.file_path)
    library.approve(lid)
    save(engine,lid,reference_type="DETAIL")
    state = engine.inspect()
    assert state.location_reference_sets[lid]["reference_type"] == "KEY_360"
    assert path.is_file()
    assert not library.approved(lid,state)
    save(engine,lid,reference_type="SIMPLE")
    assert path.is_file()
    assert not library.approved(lid,engine.inspect())


def test_simple_card_exposes_prompt_and_optional_image_without_floor_plan(master_pack):
    engine,_,_,_ = master_pack
    lid = BackgroundLibrary(engine).add("Cafe","A cafe",reference_type="SIMPLE").location_id
    def display(engine,lid):
        from story_channel.background_assets_ui import _background_card
        _background_card(engine,lid,"test-simple-card")
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    assert any(w.label == "Detail prompt" for w in app.text_area)
    assert len(app.get("file_uploader")) == 1
    assert any(b.label == "Generate master (OpenAI)" and not b.disabled for b in app.button)
    assert not any("floor plan" in b.label.lower() for b in app.button)
