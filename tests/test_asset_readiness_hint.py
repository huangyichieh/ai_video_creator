from streamlit.testing.v1 import AppTest

from story_channel.background_library import BackgroundLibrary
from story_channel.assets_workflow import animation_asset_readiness
from test_location_master_pack import master_pack
from test_studio import world


def test_hint_replaces_global_generation_buttons_and_keeps_inventory(master_pack):
    engine,_,_,_ = master_pack
    def display(engine):
        from story_channel.studio_assets_ui import asset_controls
        asset_controls(engine,engine.project_id)
    app = AppTest.from_function(display,args=(engine,),default_timeout=25).run()
    assert not app.exception
    assert not any(button.key in ("studio-assets-next","studio-assets-pipeline") for button in app.button)
    assert any("at least 1 character and 1 background" in message.value for message in app.success)
    assert any("Characters:" in message.value and "Backgrounds:" in message.value for message in app.caption)
    assert app.dataframe
    assert any(button.label == "Regenerate selected" for button in app.button)


def test_approved_minimum_shows_ready_hint_without_changing_other_gates(master_pack,monkeypatch):
    engine,lid,_,_ = master_pack
    monkeypatch.setattr(engine,"character_approved",lambda *args:True)
    monkeypatch.setattr(BackgroundLibrary,"approved",lambda self,subject,*args:subject == lid)
    result = animation_asset_readiness(engine,engine.load())
    assert result["approvedCharacters"] >= 1 and result["approvedBackgrounds"] == 1
    def display(engine):
        from story_channel.studio_assets_ui import animation_asset_hint
        animation_asset_hint(engine,engine.load())
    app = AppTest.from_function(display,args=(engine,),default_timeout=25).run()
    assert not app.exception
    assert any("Assets prerequisites done" in message.value for message in app.success)


def test_hint_handles_registration_race_without_losing_previous_counts(master_pack,monkeypatch):
    engine,_,_,_ = master_pack
    def display(engine):
        from story_channel.studio_assets_ui import animation_asset_hint
        animation_asset_hint(engine,engine.load())
    app = AppTest.from_function(display,args=(engine,),default_timeout=25).run()
    captions = [message.value for message in app.caption]
    def race(state):
        raise KeyError("registration is updating")
    monkeypatch.setattr(engine,"tasks",race)
    app.run()
    assert not app.exception
    assert [message.value for message in app.caption] == captions
