import json

import numpy as np
from PIL import Image

from story_channel.directional_masters import NAMES,YAWS,select,overlap_range,crop,assess
from story_channel.location_references import LocationReferenceResolver
from story_channel.floor_plan_guides import current_guides
from test_location_details import prepare
from test_location_master_pack import master_pack
from test_studio import world


def test_shared_world_angles_match_including_north_west_wrap():
    def sector(name):
        angles = np.deg2rad(YAWS[name]-55+(np.arange(1100)+.5)*.1)
        line = np.stack([127+100*np.cos(angles),127+100*np.sin(angles),np.full_like(angles,180)],axis=-1)
        return Image.fromarray(np.tile(np.rint(line).astype("uint8"),(128,1,1)))
    for a,b in zip(NAMES,NAMES[1:]+NAMES[:1]):
        region = overlap_range(a,b)
        assert region[1]-region[0] == 20
        assert np.abs(crop(sector(a),a,region)-crop(sector(b),b,region)).mean() < .1
    assert overlap_range(NAMES[0],NAMES[2]) is None
    assert select(90) == ["east_panorama"]
    assert select(270) == ["west_panorama"]
    assert select(45) == ["front_panorama","east_panorama"]
    assert select(315) == ["front_panorama","west_panorama"]


def test_master_generation_uses_neighbors_and_closes_west_north_boundary(master_pack):
    engine,lid,provider,guides = prepare(master_pack)
    assert [len(refs) for _,refs in provider.calls] == [2,3,3,4]
    for name,(prompt,refs) in zip(NAMES,provider.calls):
        assert "110-degree" in prompt and "20 degrees" in prompt
        assert refs[0] == engine.local_path(guides["views"][name]["cameraGuide"])
        if name != NAMES[0]:
            assert "an overlap crop" in prompt
            assert "correct that error instead of copying it" in prompt
    west_prompt = provider.calls[-1][0]
    assert '"source": "front_panorama"' in west_prompt
    assert '"source": "back_panorama"' in west_prompt
    state = engine.load()
    report = assess(engine,state,lid)
    assert len(report["overlaps"]) == 4 and not report["semanticGeometryVerified"]
    assert json.loads((engine.path/"locations"/lid/"overlap_qa.json").read_text())["signature"] == report["signature"]
    resolver = LocationReferenceResolver(engine,state)
    assert resolver.resolve(lid,90) == [f"backgrounds:{lid}__east_panorama"]
    assert resolver.resolve(lid,270) == [f"backgrounds:{lid}__west_panorama"]
    assert resolver.resolve(lid,45) == [f"backgrounds:{lid}__front_panorama",f"backgrounds:{lid}__east_panorama"]


def test_old_two_view_guides_require_refresh_without_deleting_saved_files(master_pack):
    engine,lid,_,guides = master_pack
    marker = engine.path/"locations"/lid/"camera_guides.json"
    old = dict(guides,views={key:value for key,value in guides["views"].items() if key in (NAMES[0],NAMES[2])})
    marker.write_text(json.dumps(old),encoding="utf-8")
    assert current_guides(engine,lid,engine.load().location_reference_sets[lid]) is None
    assert all(engine.local_path(view["cameraGuide"]).is_file() for view in guides["views"].values())


def test_master_ui_shows_four_views_and_requires_overlap_confirmation(master_pack,monkeypatch):
    from streamlit.testing.v1 import AppTest
    engine,lid,provider,_ = master_pack
    engine.generate("backgrounds",subject=lid)
    def launch(engine,callback,label,**kwargs):
        callback()
    monkeypatch.setattr("story_channel.background_assets_ui.launch_background",launch)
    def display(engine,lid):
        from story_channel.dual_panorama_ui import outputs
        outputs(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    assert len([item for item in app.caption if "Horizontal FOV: 110" in item.value]) == 4
    assert len([item for item in app.markdown if "20° overlap" in item.value]) == 4
    button_key = f"approve-location-pair-{lid}-masters"
    assert app.button(key=button_key).disabled
    review = next(item for item in app.checkbox if "overlapping regions" in item.label)
    review.check().run()
    assert not app.exception and not app.button(key=button_key).disabled
    app.button(key=button_key).click().run()
    assert not app.exception
    assert app.button(key=button_key).label == "Approved" and app.button(key=button_key).disabled
    assert len(provider.calls) == 4  # Reviewing and approving never request more images.
