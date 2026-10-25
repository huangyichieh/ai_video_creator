"""Reference/voice direction and continuity review, with explicit human approvals."""
import streamlit as st
from .animation import AnimationDirector, voice_signature
from .animation_models import AnimationPlan
from .animation_rendering import approve_continuity
from .modification_ui import open_modification
from .ui_tasks import busy, launch, launch_render
from .files import input_hash
from .proofreading import TypoSuggestion, apply_suggestion, check_typos
from .action_labels import action_label, text_action_label
from .voice_design_ui import voice_design_heading


def animation_direction(manager, project_id):
    director = AnimationDirector(manager, project_id)
    state = director.load()
    from .identity import IdentityManager
    identity = IdentityManager(manager, project_id)
    library = identity.load()
    panel_key = f"animation-direction-panel-{project_id}"
    st.session_state.setdefault(panel_key, bool(state))
    with st.expander("Shot direction and performance approvals", key=panel_key, on_change="rerun"):
        if library:
            st.caption("Voice profiles and Location Bible come from Identity library. Edit them there, save, then rebuild this plan. Shot actions, interaction targets, costume/prop IDs and performance metadata remain editable here.")
        st.caption("Voice casting and audition approval are managed on Assets. Here, review the scene images from step 1, save shot/performance changes, and complete both approval checks below before step 3.")
        if st.button("Build / rebuild animation plan", key=f"animation-build-{project_id}", disabled=busy()):
            launch(director.prepare, "Building animation direction", lambda _: st.session_state.pop(f"animation-plan-draft-{project_id}", None))
        if not state:
            return
        path = director.path / "animation_plan.md"
        if path.exists():
            st.markdown(path.read_text(encoding="utf-8"))
        with st.expander("Optional Act-Two performance for dialogue close-ups"):
            st.caption("Use only for important single-character dialogue close-ups. Upload a 3-30 second driving performance video with a clearly visible face. This transfers acting; it is not a text-only or audio-only lip-sync step. Voice tracks remain separately approved.")
            ids = [shot.shot_id for shot in state.plan.shots]
            chosen_shot = st.selectbox("Performance shot", ids, key=f"animation-performance-shot-{project_id}")
            uploaded = st.file_uploader("Driving performance MP4", type=["mp4"], max_upload_size=100, key=f"animation-performance-upload-{project_id}", disabled=busy())
            selected_shot = next(shot for shot in state.plan.shots if shot.shot_id == chosen_shot)
            if selected_shot.performance_video:
                st.video(str(director.assets.local_path(selected_shot.performance_video)))
            attach, remove = st.columns(2)
            def save_performance(data):
                expected = state.plan.model_dump_json(by_alias=True)
                launch(lambda: director.import_performance(chosen_shot, data, expected=expected), "Saving driving performance",
                       lambda _: st.session_state.pop(f"animation-plan-draft-{project_id}", None))
            if attach.button("Use Act-Two for this shot", key=f"animation-performance-attach-{project_id}", disabled=busy() or uploaded is None):
                save_performance(uploaded.getvalue())
            if remove.button("Use main animation model", key=f"animation-performance-remove-{project_id}", disabled=busy() or not selected_shot.performance_video):
                save_performance(None)
        draft_key = f"animation-plan-draft-{project_id}"
        baseline = state.plan.model_dump_json(by_alias=True, indent=2)
        st.session_state.setdefault(draft_key, baseline)
        text = st.text_area("Animation / performance / voice profile JSON", key=draft_key, height=320, disabled=busy())
        st.caption("For a Kling terminal keyframe, set a shot's lastFrame to an approved image path inside this project. It is sent on the final generated part of that shot. Changing the image requires direction approval again; Act-Two does not accept this constraint.")
        changed = text != baseline
        if changed:
            st.caption("Modified — not saved")
        if st.button("Generate performance direction with OpenAI", key=f"animation-performance-generate-{project_id}", disabled=busy() or changed,
                     help="Use the selected story LLM to refine shared acting metadata. API usage is recorded, including in Demo mode."):
            expected = state.plan.model_dump_json(by_alias=True)
            instructions = ("Refine every dialogue performance into natural cinematic acting: infer subtext, emotion/intensity, pace, volume, pauses, "
                "facial expression, gesture, body/eye action, camera reaction and SFX cues from the line and its scene context. "
                "Keep voice profiles, locations, dialogue text, speaker/scene IDs, shot IDs and driving video selections unchanged. "
                "Refine concrete shot interaction/action and camera direction where useful; do not invent characters or references.")
            launch(lambda: director.modify_with_openai(instructions, expected), "Generating performance direction",
                   lambda _: st.session_state.pop(draft_key, None))
        save, revert, modify, typo = st.columns(4)
        if save.button("Save direction", key=f"animation-save-{project_id}", disabled=busy() or not changed):
            try:
                plan = AnimationPlan.model_validate_json(text)
                launch(lambda: director.save_plan(plan), "Saving direction", lambda _: st.session_state.pop(draft_key, None))
            except ValueError as exc:
                st.error(str(exc))
        revert.button("Revert all changes", key=f"animation-revert-{project_id}", disabled=busy() or not changed,
                      on_click=lambda: st.session_state.update({draft_key: baseline}))
        if modify.button(text_action_label("Modify", director.assets.creative), key=f"animation-modify-{project_id}", disabled=busy() or changed):
            open_modification(project_id, "animation", "animation_plan", expected=state.plan.model_dump_json(by_alias=True))
        report_key = f"animation-typos-{project_id}"
        if typo.button("Check typos", key=f"animation-typos-check-{project_id}", disabled=busy()):
            def checked(report):
                st.session_state[report_key] = {"draft_hash": input_hash(text), "report": report.model_dump(), "applied": []}
            launch(lambda: check_typos(director.assets.creative, {"animation_plan": text}, "animation_plan"), "Checking direction typos", checked)
        report = st.session_state.get(report_key)
        if report:
            stale = report["draft_hash"] != input_hash(text)
            st.write(report["report"]["summary"])
            if stale:
                st.caption("Direction draft changed; check typos again for current suggestions.")
            for index, item in enumerate(report["report"]["suggestions"]):
                st.write(f"{item['original']} → {item['replacement']}")
                st.caption(item["explanation"])
                def apply(index=index):
                    saved = st.session_state[report_key]
                    current = st.session_state[draft_key]
                    if saved["draft_hash"] != input_hash(current):
                        return
                    suggestion = TypoSuggestion.model_validate(saved["report"]["suggestions"][index])
                    st.session_state[draft_key] = apply_suggestion({"animation_plan": current}, suggestion)
                    saved["draft_hash"] = input_hash(st.session_state[draft_key])
                    saved["applied"].append(index)
                st.button("Applied" if index in report["applied"] else "Apply suggestion",
                          key=f"animation-typo-apply-{project_id}-{index}", on_click=apply,
                          disabled=busy() or stale or index in report["applied"])
        plan_approved = bool(state.plan_approval and state.plan_approval.content_hash == director.direction_hash(state))
        performance_approved = bool(state.performance_approval and state.performance_approval.content_hash == input_hash(state.plan.model_dump(mode="json")))
        with st.container(key=f"animation-direction-approval-{project_id}"):
            checked = st.checkbox("I reviewed the shot plan, character references and location bible", key=f"animation-plan-check-{project_id}")
            if st.button("Approved" if plan_approved else "Approve animation and references", key=f"animation-approve-plan-{project_id}", disabled=busy() or changed or not checked or plan_approved):
                launch(director.approve_plan, "Approving animation direction")
        if state.plan_approval:
            current = state.plan_approval.content_hash == director.direction_hash(state)
            st.caption("Animation/reference approval: " + ("current" if current else "outdated — approve current references again"))
        pack = director.references()
        with st.expander("Character and location reference pack"):
            for key, item in pack.items():
                st.write(key)
                st.image(str(director.assets.local_path(item["file"])), width=260)
            st.json({"references": pack, "locations": {k: v.model_dump(mode="json", by_alias=True) for k, v in state.plan.locations.items()}})
        with st.container(key=f"animation-performance-approval-{project_id}"):
            performance = st.checkbox("I reviewed each line's emotion, intensity, pauses, subtext, gesture and expression", key=f"animation-performance-check-{project_id}")
            if st.button("Approved" if performance_approved else "Approve dialogue / performance direction", key=f"animation-performance-approve-{project_id}", disabled=busy() or changed or not performance or performance_approved):
                launch(director.approve_performance, "Approving performance direction")
        st.caption("After changing voice/performance direction, regenerate stale voice tracks, alignment and asset reviews before rendering. AI voice and face consistency still require listening and visual review.")


def animation_continuity(engine):
    director = AnimationDirector(engine.manager, engine.project_id)
    state = director.load()
    if not state:
        return
    st.subheader("Animated shots and continuity review")
    st.caption("Shots run in order because each cut frame can condition the next shot. Successful shots are cached. Retry replaces the chosen shot and any shots whose chained input frame changes.")
    st.dataframe([{"Shot": record.shot_id, "Scene": record.scene_id, "Status": record.status,
                   "Revision": record.revision, "Attempts": record.attempts, "Progress": record.remote_progress,
                   "Error": record.error or ""} for record in state.shots.values()], hide_index=True, width="stretch")
    for record in state.shots.values():
        with st.expander(f"{record.shot_id} · {record.status} · revision {record.revision}"):
            if record.asset:
                file = director.assets.local_path(record.asset.file_path)
                if file.exists():
                    st.video(str(file))
                    st.caption("Raw animation clip. Any provider-generated audio is replaced by the approved voice tracks in the scene previews below.")
            if record.end_frame:
                file = director.assets.local_path(record.end_frame)
                if file.exists():
                    st.image(str(file), caption="Cut frame used to condition the next compatible shot", width=320)
            if st.button("Retry / regenerate this shot", key=f"animation-shot-retry-{record.shot_id}", disabled=busy()):
                launch_render(engine, lambda sid=record.shot_id: engine.retry_animation_shot(sid), f"Retrying {record.shot_id}")
            if record.asset and record.asset.generation_params.get("jobId"):
                if st.button(action_label("Modify", record.asset.provider, record.asset.model), key=f"animation-shot-modify-{record.shot_id}", disabled=busy()):
                    open_modification(engine.project_id, "animation_shot", "shot", subject=record.shot_id, expected=record.asset.asset_id)
            st.json(record.model_dump(mode="json", by_alias=True))
    with st.container(key=f"animation-continuity-approval-{engine.project_id}"):
        checked = st.checkbox("I reviewed faces, hair, costumes, props, background layout, lighting, voices, emotion and motion at every cut", key=f"animation-continuity-check-{engine.project_id}")
        if st.button("Approve scene continuity", key="animation-approve-continuity", disabled=busy() or not checked or not state.shots):
            launch(lambda: approve_continuity(director), "Approving scene continuity")
    if engine.load().status == "continuity_review":
        st.info("Approve scene continuity and each scene, then open Final > Outputs & Publish approval to assemble the approved scenes.")


def provider_voice_input(project_id, cid, saved, *, key=None, label="Provider voice ID"):
    voice_key = key or f"animation-preview-voice-{project_id}-{cid}"
    previous_key = voice_key + "-baseline"
    previous = st.session_state.get(previous_key, saved)
    if saved != previous and st.session_state.get(voice_key, previous) == previous:
        st.session_state[voice_key] = saved
    st.session_state[previous_key] = saved
    st.session_state.setdefault(voice_key, saved)
    return st.text_input(label, key=voice_key, disabled=busy(),
                         placeholder="ElevenLabs Voice ID (copy from your voice library)", persist_state="session")


@st.fragment
def character_voice_editor(manager, project_id, cid):
    with st.container(key=f"asset-character-voice-editor-{project_id}-{cid}"):
        character_voice_controls(manager, project_id, cid)


def character_voice_controls(manager, project_id, cid):
    from .identity import IdentityManager
    identity = IdentityManager(manager, project_id)
    library = identity.load()
    director = AnimationDirector(manager, project_id)
    state = director.load()
    profile = state.plan.voices[cid] if state else library.voices[cid]
    chosen = provider_voice_input(project_id, cid, profile.voice_id)
    input_key = f"animation-preview-voice-{project_id}-{cid}"
    error_key = f"animation-voice-save-error-{project_id}-{cid}"
    # Keep later controls at the same Streamlit element paths when validation
    # appears or clears, including reruns confined to this editor fragment.
    error_slot = st.empty()
    if error_key in st.session_state:
        if chosen == st.session_state[error_key]:
            error_slot.error("voice id does not exist")
        else:
            st.session_state.pop(error_key, None)
    unsaved = bool(library and st.session_state.get(f"identity-draft-{project_id}", library.model_dump_json(by_alias=True, indent=2))
                   != library.model_dump_json(by_alias=True, indent=2))
    if unsaved:
        st.caption("Save or revert the identity library draft before changing voice IDs here.")
    target = (project_id, cid)
    task = st.session_state.get("workspace-task")
    saving = bool(busy() and task and task["label"] == f"Saving {cid} voice"
                  and st.session_state.get("character-voice-saving") == target)
    result_key = f"animation-voice-save-result-{project_id}-{cid}"

    def start_save():
        value = st.session_state[f"animation-preview-voice-{project_id}-{cid}"].strip()

        def save_voice():
            if state:
                plan = state.plan.model_copy(deep=True)
                plan.voices[cid].voice_id = value
                if not library:
                    return director.save_plan(plan)
                changed = library.model_copy(deep=True)
                changed.voices[cid] = plan.voices[cid]
            else:
                changed = library.model_copy(deep=True)
                changed.voices[cid].voice_id = value
            identity.save(changed, expected=library.model_dump_json(by_alias=True))
            return director.prepare() if state else changed

        def saved(_):
            st.session_state.pop(f"animation-plan-draft-{project_id}", None)
            st.session_state.pop(f"identity-draft-{project_id}", None)
            st.session_state[result_key] = value
            st.session_state.pop("character-voice-saving", None)

        def failed(exc):
            from .voice_validation import VoiceNotFoundError
            st.session_state.pop("character-voice-saving", None)
            if isinstance(exc, VoiceNotFoundError):
                # Completion runs before widgets are rendered on the next app
                # run, so restoring the saved input is safe for Streamlit.
                st.session_state[input_key] = profile.voice_id
                st.session_state[error_key] = profile.voice_id
                return True  # The field owns this error; keep page-level alerts stable.

        st.session_state.pop(result_key, None)
        st.session_state.pop(error_key, None)
        st.session_state["character-voice-saving"] = target
        launch(save_voice, f"Saving {cid} voice", saved, rerun_on_start=False, failure=failed, inline=True)

    from .voice_samples_ui import play_voice_button, voice_sample_player
    with st.container(horizontal=True, vertical_alignment="center"):
        st.button("Saving..." if saving else "Save character voice", key=f"animation-voice-save-{project_id}-{cid}",
                  disabled=busy() or unsaved or not chosen.strip() or chosen.strip() == profile.voice_id,
                  on_click=start_save,
                  help="Production ElevenLabs saves verify the Voice ID before changing the saved voice.")
        play_voice_button(manager, project_id, cid, profile.voice_id, demo=director.assets.demo)
    if st.session_state.get(result_key) == chosen.strip() == profile.voice_id:
        st.caption(":green[Voice saved.]")
    voice_sample_player(manager, project_id, cid, profile.voice_id, demo=director.assets.demo)


@st.fragment
def narrator_voice_editor(manager, project_id, *, demo=None):
    with st.container(key=f"asset-narrator-card-{project_id}", border=True):
        voice_design_heading(project_id, "narrator", narrator=True, rerun_app=True)
        narrator_voice_controls(manager, project_id, demo=demo)


def narrator_voice_controls(manager, project_id, *, demo=None):
    from .assets import AssetPipeline
    engine = AssetPipeline(manager, project_id, demo=demo)
    current = engine.load().voice_map.get("narrator", "")
    input_key = f"asset-narrator-{project_id}"
    chosen = provider_voice_input(project_id, "narrator", current, key=input_key, label="Narrator provider Voice ID")
    task = st.session_state.get("workspace-task")
    saving = bool(busy() and task and task["label"] == "Saving narrator voice"
                  and st.session_state.get("narrator-voice-saving") == project_id)
    result_key = f"asset-narrator-saved-{project_id}"

    def start_save():
        value = st.session_state[input_key].strip()

        def saved(_):
            st.session_state[result_key] = value
            st.session_state.pop("narrator-voice-saving", None)

        st.session_state.pop(result_key, None)
        st.session_state["narrator-voice-saving"] = project_id
        launch(lambda: engine.set_voice("narrator", value), "Saving narrator voice", saved, rerun_on_start=False, inline=True)

    from .voice_samples_ui import play_voice_button, voice_sample_player
    with st.container(horizontal=True, vertical_alignment="center"):
        st.button("Saving..." if saving else "Save narrator voice", key=f"save-narrator-voice-{project_id}",
                  disabled=busy() or not chosen.strip() or chosen.strip() == current, on_click=start_save,
                  help="Production ElevenLabs saves verify the Voice ID before changing the saved voice.")
        play_voice_button(manager, project_id, "narrator", current, demo=engine.demo, narrator=True)
    if st.session_state.get(result_key) == chosen.strip() == current:
        st.caption(":green[Voice saved.]")
    voice_sample_player(manager, project_id, "narrator", current, demo=engine.demo, narrator=True)


def character_voices(manager, project_id, *, demo=None):
    st.subheader("Character voices")
    st.caption("Assign a provider Voice ID to each character. Use the information icon for an ElevenLabs voice-creation prompt and instructions. Generate baseline and emotion samples, listen, then approve before dialogue tracks. Samples are AI-generated; offline samples are silent placeholders.")
    director = AnimationDirector(manager, project_id)
    from .identity import IdentityManager
    identity = IdentityManager(manager, project_id)
    library = identity.load()
    if demo is not None:
        director.assets.demo = demo
    state = director.load()
    if not state:
        if library:
            st.info("Assign a provider Voice ID to each character below. Then approve the identity library and use Generate next asset Stage to set up voices and auditions.")
            st.markdown("ElevenLabs: **My Voices → ⋯ → Copy voice ID**. [Voice Library help](https://elevenlabs.io/docs/eleven-creative/voices/voice-library/)")
            for cid, profile in library.voices.items():
                with st.container(border=True):
                    voice_design_heading(project_id, cid)
                    st.markdown(":red[Voice approval: Not approved — set up and approve audition samples.]")
                    character_voice_editor(manager, project_id, cid)
            return
        st.info("Complete and approve Storyboard, then set up character voices. This also creates the animation direction plan.")
        if st.button("Set up character voices", key=f"animation-voices-setup-{project_id}", disabled=busy()):
            launch(director.prepare, "Setting up character voices", lambda _: st.session_state.pop(f"animation-plan-draft-{project_id}", None))
        return
    st.caption("Use the same sample text for each emotion to compare delivery. Approval covers the baseline and all generated emotion samples; regenerating a sample requires approval again.")
    for cid, profile in state.plan.voices.items():
        voice_design_heading(project_id, cid)
        from .assets_workflow import character_voice_approval
        approved, reason = character_voice_approval(director.assets, state, cid)
        st.markdown(":green[Voice approval: Approved]" if approved else ":red[Voice approval: Not approved]")
        st.caption(reason)
        with st.expander("Voice settings & audition samples", key=f"animation-voice-panel-{project_id}-{cid}", on_change="rerun"):
            st.caption(f"Voice ID: {profile.voice_id or 'not selected'}")
            st.caption("Edit voiceId, genderPresentation, ageRange, pitchRange, accent, speakingStyle and baselineEmotion in the direction JSON. Do not infer gender from a voice name; listen before approving.")
            character_voice_editor(manager, project_id, cid)
            character_voice_auditions(manager, project_id, cid)


@st.fragment
def character_voice_auditions(manager, project_id, cid):
    with st.container(key=f"voice-auditions-{project_id}-{cid}"):
        character_audition_controls(manager, project_id, cid)


def character_audition_controls(manager, project_id, cid):
    director = AnimationDirector(manager, project_id)
    state = director.load(required=True)
    profile = state.plan.voices[cid]
    error_key = f"animation-audition-error-{project_id}-{cid}"
    error_slot = st.empty()
    if st.session_state.get(error_key):
        error_slot.error(st.session_state[error_key])

    def start(callback, label):
        st.session_state.pop(error_key, None)
        def failed(exc):
            st.session_state[error_key] = str(exc)
            return True
        launch(callback, label, rerun_on_start=False, inline=True, failure=failed)

    task = st.session_state.get("workspace-task")
    active_label = task["label"] if task and busy() else ""
    preview_label = f"Previewing {cid}"
    approval_label = f"Approving {cid} voice preview"
    sample_key = f"animation-preview-text-{project_id}-{cid}"
    from .voice_preview_text import ENGLISH_SAMPLE, preview_sample_text
    language = manager.load(project_id).language
    example = preview_sample_text(language, state.plan.performances, cid)
    default_key = sample_key + "-default"
    previous = st.session_state.get(default_key, ENGLISH_SAMPLE)
    if sample_key not in st.session_state or (st.session_state[sample_key] == previous and previous != example):
        st.session_state[sample_key] = example
    st.session_state[default_key] = example
    sample = st.text_area("Preview sample text", key=f"animation-preview-text-{project_id}-{cid}",
                          placeholder="Enter a natural line long enough for a 3–5 second preview.",
                          help=f"Default sample follows the project language ({language}). You can edit it before generating.")
    def generate_preview():
        text = st.session_state[sample_key]
        start(lambda: director.preview_voice(cid, text), preview_label)
    st.button("Generating voice preview..." if active_label == preview_label else "Generate voice preview",
              key=f"animation-preview-{project_id}-{cid}", disabled=busy() or not sample.strip(), on_click=generate_preview)
    emotion = st.selectbox("Sample emotion", ("happy", "sad", "angry", "afraid", "excited"), key=f"animation-emotion-choice-{project_id}-{cid}")
    def generate_emotion():
        text = st.session_state[sample_key]
        feeling = st.session_state[f"animation-emotion-choice-{project_id}-{cid}"]
        start(lambda: director.preview_voice(cid, text, emotion=feeling), f"Previewing {cid}: {feeling}")
    st.button("Generating emotion sample..." if active_label.startswith(f"Previewing {cid}: ") else "Generate emotion sample",
              key=f"animation-emotion-generate-{project_id}-{cid}", disabled=busy() or not sample.strip(), on_click=generate_emotion)
    for feeling, clip in state.emotion_previews.get(cid, {}).items():
        st.write(feeling.title())
        if clip.profile_hash != voice_signature(profile, manager.settings):
            st.caption("Outdated - regenerate for the current voice profile")
        audio = director.assets.local_path(clip.asset.file_path)
        if audio.exists():
            st.audio(str(audio))
    preview = state.previews.get(cid)
    if preview:
        st.write("Baseline sample")
        path = director.assets.local_path(preview.asset.file_path)
        if path.exists():
            st.audio(str(path))
        current = preview.profile_hash == voice_signature(profile, manager.settings) and preview.asset.provider == ("demo" if director.assets.demo else manager.settings.tts_provider)
        from .assets_workflow import character_voice_approval
        approval_current = character_voice_approval(director.assets, state, cid)[0]
        checked_voice = st.checkbox("I approve this character's voice and delivery", key=f"animation-voice-check-{project_id}-{cid}-{preview.file_hash}")
        st.button("Approving..." if active_label == approval_label else "Approve voice preview",
                  key=f"animation-voice-approve-{project_id}-{cid}", disabled=busy() or not current or not checked_voice or approval_current,
                  on_click=lambda: start(lambda: director.approve_voice(cid), approval_label))
        st.caption("Voice preview: " + ("outdated" if not current else "approved" if approval_current else "needs review"))
