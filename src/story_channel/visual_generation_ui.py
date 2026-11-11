"""One framed generation section with mutually exclusive prompt modes."""
from .image_preview_ui import image_preview
import streamlit as st

from .visual_generation import VisualGeneration, DESIGN_MODEL, EDIT_MODEL
from .generation_ui import generation_button
from .ui_tasks import busy, launch_assets, asset_activity, can_queue_asset, queue_asset, generation_busy, can_queue_work, launch
from .error_report_ui import render_error


def shortened_prompt_preview(engine, source):
    from .character_visual_compaction import cached_prompt
    prepared = cached_prompt(engine, source)
    if prepared:
        st.caption(f"Automatically shortened Runway prompt · {len(prepared.encode('utf-16-le')) // 2}/999 characters")
        st.text(prepared)
    elif not engine.demo:
        st.caption("Generate will shorten this prompt with OpenAI before requesting the image. Character detail stays unchanged.")


def generation_section(engine, state, stage, subject, *, changed=False, available=True):
    inputs = VisualGeneration(engine)
    # Character tabs rerun as fragments with their original arguments. Reload
    # saved input overlays rather than reusing the snapshot from the page run.
    state = inputs.apply(state.model_copy(deep=True))
    spec = inputs.get(stage, subject, state)
    key = f"visual-generation-{engine.project_id}-{stage}-{subject}"
    is_background = stage == "backgrounds"
    reference_set = state.location_reference_sets.get(subject) if is_background else None
    from .shared_room import enabled
    if enabled(reference_set):
        from .background_assets_ui import reference_set_settings
        from .background_library import BackgroundLibrary
        reference_set_settings(engine,BackgroundLibrary(engine),subject,state)
        return
    if reference_set and reference_set["reference_type"] in ("KEY", "KEY_360"):
        from .dual_panorama_ui import settings
        settings(engine, state, subject)
        return
    actual_subject = subject
    if is_background:
        from .background_panorama import panorama_mode
        if panorama_mode(state, subject):
            actual_subject = subject + "__panorama"
        elif reference_set:
            from .location_references import view_names
            actual_subject = subject + "__" + view_names(reference_set)[0]
            if reference_set["reference_type"] in ("KEY", "KEY_360"):
                from .dual_panorama import next_subjects
                pending = next_subjects(engine, state, subject)
                actual_subject = pending[0] if pending else actual_subject
    record = state.records.get(f"{stage}:{actual_subject}")

    def safely(callback):
        try:
            callback()
            st.session_state.pop(key + "-error", None)
        except (ValueError, OSError, RuntimeError) as exc:
            st.session_state[key + "-error"] = str(exc)

    def start(callback, label):
        if is_background:
            from .background_assets_ui import launch_background
            launch_background(engine, callback, label)
        else:
            launch_assets(engine, callback, label, rerun_on_start=False)

    with st.container(border=True, key=key):
        st.subheader("Generate location reference set" if reference_set else "Generate panorama" if is_background else "Generate model sheet")
        if is_background:
            from .background_assets_ui import reference_set_settings
            from .background_library import BackgroundLibrary
            reference_set_settings(engine, BackgroundLibrary(engine), subject, state)
            st.caption("DETAIL / SIMPLE generation uses the saved floor plan and object layout. Save edited settings first. Background prompts exclude people and characters.")
            if reference_set and reference_set["reference_type"] in ("KEY", "KEY_360"):
                st.info("DETAIL uses two approved north/south panorama masters and seam QA. Yaw views are projected locally at FOV 70°, pitch 0°.")
        with st.container(horizontal=True, gap="small"):
            st.button("Switch to Text prompt", key=key + "-text", type="primary" if spec["mode"] == "text" else "secondary",
                      on_click=lambda: safely(lambda: inputs.update(stage, subject, mode="text")))
            st.button("Switch to image prompt", key=key + "-image", type="primary" if spec["mode"] == "image" else "secondary",
                      on_click=lambda: safely(lambda: inputs.update(stage, subject, mode="image")))
        prompt_key = key + "-prompt"
        saved_key = key + "-saved-prompt"
        previous_saved = st.session_state.get(saved_key)
        if (prompt_key not in st.session_state or
                spec["prompt"] != previous_saved and
                (not st.session_state[prompt_key].strip() or st.session_state[prompt_key] == previous_saved)):
            st.session_state[prompt_key] = spec["prompt"]
        st.session_state[saved_key] = spec["prompt"]

        def save_prompt():
            # A transient empty widget value (or a user clearing the field to
            # replace it) must not erase the last saved prompt on disk.
            prompt = st.session_state.get(prompt_key, "")
            if prompt.strip():
                safely(lambda: inputs.update(stage, subject, prompt=prompt))
        from .style_rules import apply_rules
        from .style_rules import style_reference
        if spec["mode"] == "text":
            st.text_area("Background appearance / material notes" if reference_set else "Runway API prompt", key=prompt_key, height=240, persist_state="session",
                         on_change=save_prompt)
            if reference_set and reference_set["reference_type"] in ("KEY", "KEY_360"):
                st.caption("Appearance notes guide the 360 master panorama. Yaw images reuse their canonical world pixels without independent AI generation.")
            full_prompt = apply_rules(st.session_state[prompt_key], state.project_style_rules)
            style_valid = True
            try:
                full_prompt, _ = style_reference(engine, state, full_prompt, [])
            except (ValueError, OSError) as exc:
                style_valid = False
                st.warning(str(exc))
            if is_background:
                from .visual_generation import BACKGROUND_EMPTY
                full_prompt += "\n" + BACKGROUND_EMPTY
            count = len(full_prompt.encode("utf-16-le")) // 2
            st.caption(f"{count}/999 UTF-16 characters including shared Style rules. " +
                       ("These are appearance notes. Each yaw adds its required location references and camera/fill instructions; inspect the image's info icon for the submitted request."
                        if reference_set else "The shared style image is included; no design image is sent." if state.project_style_image else "No reference image is sent."))
            if state.project_style_rules or state.project_style_image or is_background:
                with st.expander("Source prompt before automatic shortening" if stage == "character_references" and count > 999 and not engine.demo else "Full Runway API prompt", expanded=False):
                    st.text(full_prompt)
            can_shorten = (stage == "character_references" or reference_set is not None) and not engine.demo
            valid = style_valid and bool(st.session_state[prompt_key].strip()) and (count <= 999 or can_shorten)
            if count > 999:
                st.warning("Generate will automatically shorten this character prompt, keeping personality to visible cues." if can_shorten else
                           "Shorten this prompt to 999 characters before generating. Automatic character shortening is available in Production mode.")
        else:
            images = state.background_images if is_background else state.character_images
            logical_subject = inputs.key(stage, subject).split(":", 1)[1]
            upload_key = key + "-upload-" + images.get(logical_subject, {}).get("hash", "empty")[:12]
            def uploaded():
                upload = st.session_state.get(upload_key)
                if upload is None:
                    return
                data = upload.getvalue()
                if is_background:
                    from .background_library import BackgroundLibrary
                    BackgroundLibrary(engine).set_image(subject, data)
                else:
                    engine.set_character_image(subject, data)
            st.file_uploader("Design reference image", type=["png", "jpg", "jpeg", "webp"], key=upload_key,
                             on_change=lambda: safely(uploaded), disabled=is_background and busy(),
                             help="This image defines the design and appearance sent to Runway, rather than acting only as a style guide.")
            valid = False
            try:
                path = inputs.selected_image(stage, subject, state)
                image_preview(str(path), caption="Reference image sent to Runway", width="stretch" if is_background else 256)
                valid = True
                def remove():
                    if is_background:
                        from .background_library import BackgroundLibrary
                        BackgroundLibrary(engine).set_image(subject, None)
                    else:
                        engine.set_character_image(subject, None)
                    st.session_state.pop(upload_key, None)
                st.button("Remove reference image", key=key + "-remove", on_click=lambda: safely(remove))
            except (KeyError, OSError, ValueError):
                st.caption("Upload an image or generate a design reference below.")
            from .visual_generation import CHARACTER_INSTRUCTION, BACKGROUND_INSTRUCTION
            instruction = ("Generate and approve one full 360 panorama from the saved floor plan, then locally project yaw views from the seam-checked image."
                           if reference_set else BACKGROUND_INSTRUCTION if is_background else CHARACTER_INSTRUCTION)
            full_prompt = apply_rules(instruction, state.project_style_rules)
            try:
                full_prompt, _ = style_reference(engine, state, full_prompt, [path] if valid else [])
            except (ValueError, OSError) as exc:
                valid = False
                st.warning(str(exc))
            if is_background:
                from .visual_generation import BACKGROUND_EMPTY
                full_prompt += "\n" + BACKGROUND_EMPTY
            with st.expander("Source instruction before automatic shortening" if stage == "character_references" and len(full_prompt.encode("utf-16-le")) // 2 > 999 and not engine.demo else "Runway API instruction", expanded=False):
                st.write(full_prompt)
            if len(full_prompt.encode("utf-16-le")) // 2 > 999:
                if stage == "character_references" and not engine.demo:
                    st.warning("Generate will automatically shorten the character instruction and Style rules for this request.")
                else:
                    valid = False
                    st.warning("Shorten shared Style rules: the full Runway instruction exceeds 999 characters.")
            with st.expander("Generate a design reference with OpenAI", expanded=False):
                st.text_area("Design description", key=prompt_key, height=180, persist_state="session",
                             on_change=save_prompt)
                extra_key = key + "-additional"
                st.text_area("Additional prompt (optional)", key=extra_key, height=100)
                def design():
                    description, extra = st.session_state[prompt_key], st.session_state.get(extra_key, "")
                    def run():
                        inputs.update(stage, subject, mode="image", prompt=description)
                        return inputs.design_reference(stage, subject, extra)
                    from threading import Event
                    task = launch(run, "Generating design reference", rerun_on_start=False, concurrent_safe=True,
                           queue_enabled=True, stop_event=Event(), resource=f"design:{engine.project_id}:{stage}:{subject}", project_path=engine.path, service="openai")
                    if task:
                        task.update(visual_design_project=engine.project_id, visual_design_stage=stage)
                generation_button("Generate design reference (OpenAI / " + DESIGN_MODEL + ")", key=key + "-design",
                                  disabled=generation_busy() or not st.session_state[prompt_key].strip(), on_click=lambda: safely(design))
                st.caption(("Creates a small front-view character reference (up to 512 px), not a model sheet. " if not is_background else "Creates an environment design reference. ")
                           + "Press Generate separately for the Runway result. Project Style rules apply; usage is recorded.")
        if key + "-error" in st.session_state:
            render_error(st.session_state[key + "-error"], project_path=engine.path, key=key + "-error-report")
        with st.container(horizontal=True, gap="small"):
            queueable = can_queue_work() or (not is_background and can_queue_asset(engine, stage, subject))
            def generate():
                # Capture the visible draft before the background job starts.
                selected_mode, prompt = spec["mode"], st.session_state[prompt_key]
                def run():
                    inputs.update(stage, subject, mode=selected_mode, prompt=prompt)
                    return engine.generate(stage, subject=subject, force=True)
                if busy() and queueable and not can_queue_work():
                    inputs.update(stage, subject, mode=selected_mode, prompt=prompt)
                    queue_asset(engine, stage, subject, force=True)
                else:
                    start(run, "Generating " + subject)
            generate_key = "generate-background-" + subject if is_background else "retry-asset-character_references:" + subject
            waiting_master = False
            missing_guides = False
            if reference_set and reference_set["reference_type"] in ("KEY", "KEY_360"):
                from .dual_panorama import next_subjects
                waiting_master = not next_subjects(engine, state, subject)
                from .floor_plan_guides import current_guides
                missing_guides = not current_guides(engine,subject,reference_set)
            generation_button("Queue asset" if busy() and queueable else "Generate", key=generate_key, activity=asset_activity(engine, stage, actual_subject),
                              disabled=waiting_master or missing_guides or changed or not valid or bool(reference_set and reference_set["reference_type"] in ("KEY", "KEY_360") and
                                  (not reference_set.get("floor_plan", "").strip() or not reference_set.get("object_layout"))) or (not queueable if busy() else not available),
                              on_click=lambda: safely(generate))
            if waiting_master:
                st.caption("Review the 360 master panorama below and resolve wrap QA before projecting yaw views.")
            if missing_guides:
                st.caption("Generate floor plan image below before creating master panoramas.")
            def modify():
                from .modification_ui import open_modification
                open_modification(engine.project_id, "asset", stage, subject=actual_subject,
                    expected=record.asset.asset_id if record and record.asset else None,
                    pending_generation_input={"mode": spec["mode"], "prompt": st.session_state[prompt_key]}, rerun=False)
            st.button("Modify (OpenAI / " + EDIT_MODEL + ")", key=f"modify-asset-{stage}:{actual_subject}",
                      disabled=busy() or changed or not record or not engine._intact(record) or bool(reference_set and reference_set["reference_type"] in ("KEY", "KEY_360")), on_click=modify,
                      help="Panorama repairs must preserve protected pixels; unrestricted AI edits are disabled for DETAIL." if reference_set and reference_set["reference_type"] in ("KEY", "KEY_360") else None)
        if changed:
            st.caption("Save or revert character detail changes before generating.")
        if busy():
            st.caption("Changes to the prompt or reference are saved for the next request. Running tasks keep their captured inputs.")
        if stage == "character_references" and len(full_prompt.encode("utf-16-le")) // 2 > 999:
            shortened_prompt_preview(engine, full_prompt)
