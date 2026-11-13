"""Voice and costume controls for one shared character card."""
from .image_preview_ui import image_preview
from .generation_ui import generation_button
import streamlit as st

from .animation_ui import character_voice_editor
from .identity import IdentityManager
from .ui_tasks import busy, launch, generation_busy, collect, queue_status, find_task, take_task_result
from .voice_design_ui import voice_design_heading
from .voice_preview_text import preview_sample_text
from .files import input_hash
from .character_revisions_ui import voice_revisions, costume_revisions
from .library_revisions import active_visual, visual_current


def launch_media(media, callback, label, *, button_key=None):
    if generation_busy():
        return
    from threading import Event
    event = Event()
    def run():
        media.stop_requested = event.is_set
        return callback()
    resource = button_key.replace("studio-video-sample-", "sample-").replace("studio-audio-sample-", "sample-") if button_key else label
    task = launch(run, label, stop_event=event, rerun_on_start=False, queue_enabled=True,
                  concurrent_safe=True, resource=resource, project_path=getattr(media, "path", None),
                  service=media.manager.settings.tts_provider if hasattr(media, "manager") else "elevenlabs")
    if task:
        task["sample_project"] = getattr(media, "project_id", st.session_state.get("generation-context-project"))
    if button_key:
        if task:
            task["generation_button_key"] = button_key
            task["generation_button_project"] = st.session_state.get("generation-context-project")


@st.fragment
def character_voice(engine, media, character):
    manager, project_id, cid = engine.manager, engine.project_id, character.character_id
    sample_keys = ("studio-audio-sample-" + cid, "studio-video-sample-" + cid)
    message_key = f"sample-message-{project_id}-{cid}"
    task = find_task(lambda item: item.get("sample_project") == project_id and item.get("generation_button_key") in sample_keys)
    owns_task = bool(task and task.get("sample_project") == project_id
                     and task.get("generation_button_key") in sample_keys)
    if owns_task and task["future"].done():
        collect(task)
    result = take_task_result(project_id, sample_keys)
    if result:
        st.session_state[message_key] = result
    voice_design_heading(project_id, cid)
    library_state = media.load()
    profile = IdentityManager(manager, project_id).load().voices[cid]
    approved = media.voice_approved(cid, profile, library_state)
    st.markdown(":green[Voice · Approved]" if approved else ":orange[Voice · Pending]")
    character_voice_editor(manager, project_id, cid)
    language = manager.load(project_id).language
    sample_key = f"studio-draft-sample-{project_id}-{cid}"
    st.session_state.setdefault(sample_key, preview_sample_text(language))
    st.text_area("Sample speech", key=sample_key, disabled=generation_busy())
    emotion_key = f"studio-sample-emotion-{project_id}-{cid}"
    st.selectbox("Emotion", ["neutral", "happy", "sad", "angry", "afraid", "excited"],
                 key=emotion_key, disabled=generation_busy())
    audio, video = st.columns(2)
    def start_sample(with_video):
        st.session_state.pop(message_key, None)
        sample_text, sample_emotion = st.session_state[sample_key], st.session_state[emotion_key]
        button_key = ("studio-video-sample-" if with_video else "studio-audio-sample-") + cid
        launch_media(media, lambda: media.talking_sample(cid, sample_text, sample_emotion, video=with_video),
                     "Generating character talking sample" if with_video else "Generating character voice sample",
                     button_key=button_key)
    generation_button("Generate voice sample", key="studio-audio-sample-" + cid, disabled=generation_busy(), container=audio,
                      on_click=start_sample, args=(False,))
    generation_button("Generate talking animation sample", key="studio-video-sample-" + cid, disabled=generation_busy(), container=video,
                      on_click=start_sample, args=(True,))
    task = find_task(lambda item: item.get("sample_project") == project_id and item.get("generation_button_key") in sample_keys)
    if task and task.get("sample_project") == project_id and task.get("generation_button_key") in sample_keys:
        queue_status(task, key="sample-queue-" + project_id + "-" + cid)
    message = st.session_state.get(message_key)
    if message:
        if message[0][0] == "error":
            from .error_report_ui import render_error
            render_error(message[0][1], project_path=engine.path, task_key=message[1].get("task"), key=message_key + "-error")
        else:
            st.success(message[0][1])
    st.caption("Talking samples last up to 3 seconds and combine generated speech and image-to-video motion. Longer speech is cut off in the video. Listen and inspect the result; exact lip synchronization is not guaranteed.")
    missing = media.voice_approval_missing(cid, profile, library_state)
    reasons = missing + (["Wait for the current task to finish."] if busy() else [])
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        if st.button("Approved" if approved else "Approve voice samples", key="studio-voice-approve-" + cid,
                     disabled=busy() or approved or bool(missing),
                     help="Already approved." if approved else " ".join(reasons) or "Approve the selected voice samples after listening."):
            launch(lambda: media.approve_voice(cid), "Approving character voice samples")
        if not approved and reasons:
            st.caption("Missing: " + " ".join(missing) if missing else reasons[0])
    voice_revisions(engine, media, cid)


def character_costumes(engine, state, media, character):
    identity = IdentityManager(engine.manager, engine.project_id)
    prefix = f"studio-draft-new-costume-{engine.project_id}-{character.character_id}-"
    if st.session_state.pop(prefix + "clear", False):
        st.session_state.pop(prefix + "id", None)
        st.session_state.pop(prefix + "description", None)
    with st.container(border=True):
        st.markdown("**Add costume**")
        with st.form("add-costume-" + engine.project_id + "-" + character.character_id):
            costume_id = st.text_input("Costume ID (optional)", key=prefix + "id", disabled=busy(),
                help="Leave blank to create a unique ID automatically.")
            description = st.text_area("New costume description", key=prefix + "description", disabled=busy())
            if st.form_submit_button("Add costume", key="add-costume-" + character.character_id, disabled=busy()):
                def added(_):
                    st.session_state[prefix + "clear"] = True
                    st.session_state.pop("identity-draft-" + engine.project_id, None)
                launch(lambda: identity.save_costume(character.character_id, costume_id, description), "Adding costume", added)
    library = identity.load()
    costumes = [costume for costume in library.costumes.values() if costume.character_id == character.character_id] if library else []
    if not costumes:
        st.caption("No costumes registered for this character.")
    for costume in costumes:
        costume_card(engine, media, identity, character, costume)


@st.fragment
def costume_card(engine, media, identity, character, costume):
    button_key = "studio-costume-" + costume.costume_id
    message_key = f"costume-message-{engine.project_id}-{costume.costume_id}"
    task = find_task(lambda item: item.get("costume_project") == engine.project_id and item.get("generation_button_key") == button_key)
    if (task and task.get("costume_project") == engine.project_id
            and task.get("generation_button_key") == button_key and task["future"].done()):
        collect(task)
    result = take_task_result(engine.project_id, [button_key])
    if result:
        st.session_state[message_key] = result
    from .ui_panels import persistent_expander
    panel = persistent_expander("**" + costume.costume_id + "**", expanded=False,
                     key=f"character-costume-card-{engine.project_id}-{costume.costume_id}")
    if not panel.open:
        return
    with panel:
        draft_key = f"studio-draft-costume-{engine.project_id}-{costume.costume_id}-{input_hash(costume.description)[:10]}"
        st.session_state.setdefault(draft_key, costume.description)
        description = st.text_area("Costume description", key=draft_key, disabled=busy())
        changed = description.strip() != costume.description
        save, revert = st.columns(2)
        if save.button("Save costume", key="save-costume-" + costume.costume_id, disabled=busy() or not changed or not description.strip()):
            launch(lambda: identity.save_costume(character.character_id, costume.costume_id, description,
                expected=costume.model_dump_json(by_alias=True)), "Saving costume",
                lambda _: st.session_state.pop("identity-draft-" + engine.project_id, None))
        revert.button("Revert changes", key="revert-costume-" + costume.costume_id, disabled=busy() or not changed,
                      on_click=lambda: st.session_state.update({draft_key: costume.description}))
        if changed:
            st.caption("Modified — save or revert before generating a visual.")
        key = "costume:" + costume.costume_id
        saved = media.load()
        versions = saved["visuals"].get(key, [])
        ready, signature = False, ""
        try:
            signature = media.costume_signature(character.character_id, costume.costume_id)
            ready = True
        except (OSError, ValueError) as exc:
            st.caption(str(exc))
        def generate():
            st.session_state.pop(message_key, None)
            task = launch(lambda: media.generate_costume(character.character_id, costume.costume_id,
                expected=costume.model_dump_json(by_alias=True)), "Generating costume model sheet", rerun_on_start=False,
                allow_parallel=True, queue_enabled=True, service="openai")
            if task:
                task.update(costume_project=engine.project_id, generation_button_key=button_key,
                            generation_button_project=engine.project_id)
        with st.container(horizontal=True, vertical_alignment="center", gap=None):
            generation_button("Regenerate visual" if versions else "Generate visual", key=button_key,
                              disabled=generation_busy() or changed or not ready, on_click=generate)
        task = find_task(lambda item: item.get("costume_project") == engine.project_id and item.get("generation_button_key") == button_key)
        if task and task.get("costume_project") == engine.project_id and task.get("generation_button_key") == button_key:
            queue_status(task, key=button_key + "-queue")
        message = st.session_state.get(message_key)
        if message:
            if message[0][0] == "error":
                from .error_report_ui import render_error
                render_error(message[0][1], project_path=engine.path, task_key=message[1].get("task"), key=message_key + "-error")
            else:
                st.success(message[0][1])
        # New results belong below the controls so completion cannot push the
        # generation button away from the user's current viewport.
        latest = active_visual(saved, key)
        if latest:
            path = engine.local_path(latest["asset"]["filePath"])
            current = ready and visual_current(media, saved, key, signature)
            st.caption(f"Revision {latest['revision']} · " + ("Current" if current else "Outdated — generate or apply a saved model sheet"))
            if path.is_file():
                image_preview(str(path), width=320)
        costume_revisions(engine, media, character.character_id, costume.costume_id)
