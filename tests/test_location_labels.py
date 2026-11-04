import json
from io import BytesIO

from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.location_labels import location_labels,location_label,display_locations
from story_channel.approval_exports import readable_export
from story_channel.files import atomic_write
from story_channel.background_library import BackgroundLibrary
from test_location_master_pack import master_pack,approve_pack
from test_studio import world


def test_background_card_title_shows_smaller_id_before_status(master_pack):
    engine,lid,_,_ = master_pack
    def display(engine):
        from story_channel.background_assets_ui import background_cards
        background_cards(engine)
    app = AppTest.from_function(display,args=(engine,),default_timeout=25).run()
    assert not app.exception
    label = next(box.label for box in app.expander if box.key == "background-card-"+lid)
    assert "**Office** · `"+lid+"` · :orange[Pending]" == label
    assert any("font-size:0.75em" in item.proto.body for item in app.get("html"))


def test_script_readable_export_has_title_but_canonical_location_id_is_unchanged(master_pack):
    engine,lid,_,_ = master_pack
    source = engine.path/"script.json"
    text = json.dumps({"title":"Example","scenes":[{"locationId":lid,"text":"Dialogue stays unchanged"}]})
    atomic_write(source,text)
    export = readable_export(source)
    assert lid+"(Office)" in export.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == text
    assert location_label("unknown",location_labels(engine.path)) == "unknown"


def test_detail_locations_show_parent_name_and_stable_detail_id(master_pack):
    engine,lid,_,_ = master_pack
    engine.generate("backgrounds",subject=lid)
    approve_pack(engine,lid)
    image = BytesIO()
    Image.new("RGB",(20,20),"red").save(image,format="PNG")
    state = BackgroundLibrary(engine).add_detail_reference(lid,"Desk close-up",image.getvalue(),detail_id="desk_area")
    detail = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    labels = location_labels(engine.path)
    expected = lid+"(Office · desk_area)"
    assert location_label(detail["key"],labels) == expected
    assert location_label(detail["key"].removeprefix("backgrounds:"),labels) == expected
    assert location_label(lid,labels,"desk_area") == expected
    data = {"locationId":lid,"targetArea":"desk_area"}
    assert display_locations(data,labels)["locationId"] == expected
    assert data["locationId"] == lid
    def display(engine,lid):
        from story_channel.location_details_ui import render
        render(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    assert any(box.label == expected for box in app.expander)
