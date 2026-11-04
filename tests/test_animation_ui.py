from pathlib import Path
from streamlit.testing.v1 import AppTest
from story_channel.animation import AnimationDirector
from story_channel.cli import main
from story_channel.files import input_hash
from story_channel.pipeline import Pipeline
from test_assets_ui import prepare
import pytest
from threading import Event


def test_animation_ui_build_edit_revert_save_and_voice_preview(tmp_path,monkeypatch):
    manager,project=prepare(tmp_path)
    monkeypatch.setenv('STORY_DATA_ROOT',str(manager.root))
    monkeypatch.setenv('STORY_LLM_PROVIDER','demo')
    monkeypatch.setenv('STORY_ASSET_PROVIDER','demo')
    app=AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"),default_timeout=30).run()
    app.button(key='nav-animations').click().run()
    app.button(key=f'animation-build-{project.project_id}').click().run()
    assert not app.exception
    director=AnimationDirector(manager,project.project_id)
    draft=f'animation-plan-draft-{project.project_id}'
    original=app.text_area(key=draft).value
    app.text_area(key=draft).set_value(original+'\n ').run()
    app.button(key=f'animation-revert-{project.project_id}').click().run()
    assert app.text_area(key=draft).value==original
    import json
    changed=json.loads(original)
    cid=next(iter(changed['voices']))
    changed['voices'][cid]['voiceId']='nova'
    app.text_area(key=draft).set_value(json.dumps(changed)).run()
    app.button(key=f'animation-save-{project.project_id}').click().run()
    assert not app.exception
    assert director.load().plan.voices[cid].voice_id=='nova'
    app.button(key='nav-assets').click().run()
    sample=f'animation-preview-text-{project.project_id}-{cid}'
    app.text_area(key=sample).set_value('Keep the beacon burning. We will get through the storm together, one step at a time.').run()
    app.button(key=f'animation-preview-{project.project_id}-{cid}').click().run()
    if 'workspace-task' in app.session_state:
        app.session_state['workspace-task']['future'].result(timeout=20)
        app.run()
    assert not app.exception
    assert director.load().previews[cid].asset.duration>=3
    checkbox=next(c for c in app.checkbox if c.label=="I approve this character's voice and delivery")
    checkbox.check().run()
    app.button(key=f'animation-voice-approve-{project.project_id}-{cid}').click().run()
    assert director.load().previews[cid].approval
    app.button(key='nav-story-settings').click().run()
    app.button(key='nav-assets').click().run()
    assert app.text_area(key=sample).value.startswith('Keep the beacon')


def test_animation_cli_and_legacy_artifacts_remain_compatible(tmp_path,capsys):
    manager,project=prepare(tmp_path)
    flow=Pipeline(manager,project.project_id)
    before={k:v.output_hash for k,v in manager.load(project.project_id).steps.items()}
    common=['--data-root',str(manager.root),'animation',project.project_id]
    assert main(common+['prepare'])==0
    capsys.readouterr()
    assert main(common+['show'])==0
    capsys.readouterr()
    flow.inspect()
    assert {k:v.output_hash for k,v in manager.load(project.project_id).steps.items()}==before
    assert main(common+['preview'])==1
    capsys.readouterr()
    from story_channel.logging_config import configure_logging
    configure_logging('WARNING')


@pytest.mark.parametrize("action,method,hint", [
    ("animation-preview", "preview_voice", "Generating…"),
    ("animation-emotion-generate", "preview_voice", "Generating…"),
    ("animation-voice-approve", "approve_voice", "Approving..."),
])
def test_audition_actions_show_loading_without_page_banners(tmp_path, monkeypatch, action, method, hint):
    from test_identity_ui import open_assets
    from test_ui import finish_background
    manager, project = prepare(tmp_path)
    director = AnimationDirector(manager, project.project_id)
    state = director.prepare()
    cid = next(iter(state.plan.voices))
    state.plan.voices[cid].voice_id = "nova"
    director.save_plan(state.plan)
    director.preview_voice(cid, "Keep the beacon burning. Together we will find our way safely through the storm, one step at a time.")
    app = open_assets(manager, monkeypatch)
    app.session_state[f"page-tab-assets-{project.project_id}"] = "Voice Profiles"
    app.session_state[f"animation-voice-panel-{project.project_id}-{cid}"] = True
    app.run()
    if action == "animation-voice-approve":
        next(item for item in app.checkbox if item.label == "I approve this character's voice and delivery").check().run()
    release = Event()
    original = getattr(AnimationDirector, method)
    def delayed(self, *args, **kwargs):
        assert release.wait(20)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(AnimationDirector, method, delayed)
    key = f"{action}-{project.project_id}-{cid}"
    try:
        app.button(key=key).click().run()
        assert not app.exception
        assert app.button(key=key).label == hint and app.button(key=key).disabled
        assert app.session_state["workspace-task"]["inline"] is True
        release.set()
        finish_background(app)
        assert not app.exception
        assert app.session_state[f"animation-voice-panel-{project.project_id}-{cid}"] is True
        assert app.session_state[f"page-tab-assets-{project.project_id}"] == "Voice Profiles"
        assert not any("completed." in item.value for item in app.success)
        if action == "animation-voice-approve":
            assert director.load().previews[cid].approval
            assert app.button(key=key).disabled
    finally:
        release.set()
