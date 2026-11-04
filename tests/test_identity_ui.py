from pathlib import Path
from threading import Event

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.animation import AnimationDirector
from story_channel.identity import IdentityManager
from test_assets_ui import prepare
from test_ui import finish_background


def open_assets(manager, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    return app


def test_library_expansion_survives_build_and_navigation(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    app = open_assets(manager, monkeypatch)
    panel_key = f"identity-panel-{project.project_id}"
    # AppTest cannot click an expander; set its native tracked state instead.
    app.session_state[panel_key] = True
    app.run()
    app.button(key=f"identity-build-{project.project_id}").click().run()
    finish_background(app)
    assert app.session_state[panel_key] is True
    panel = next(item for item in app.expander if item.label.startswith("Identity library:"))
    assert panel.proto.expanded
    app.button(key="nav-final").click().run()
    app.button(key="nav-assets").click().run()
    assert app.session_state[panel_key] is True
    app.session_state[panel_key] = False
    app.run()
    assert not next(item for item in app.expander if item.label.startswith("Identity library:")).proto.expanded
    assert not app.exception


def test_current_approval_is_visible_and_disabled_then_expires(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    service = IdentityManager(manager, project.project_id)
    library = service.prepare()
    expected = library.model_dump_json(by_alias=True)
    for profile in library.voices.values():
        profile.voice_id = "nova"
    service.save(library, expected=expected)
    assets = AssetPipeline(manager, project.project_id)
    assets.run()
    assets.approve("characters")
    assets.run()
    assets.approve("backgrounds")
    service.approve()
    app = open_assets(manager, monkeypatch)
    approve = app.button(key=f"identity-approve-{project.project_id}")
    assert approve.label == "Approved" and approve.disabled
    assert any("Identity library status:** :green[Approved]" in item.value for item in app.markdown)
    assets.set_generic_prompt("A new watercolor style")
    app.run()
    approve = app.button(key=f"identity-approve-{project.project_id}")
    assert approve.label == "Approve identity library" and approve.disabled
    assert any("Approval outdated" in item.value for item in app.markdown)
    assert not app.exception


def test_voice_ids_can_be_saved_before_voice_setup(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    service = IdentityManager(manager, project.project_id)
    library = service.prepare()
    director = AnimationDirector(manager, project.project_id)
    assert director.load() is None
    app = open_assets(manager, monkeypatch)
    for cid in library.voices:
        value = f"test-provider-voice-{cid}"
        app.text_input(key=f"animation-preview-voice-{project.project_id}-{cid}").set_value(value).run()
        app.button(key=f"animation-voice-save-{project.project_id}-{cid}").click().run()
        finish_background(app)
        assert service.load().voices[cid].voice_id == value
        assert director.load() is None
        assert not app.exception
    app.button(key="nav-final").click().run()
    app.button(key="nav-assets").click().run()
    for cid in library.voices:
        assert app.text_input(key=f"animation-preview-voice-{project.project_id}-{cid}").value == f"test-provider-voice-{cid}"
    assert not app.exception


def test_configure_next_step_opens_voice_tab_without_starting_generation(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    service = IdentityManager(manager, project.project_id)
    library = service.prepare()
    assets = AssetPipeline(manager, project.project_id)
    assets.generate("character_references")
    assets.approve("characters")
    assets.generate("backgrounds")
    assets.approve("backgrounds")
    app = open_assets(manager, monkeypatch)
    cid = next(iter(library.voices))
    voice_key = f"animation-preview-voice-{project.project_id}-{cid}"
    app.text_input(key=voice_key).set_value("unsaved-voice-selection").run()
    button = app.button(key="generate-next-asset-stage")
    assert button.label.endswith("Configure Voice Profiles") and not button.disabled
    assert next(item for item in app.button if item.label == "Generate assets until review").disabled
    button.click().run()
    assert not app.exception
    assert app.session_state[f"page-tab-assets-{project.project_id}"] == "Voice Profiles"
    assert app.text_input(key=voice_key).value == "unsaved-voice-selection"
    html = [item.proto.body for item in app.get("html")]
    assert any("outline: 2px solid" in body for body in html)
    scroll_script = next(body for body in html if "scrollIntoView" in body)
    app.button(key="generate-next-asset-stage").click().run()
    repeated_script = next(item.proto.body for item in app.get("html") if "scrollIntoView" in item.proto.body)
    assert repeated_script != scroll_script
    assert "workspace-task" not in app.session_state
    assert AnimationDirector(manager, project.project_id).load() is None
    assert not service.load().voices[cid].voice_id
    app.run()
    assert not any("scrollIntoView" in item.proto.body for item in app.get("html"))
    assert app.text_input(key=voice_key).value == "unsaved-voice-selection"


def test_approve_identity_next_step_opens_library_without_approving(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    service = IdentityManager(manager, project.project_id)
    library = service.prepare()
    changed = library.model_copy(deep=True)
    for profile in changed.voices.values():
        profile.voice_id = "nova"
    service.save(changed, expected=library.model_dump_json(by_alias=True))
    assets = AssetPipeline(manager, project.project_id)
    assets.generate("character_references")
    assets.approve("characters")
    assets.generate("backgrounds")
    assets.approve("backgrounds")
    app = open_assets(manager, monkeypatch)
    panel_key = f"identity-panel-{project.project_id}"
    app.session_state[panel_key] = False
    app.run()
    button = app.button(key="generate-next-asset-stage")
    assert button.label.endswith("Approve Identity Library") and not button.disabled
    assert next(item for item in app.button if item.label == "Generate assets until review").disabled
    button.click().run()
    assert not app.exception and app.session_state[panel_key] is True
    assert next(item for item in app.expander if item.label.startswith("Identity library:")).proto.expanded
    assert not app.button(key=f"identity-approve-{project.project_id}").disabled
    html = [item.proto.body for item in app.get("html")]
    assert any("outline: 2px solid" in body and f"identity-approve-{project.project_id}" in body for body in html)
    scroll_script = next(body for body in html if "scrollIntoView" in body)
    assert f"identity-approve-{project.project_id}" in scroll_script
    assert "workspace-task" not in app.session_state and service.load().approval is None
    app.button(key="generate-next-asset-stage").click().run()
    assert next(item.proto.body for item in app.get("html") if "scrollIntoView" in item.proto.body) != scroll_script
    app.run()
    assert not any("scrollIntoView" in item.proto.body for item in app.get("html"))
    app.button(key=f"identity-approve-{project.project_id}").click().run()
    finish_background(app)
    assert not app.exception and service.load().approval is not None
    assert app.button(key=f"identity-approve-{project.project_id}").disabled
    assert not any("identity-approve-" in item.proto.body for item in app.get("html"))


@pytest.mark.parametrize("with_direction", [False, True])
def test_character_voice_save_keeps_tab_and_panel_open_with_saving_label(tmp_path, monkeypatch, with_direction):
    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    cid = next(iter(library.voices))
    if with_direction:
        AnimationDirector(manager, project.project_id).prepare()
    app = open_assets(manager, monkeypatch)
    tab_key = f"page-tab-assets-{project.project_id}"
    panel_key = f"animation-voice-panel-{project.project_id}-{cid}"
    app.session_state[tab_key] = "Voice Profiles"
    if with_direction:
        app.session_state[panel_key] = True
    app.run()
    input_key = f"animation-preview-voice-{project.project_id}-{cid}"
    app.text_input(key=input_key).set_value("saved-test-voice").run()
    release = Event()
    original_save = IdentityManager.save

    def delayed_save(self, *args, **kwargs):
        assert release.wait(timeout=30)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(IdentityManager, "save", delayed_save)
    reruns = []
    original_rerun = st.rerun

    def track_rerun(*args, **kwargs):
        reruns.append(kwargs.get("scope", "app"))
        return original_rerun(*args, **kwargs)

    monkeypatch.setattr(st, "rerun", track_rerun)
    save_key = f"animation-voice-save-{project.project_id}-{cid}"
    try:
        app.button(key=save_key).click().run()
        assert not app.exception and not reruns
        button = app.button(key=save_key)
        assert button.label == "Saving..." and button.disabled
        assert app.session_state[tab_key] == "Voice Profiles"
        assert app.text_input(key=input_key).value == "saved-test-voice"
        if with_direction:
            assert app.session_state[panel_key] is True
            assert next(item for item in app.expander if item.label == "Voice settings & audition samples").proto.expanded
        release.set()
        finish_background(app)
        assert not app.exception
        assert identity.load().voices[cid].voice_id == "saved-test-voice"
        assert app.button(key=save_key).label == "Save character voice"
        assert app.button(key=save_key).disabled
        assert app.session_state[tab_key] == "Voice Profiles"
        assert any("Voice saved." in item.value for item in app.caption)
        if with_direction:
            assert app.session_state[panel_key] is True
            assert next(item for item in app.expander if item.label == "Voice settings & audition samples").proto.expanded
            assert AnimationDirector(manager, project.project_id).load().plan.voices[cid].voice_id == "saved-test-voice"
        else:
            assert AnimationDirector(manager, project.project_id).load() is None
    finally:
        release.set()


def test_narrator_save_stays_on_voice_profiles_and_shows_saving(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    app = open_assets(manager, monkeypatch)
    tab_key = f"page-tab-assets-{project.project_id}"
    app.session_state[tab_key] = "Voice Profiles"
    app.run()
    input_key = f"asset-narrator-{project.project_id}"
    app.text_input(key=input_key).set_value("nova").run()
    release = Event()
    original = AssetPipeline.set_voice

    def delayed(self, *args):
        assert release.wait(timeout=30)
        return original(self, *args)

    monkeypatch.setattr(AssetPipeline, "set_voice", delayed)
    reruns = []
    original_rerun = st.rerun

    def rerun(*args, **kwargs):
        reruns.append(kwargs.get("scope", "app"))
        return original_rerun(*args, **kwargs)

    monkeypatch.setattr(st, "rerun", rerun)
    key = f"save-narrator-voice-{project.project_id}"
    try:
        app.button(key=key).click().run()
        assert not app.exception and not reruns
        assert app.button(key=key).label == "Saving..." and app.button(key=key).disabled
        assert app.session_state[tab_key] == "Voice Profiles"
        release.set()
        finish_background(app)
        assert not app.exception
        assert AssetPipeline(manager, project.project_id).load().voice_map["narrator"] == "nova"
        assert app.button(key=key).label == "Save narrator voice" and app.button(key=key).disabled
        assert app.session_state[tab_key] == "Voice Profiles"
        assert app.text_input(key=input_key).value == "nova"
        assert any("Voice saved." in item.value for item in app.caption)
    finally:
        release.set()


def test_narrator_can_be_saved_before_creative_stages(tmp_path, monkeypatch):
    from test_workspace_state import workspace
    manager, project, app = workspace(tmp_path, monkeypatch)
    app.button(key="nav-assets").click().run()
    app.text_input(key=f"asset-narrator-{project.project_id}").set_value("nova").run()
    app.button(key=f"save-narrator-voice-{project.project_id}").click().run()
    finish_background(app)
    assert not app.exception
    assert AssetPipeline(manager, project.project_id).load().voice_map["narrator"] == "nova"
    assert not manager.load(project.project_id).steps


@pytest.mark.parametrize("with_direction", [False, True])
@pytest.mark.parametrize("previous_voice", ["", "saved-test-voice"])
@pytest.mark.parametrize("failure_status", [404, 401])
def test_character_save_rejects_invalid_voice_and_allows_retry(tmp_path, monkeypatch, with_direction, previous_voice, failure_status):
    import httpx
    from story_channel.llm import ProviderError
    from test_voice_validation import install_lookup

    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    cid = list(library.voices)[-1 if with_direction else 0]
    if previous_voice:
        changed = library.model_copy(deep=True)
        changed.voices[cid].voice_id = previous_voice
        library = identity.save(changed, expected=library.model_dump_json(by_alias=True))
    if with_direction:
        AnimationDirector(manager, project.project_id).prepare()
    AssetPipeline(manager, project.project_id, demo=False).inspect()
    status = [failure_status]
    requests = []

    def lookup(request):
        requests.append(request)
        return httpx.Response(status[0], json={"voice_id": "voice-to-check", "name": "Verified voice"})

    install_lookup(monkeypatch, lookup)
    app = open_assets(manager, monkeypatch)
    assert app.selectbox(key=f"asset-mode-{project.project_id}").value == "OpenAI"
    tab_key = f"page-tab-assets-{project.project_id}"
    panel_key = f"animation-voice-panel-{project.project_id}-{cid}"
    app.session_state[tab_key] = "Voice Profiles"
    if with_direction:
        app.session_state[panel_key] = True
    app.run()
    input_key = f"animation-preview-voice-{project.project_id}-{cid}"
    app.text_input(key=input_key).set_value("voice-to-check").run()
    save_key = f"animation-voice-save-{project.project_id}-{cid}"
    app.button(key=save_key).click().run()
    errors = [item.value for item in app.error]
    if "workspace-task" in app.session_state:
        with pytest.raises(ProviderError, match="not found" if failure_status == 404 else "rejected the API key"):
            app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    errors.extend(item.value for item in app.error)
    assert not app.exception
    assert identity.load().voices[cid].voice_id == previous_voice
    if with_direction:
        assert AnimationDirector(manager, project.project_id).load().plan.voices[cid].voice_id == previous_voice
    if failure_status == 404:
        assert any(item.value == "voice id does not exist" for item in app.error)
        assert not any("Voice ID was not found" in message for message in errors)
        assert len([item for item in app.error if item.value == "voice id does not exist"]) == 1
        assert app.text_input(key=input_key).value == previous_voice
        assert app.button(key=save_key).disabled
        app.run()
        assert any(item.value == "voice id does not exist" for item in app.error)
    else:
        assert any("rejected the API key" in message for message in errors)
        assert not any(item.value == "voice id does not exist" for item in app.error)
        assert app.text_input(key=input_key).value == "voice-to-check"
    assert app.session_state[tab_key] == "Voice Profiles"
    if with_direction:
        assert app.session_state[panel_key] is True
    status[0] = 200
    app.text_input(key=input_key).set_value("voice-to-check").run()
    assert not any(item.value == "voice id does not exist" for item in app.error)
    app.button(key=save_key).click().run()
    finish_background(app)
    assert not app.exception
    assert identity.load().voices[cid].voice_id == "voice-to-check"
    assert len(requests) == 2 and all(request.method == "GET" for request in requests)
