"""Preview saved character media independently from its active selection."""
from .image_preview_ui import image_preview
from .revision_labels import revision_label
import streamlit as st

from .error_report_ui import render_error
from .library_revisions import (active_visual, sample_intact, sample_profile,
                                visual_current, visual_intact)
from .identity import IdentityManager
from .ui_tasks import busy, launch
from .files import input_hash


def delete_control(engine, media, cid, prefix, selected, record, *, costume_id=None):
    def delete():
        expected = input_hash(record)
        callback = (lambda: media.delete_costume_revision(cid, costume_id, selected, expected=expected)) if costume_id else (
            lambda: media.delete_voice_revision(cid, selected, expected=expected))
        task = launch(callback, f"Deleting {'costume' if costume_id else 'voice'} {cid} revision {selected}",
                      lambda _: st.session_state.pop(prefix + "-choice", None), inline=True, rerun_on_start=False)
        if task:
            task.update(asset_subject=cid, generation_button_project=engine.project_id, ui_owned_asset_project=engine.project_id)
    st.button("Delete revision", key=prefix + "-delete", disabled=busy(), on_click=delete)
    st.caption("Remaining revisions are renumbered from 1. Deleting active media clears that selection; apply or generate a replacement, then approve again.")


@st.fragment
def voice_revisions(engine, media, cid):
    state = media.load()
    history = sorted(state["samples"].get(cid, []), key=lambda s: s["revision"], reverse=True)
    if not history:
        return
    profile = IdentityManager(engine.manager, engine.project_id).load().voices[cid]
    selected_samples = media.selected_samples(cid, profile, state)
    active = {s["revision"] for s in selected_samples}
    st.caption("Active voice revisions: " + (", ".join(f"{s['emotion']} · revision {s['revision']}" for s in selected_samples)
                                           or "No sample for the current voice settings"))
    prefix = f"character-revisions-{engine.project_id}-voice-{cid}"
    panel = st.expander("View / apply voice revisions", key=prefix + "-panel", on_change="rerun")
    if not panel.open:
        return
    with panel:
        choices = {s["revision"]: s for s in history}
        if st.session_state.get(prefix + "-choice") not in choices:
            st.session_state.pop(prefix + "-choice", None)
        selected = st.selectbox("Preview voice revision", list(choices),
            format_func=lambda r: revision_label(choices[r])+f" · {choices[r]['emotion']}"
                + (" (Animation)" if choices[r].get("video") else "") + (" · Active" if r in active else ""),
            key=prefix + "-choice", persist_state="session")
        sample = choices[selected]
        intact = sample_intact(media, sample)
        if intact:
            st.audio(str(engine.local_path(sample["audio"])))
        else:
            st.warning("This revision's audio is missing or modified and cannot be applied.")
        if sample.get("video"):
            path = engine.local_path(sample["video"])
            if path.is_file():
                st.video(str(path))
        st.caption(f"Voice ID: {sample.get('voiceId', 'Unknown')} · Status: {sample.get('status', 'succeeded')}")
        st.write(sample.get("text", ""))
        can_apply = intact and media.sample_matches_provider(sample)
        try:
            saved_profile = sample_profile(media, cid, sample)
        except ValueError as exc:
            can_apply = False
            st.caption(str(exc))
        else:
            with st.expander("Saved voice settings"):
                st.json(saved_profile.model_dump(mode="json", by_alias=True))
        if not media.sample_matches_provider(sample):
            st.caption("Restore this revision's provider/model settings before applying it.")
        if sample.get("error"):
            render_error(sample["error"], project_path=engine.path,
                         task_key=f"library:talking_sample:{cid}", key=prefix + f"-error-{selected}")
        st.caption("Preview leaves the current voice unchanged. Apply restores the saved voice settings and selects this emotion's sample. Approve the voice samples, then approve the character again. No generation API is called.")
        label = f"Applying voice {cid} revision {selected}"
        task = st.session_state.get("workspace-task")
        saving = bool(busy() and task and task["label"] == label)

        def apply():
            def saved(_):
                st.session_state.pop("identity-draft-" + engine.project_id, None)
                st.session_state.pop(f"animation-voice-save-result-{engine.project_id}-{cid}", None)
                # The completion callback runs before the inputs are rendered.
                st.session_state[f"animation-preview-voice-{engine.project_id}-{cid}"] = saved_profile.voice_id
            launch(lambda: media.apply_voice_revision(cid, selected), label, saved,
                   inline=True, rerun_on_start=False)

        st.button("Applying…" if saving else f"Applied revision {selected}" if selected in active else f"Apply revision {selected}",
                  key=prefix + "-apply", disabled=busy() or not can_apply or selected in active, on_click=apply)
        delete_control(engine, media, cid, prefix, selected, sample)


@st.fragment
def costume_revisions(engine, media, cid, costume_id):
    state = media.load()
    key = "costume:" + costume_id
    versions = state["visuals"].get(key, [])
    if not versions:
        return
    prefix = f"character-revisions-{engine.project_id}-costume-{costume_id}"
    active = active_visual(state, key)
    signature = ""
    try:
        signature = media.costume_signature(cid, costume_id)
    except (ValueError, OSError):
        pass
    panel = st.expander("View / apply costume revisions", key=prefix + "-panel", on_change="rerun")
    if not panel.open:
        return
    with panel:
        choices = {v["revision"]: v for v in reversed(versions)}
        if st.session_state.get(prefix + "-choice") not in choices:
            st.session_state.pop(prefix + "-choice", None)
        current = active["revision"] if active else None
        selected = st.selectbox("Preview costume revision", list(choices),
            format_func=lambda r: revision_label(choices[r]) + (" · Active" if r == current else ""),
            key=prefix + "-choice", persist_state="session")
        version = choices[selected]
        intact = visual_intact(media, version)
        if intact:
            image_preview(str(engine.local_path(version["asset"]["filePath"])), width="stretch")
        else:
            st.warning("This revision's image is missing or modified and cannot be applied.")
        with st.expander("Revision generation details"):
            st.write(version.get("prompt", ""))
            st.json(version["asset"])
        st.caption("Apply selects this model sheet for the current saved costume description. Preview does not change the selection. Inspect and approve the character again after applying. No generation API is called.")
        already = selected == current and bool(signature) and visual_current(media, state, key, signature)
        label = f"Applying costume {costume_id} revision {selected}"
        task = st.session_state.get("workspace-task")
        saving = bool(busy() and task and task["label"] == label)
        st.button("Applying…" if saving else f"Applied revision {selected}" if already else f"Apply revision {selected}",
                  key=prefix + "-apply", disabled=busy() or not intact or not signature or already,
                  on_click=lambda: launch(lambda: media.apply_costume_revision(cid, costume_id, selected), label,
                                         inline=True, rerun_on_start=False))
        delete_control(engine, media, cid, prefix, selected, version, costume_id=costume_id)
