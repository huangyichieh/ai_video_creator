import streamlit as st
from threading import Event
from uuid import uuid4

from .assets import AssetPipeline
from .character_assets_ui import character_cards, asset_cards
from .ui_tasks import busy, launch, launch_assets
from .media_utils import file_hash


def assets_page(manager, project_id, *, animations=False):
    st.header("Scene inputs & Voice generation" if animations else "Reusable assets")
    allowed_stages = ("scene_images", "voices", "subtitles", "consistency") if animations else ("character_references", "backgrounds")
    st.caption("Character references and backgrounds generate one task at a time. Other asset stages allow up to 5 simultaneous tasks. Use Queue asset on other cards in the same stage; approval gates and stage dependencies still apply.")
    default = AssetPipeline(manager, project_id)
    st.session_state.setdefault(f"asset-mode-{project_id}", "Demo (offline)" if default.demo else "OpenAI")
    mode = st.selectbox("Asset generation mode", ("OpenAI", "Demo (offline)"), index=None,
                        key=f"asset-mode-{project_id}", disabled=busy(),
                        format_func=lambda value: "Production (configured image / voice providers)" if value == "OpenAI" else value)
    engine = AssetPipeline(manager, project_id, demo=mode.startswith("Demo"))
    try:
        state = engine.load() if busy() else engine.inspect()
    except ValueError as exc:
        st.info(str(exc))
        state = engine.load()
        for record in state.records.values():
            if record.status == "succeeded":
                record.status = "stale"
    except (OSError, RuntimeError) as exc:
        st.info(str(exc))
        return
    if engine.demo:
        st.info("Offline demo creates labelled placeholder images, silent voice tracks, and sample timestamps. Use OpenAI for production assets.")
    st.caption("Voices are AI-generated. Subtitle timings come from recorded audio; visual identity is confirmed during human review.")
    if not animations:
        from .identity_ui import identity_library
        identity_library(manager, project_id, engine=engine, state=state)
    manifest = manager.load(project_id)
    characters_ready = bool(manifest.steps.get("characters") and manifest.steps["characters"].status == "succeeded")
    generic_key = f"asset-generic-prompt-{project_id}"
    st.session_state.setdefault(generic_key, state.generic_prompt)

    def save_generic():
        try:
            engine.set_generic_prompt(st.session_state[generic_key])
        except (OSError, ValueError, RuntimeError) as exc:
            st.session_state["workspace-task-message"] = ("error", str(exc))

    st.text_area("Generic type prompt — text (optional with a reference image)", key=generic_key, on_change=save_generic, disabled=busy(),
                 placeholder="e.g. Cinematic 3D animation, warm lighting, adventurous tone, natural expressive voices.",
                 help="Shared genre, tone, and style for every asset. Saved locally when you leave this field. Individual style prompts add more detail.")
    upload_epoch = st.session_state.get(f"style-upload-epoch-{project_id}", 0)
    upload_key = f"style-upload-{project_id}-{upload_epoch}"

    def save_reference():
        uploaded = st.session_state.get(upload_key) or []
        try:
            engine.set_style_references([item.getvalue() for item in uploaded])
        except (OSError, ValueError, RuntimeError) as exc:
            st.session_state["workspace-task-message"] = ("error", str(exc))

    st.file_uploader("Shared style reference images (up to 3, optional with text)", type=["png", "jpg", "jpeg", "webp"],
                     accept_multiple_files=True, max_upload_size=20, key=upload_key, on_change=save_reference, disabled=busy(),
                     help="Choose up to 3 images to replace the saved selection. All guide characters, backgrounds and scenes. Maximum 20 MB per image.")
    st.caption(f"{len(state.style_references)} / 3 shared style reference images saved. Uploading replaces the saved selection.")

    def remove_reference(index=None):
        try:
            current = engine.load()
            remaining = [engine.local_path(item["file"]).read_bytes() for i, item in enumerate(current.style_references) if i != index] if index is not None else []
            engine.set_style_references(remaining)
            st.session_state[f"style-upload-epoch-{project_id}"] = upload_epoch + 1
        except (OSError, ValueError, RuntimeError) as exc:
            st.session_state["workspace-task-message"] = ("error", str(exc))

    if state.style_references:
        columns = st.columns(len(state.style_references))
        for index, (column, reference) in enumerate(zip(columns, state.style_references)):
            with column:
                path = engine.local_path(reference["file"])
                if path.is_file() and file_hash(path) == reference["hash"]:
                    st.image(str(path), caption=f"Shared style reference {index + 1}", width=240)
                else:
                    st.warning("Reference file missing or modified. Remove it or upload it again.")
                st.button("Remove reference " + str(index + 1), key=f"remove-style-reference-{index}", disabled=busy(),
                          on_click=remove_reference, args=(index,))
        st.button("Remove style reference image" if len(state.style_references) == 1 else "Remove all style reference images",
                  key="remove-style-reference", disabled=busy(), on_click=remove_reference)
    st.caption("Use text, an image, or both. The shared style guides all visuals; per-asset prompts add details. Scene images also use approved character and background references. Image references do not set voice delivery; add text for voice tone.")
    if not engine.has_generic_prompt(state):
        st.info("Add text, a reference image, or both before generating assets.")
    if animations:
        if state.next_stage not in allowed_stages:
            st.info("Complete reference approvals on Assets and the creative stages on Story & Settings first. Subtitle alignment runs with voice production; subtitle review is on Final.")
        next_title = state.next_stage.replace("_", " ").title()
        runnable = state.next_stage in allowed_stages and engine.can_generate(state.next_stage, state)
    else:
        from .assets_workflow import next_assets_action
        workflow = next_assets_action(engine, state)
        next_title, runnable = workflow.title, workflow.runnable
        st.info(workflow.detail)
        from .identity import IdentityManager
        library = IdentityManager(manager, project_id).load()
        if library and st.session_state.get(f"identity-draft-{project_id}", library.model_dump_json(by_alias=True, indent=2)) != library.model_dump_json(by_alias=True, indent=2):
            runnable = False
            st.caption("Save or revert identity library changes before continuing generation.")
    st.markdown("Next step:  \n**" + next_title + "**")
    configure_voices = not animations and workflow.stage in ("voice_settings", "narrator", "voice_approval")
    approve_identity = not animations and workflow.stage == "identity_approval"
    continue_animations = not animations and workflow.stage == "complete"

    def open_voice_configuration():
        st.session_state[f"page-tab-assets-{project_id}"] = "Voice Profiles"
        st.session_state[f"asset-voice-highlight-{project_id}"] = True
        st.session_state[f"asset-voice-scroll-{project_id}"] = uuid4().hex

    def open_identity_approval():
        st.session_state[f"identity-panel-{project_id}"] = True
        st.session_state[f"identity-approval-highlight-{project_id}"] = True
        st.session_state[f"identity-approval-scroll-{project_id}"] = uuid4().hex

    def open_animations():
        st.session_state["workspace_page"] = "Animations"

    next_clicked = st.button("Generate next asset Stage: " + next_title,
                             key="continue-assets-to-animations" if continue_animations else "generate-next-asset-stage", type="primary", disabled=busy() or not (runnable or configure_voices or approve_identity or continue_animations),
                             on_click=open_animations if continue_animations else open_voice_configuration if configure_voices else open_identity_approval if approve_identity else None,
                             help="Open Voice Profiles and highlight the provider Voice ID fields." if configure_voices else
                                  "Open the identity library and go to its approval button." if approve_identity else None)
    # Settings and approval links navigate locally without starting generation.
    next_clicked = next_clicked and not (configure_voices or approve_identity or continue_animations)
    run_clicked = st.button("Generate assets until review", disabled=busy() or not runnable,
                            help="Run work on this page until settings, human approval, or a page handoff is required.")
    if state.next_stage == "waiting_for_creative":
        st.info("Character assets are ready. Complete the creative pipeline, including story and storyboard approvals and Prompts, to unlock other assets.")
    selection_key = f"asset-selected-records-{project_id}-{animations}"
    checked_records = set(st.session_state.get(selection_key, []))
    rows = [{"Regenerate": f"{record.stage}:{record.subject}" in checked_records, "Stage": record.stage, "Subject": record.subject,
             "Status": "Generating\u2026" if record.status == "running" else record.status,
             "Revision": record.revision, "Attempts": record.attempts, "Error": record.error or ""}
            for record in state.records.values() if record.stage in allowed_stages]
    regenerable = {f"{record.stage}:{record.subject}" for record in state.records.values() if record.stage in allowed_stages}
    if not animations:
        from .assets_status import assets_status_rows
        task = st.session_state.get("workspace-task")
        rows = assets_status_rows(engine, state, running_label=task["label"] if task and busy() else "")
        rows = [{"Regenerate": f"{row['Stage']}:{row['Subject']}" in checked_records if f"{row['Stage']}:{row['Subject']}" in regenerable else None, **row} for row in rows]
        st.caption("Assets status & approval checklist. Approval reflects saved reviews; it cannot be granted by editing this table. Optional items do not block the pipeline. Revision/attempts apply to generated image records.")
    if rows:
        table_key = f"production-selection-{project_id}-{animations}"
        row_keys = [f"{row['Stage']}:{row['Subject']}" for row in rows]

        def remember_selection():
            selected_keys = set(st.session_state.get(selection_key, []))
            for index, edits in st.session_state[table_key].get("edited_rows", {}).items():
                if "Regenerate" in edits and int(index) < len(row_keys) and row_keys[int(index)] in regenerable:
                    key = row_keys[int(index)]
                    if edits["Regenerate"]:
                        selected_keys.add(key)
                    else:
                        selected_keys.discard(key)
            st.session_state[selection_key] = sorted(selected_keys)

        selected_rows = st.data_editor(rows, hide_index=True, width="stretch",
            key=table_key, on_change=remember_selection, disabled=busy() or [name for name in rows[0] if name != "Regenerate"],
            column_config={"Regenerate": st.column_config.CheckboxColumn("Regenerate", default=False)})
        selected = [row for row in selected_rows if row["Regenerate"] and f"{row['Stage']}:{row['Subject']}" in regenerable]
        stages = {row["Stage"] for row in selected}
        st.caption("Check assets in one stage, then regenerate selected. Character references and backgrounds run one at a time; other stages allow up to 5. Later stages still require approval.")
        if not animations:
            st.caption("Regenerate applies to existing image records only. Configure profiles, generate auditions, and approve using the corresponding tab controls.")
        if len(stages) > 1:
            st.info("Select items from a single stage per batch.")
        if st.button("Regenerate selected", key="regenerate-selected-assets", disabled=busy() or not selected or len(stages) != 1):
            stage = next(iter(stages))
            subjects = [row["Subject"] for row in selected]
            launch_assets(engine, lambda: engine.generate(stage, subjects=subjects, force=True))
    st.caption(f"Running: {state.generation_active}/{state.max_parallel_tasks} · Queued: {state.generation_queued} · Finished: {state.generation_finished}/{state.generation_total}")
    engine.on_progress = None

    def perform(callback):
        launch(callback, "Asset operation")

    if (next_clicked or run_clicked) and not animations:
        from .assets_workflow import generate_assets_page
        samples = {key.removeprefix(f"animation-preview-text-{project_id}-"): value
                   for key, value in st.session_state.items() if key.startswith(f"animation-preview-text-{project_id}-")}
        event = Event()
        engine.stop_requested = event.is_set
        def finished(_):
            st.session_state.pop(f"identity-draft-{project_id}", None)
            st.session_state.pop(f"animation-plan-draft-{project_id}", None)
        launch(lambda: generate_assets_page(engine, until_review=run_clicked, samples=samples),
               "Generating Assets: " + next_title, finished, stop_event=event, asset_engine=engine)
    elif next_clicked:
        launch_assets(engine, lambda: engine.generate(state.next_stage))
    elif run_clicked:
        def run_scoped():
            current = engine.inspect()
            while current.next_stage in allowed_stages and not engine.stop_requested():
                current = engine.generate(current.next_stage)
            return current
        launch_assets(engine, run_scoped)
    manifest = manager.load(project_id)
    gate = {"character_review": "characters", "background_review": "backgrounds", "asset_review": "assets"}.get(state.next_stage)
    if gate and ((animations and gate == "assets") or (not animations and gate != "assets")):
        st.info({"characters": "Review character appearance and clothing below, then approve character references.",
                 "backgrounds": "Review the recurring locations below, then approve backgrounds.",
                 "assets": "Review scene visuals, speech, subtitle timing, and consistency findings before approving this batch."}[gate])
        with st.container(key=f"animation-input-approval-{project_id}" if animations else f"asset-reference-approval-{project_id}-{gate}"):
            note = st.text_input("Asset review note (optional)", key=f"asset-note-{project_id}-{gate}")
            accepted = st.checkbox("I reviewed these assets and approve continuing", key=f"asset-accept-{project_id}-{gate}-{engine._gate_hash(gate, state, engine.tasks(state))}")
            if st.button("Approve " + gate, key=f"approve-assets-{gate}", type="primary", disabled=busy() or not characters_ready or not accepted):
                perform(lambda: engine.approve(gate, note))
    if state.next_stage == "complete":
        st.success("Asset batch approved. Scene visuals, voice tracks, and audio-timed subtitles are ready for composition.")
    if animations:
        tabs = st.tabs(["Scene images", "Voice generation", "Scene input QA"],
                       key=f"page-tab-scene-inputs-{project_id}", on_change="rerun")
        with tabs[0]:
            asset_cards(engine, state, "scene_images")
        with tabs[1]:
            asset_cards(engine, state, "voices")
            st.markdown("**Fixed voice mapping**")
            st.json(state.voice_map)
            st.caption("Manage voice profiles and approved auditions on Assets - Voice Profiles. Audio alignment is generated with the scene inputs; review subtitles on Final.")
        with tabs[2]:
            markdown = engine.path / "asset_review.md"
            if markdown.exists():
                st.markdown(markdown.read_text(encoding="utf-8"))
            st.json(state.consistency_report)
    else:
        tabs = st.tabs(["Character Reference Packs", "Locations / Backgrounds", "Costumes", "Props", "Voice Profiles",
                        "Style References", "Music / SFX library", "Reusable generated images"],
                       key=f"page-tab-assets-{project_id}", on_change="rerun")
        with tabs[0]:
            character_cards(engine, state)
        with tabs[1]:
            asset_cards(engine, state, "backgrounds")
        from .identity import IdentityManager
        library = IdentityManager(manager, project_id).load()
        for tab, name in ((tabs[2], "costumes"), (tabs[3], "props")):
            with tab:
                st.caption("Edit and approve these registered items in the Identity library above. Stable IDs are reused in scene direction.")
                if library:
                    st.json(library.model_dump(mode="json", by_alias=True).get(name, []))
                else:
                    st.info("Build the identity library after Characters to register " + name + ".")
        highlight_voices = configure_voices and st.session_state.get(f"asset-voice-highlight-{project_id}", False)
        with tabs[4], st.container(key="asset-voice-configuration", border=highlight_voices):
            if highlight_voices:
                st.info("Review the red/green approval status above each character. Listen to and approve every required voice; save any missing character or narrator Voice IDs here.")
            from .animation_ui import character_voices, narrator_voice_editor
            character_voices(manager, project_id, demo=engine.demo)
            narrator_voice_editor(manager, project_id, demo=engine.demo)
        if highlight_voices:
            st.html("""<style>
                .st-key-asset-voice-configuration {
                    outline: 2px solid var(--st-primary-color, #58a6ff);
                    outline-offset: 4px;
                    border-radius: 0.75rem;
                    scroll-margin-top: 6rem;
                }
            </style>""")
        scroll_request = st.session_state.pop(f"asset-voice-scroll-{project_id}", None)
        if scroll_request:
            # Streamlit can commit tab selection and its contents in separate
            # frames. Wait for the selected panel to be visible before scrolling.
            st.html('<script data-scroll-request="' + scroll_request + '">' + """
                (() => {
                    let attempts = 0;
                    const scrollToVoices = () => {
                        const target = document.querySelector('.st-key-asset-voice-configuration');
                        if (target && target.getClientRects().length && target.offsetHeight) {
                            target.scrollIntoView({behavior: 'smooth', block: 'start'});
                        } else if (++attempts < 60) {
                            setTimeout(scrollToVoices, 50);
                        }
                    };
                    requestAnimationFrame(scrollToVoices);
                })();
            </script>""", unsafe_allow_javascript=True)
        with tabs[5]:
            st.caption("The shared text prompt and reference image above guide character, background and scene generation. Per-asset prompts add details.")
            if library:
                if library.style.strip():
                    st.write(library.style)
                else:
                    st.info("No additional identity style notes are saved. Use the shared style prompt and reference images above.")
        with tabs[6]:
            from .workspace_ui import audio_library
            audio_library(manager, project_id)
        with tabs[7]:
            for record in state.records.values():
                if record.stage in ("character_references", "backgrounds", "scene_images") and record.asset:
                    asset = record.asset
                    st.write(f"{record.subject} - revision {record.revision}")
                    path = engine.local_path(asset.file_path)
                    if path.exists():
                        st.image(str(path), width=320)
