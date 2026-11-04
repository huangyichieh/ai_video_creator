import json

from streamlit.testing.v1 import AppTest

from test_location_master_pack import master_pack
from test_studio import world


def app_for(engine,lid):
    def display(engine,lid):
        import streamlit as st
        from story_channel.ui_tasks import preserve_page_widgets
        from story_channel.background_assets_ui import reference_set_settings
        from story_channel.background_library import BackgroundLibrary
        preserve_page_widgets()
        if st.session_state.get("show_settings",True):
            reference_set_settings(engine,BackgroundLibrary(engine),lid,engine.load())
    return AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()


def panel(app):
    return next(item for item in app.expander if item.label == "Floor plan, object layout & reference settings")


def test_open_state_survives_generate_check_preview_and_cancel(master_pack,monkeypatch):
    from story_channel.location_layout import LocationLayout,LayoutObject
    engine,lid,_,_ = master_pack
    def generate(*args,**kwargs):
        return LocationLayout(floor_plan="Generated floor plan",objects=[LayoutObject(name="desk",position="north wall")],references_match=True)
    monkeypatch.setattr("story_channel.location_layout.generate_location_layout",generate)
    app = app_for(engine,lid)
    key = f"floor-plan-settings-{engine.project_id}-{lid}"
    prefix = f"location-reference-set-{engine.project_id}-{lid}"
    assert not panel(app).proto.expanded
    original_id = panel(app).proto.id
    app.session_state[key] = True
    app.run()
    assert panel(app).proto.expanded
    app.button(key=prefix+"-generate-layout").click().run()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception and panel(app).proto.expanded
    assert panel(app).proto.id == original_id
    app.button(key=prefix+"-check-reference").click().run()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception and panel(app).proto.expanded
    app.button(key=prefix+"-modify-prompt").click().run()
    assert not app.exception
    app.button(key="cancel-layout-modification").click().run()
    assert not app.exception and panel(app).proto.expanded


def test_first_draft_save_does_not_change_expander_identity_or_reopen_closed_panel(master_pack):
    from story_channel.background_library import BackgroundLibrary
    engine,lid,_,_ = master_pack
    library = BackgroundLibrary(engine)
    library.configure_references(lid,"KEY_360","",{})
    app = app_for(engine,lid)
    key = f"floor-plan-settings-{engine.project_id}-{lid}"
    assert panel(app).proto.expanded
    original_id = panel(app).proto.id
    library.configure_references(lid,"KEY_360","Saved floor plan",{"desk":"north wall"})
    app.run()
    assert not app.exception and panel(app).proto.expanded
    assert panel(app).proto.id == original_id
    app.session_state[key] = False
    app.run()
    assert not panel(app).proto.expanded
    library.configure_references(lid,"KEY_360","",{})
    app.run()
    assert not app.exception and not panel(app).proto.expanded
    assert panel(app).proto.id == original_id


def test_expander_state_survives_page_hide_and_restore(master_pack):
    engine,lid,_,_ = master_pack
    app = app_for(engine,lid)
    key = f"floor-plan-settings-{engine.project_id}-{lid}"
    app.session_state[key] = True
    app.run()
    app.session_state["show_settings"] = False
    app.run()
    assert not app.exception and not app.expander
    app.session_state["show_settings"] = True
    app.run()
    assert not app.exception and panel(app).proto.expanded
