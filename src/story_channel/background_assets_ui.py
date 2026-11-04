from .dialog_ui import dialog
"""Single-panorama background cards with individual approval and draft creation."""
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import json
import streamlit as st

from .background_library import BackgroundLibrary, locations
from .ui_tasks import busy, launch, collect, generation_busy, find_task, stop_tasks, take_task_result
from .ui_tasks import asset_activity
from .background_panorama import panorama_mode
from .ui_panels import polling_fragment


def dismiss_removal():
    for key in list(st.session_state):
        if key.startswith("background-removal-target-"):
            st.session_state.pop(key, None)


@dialog("Remove background", on_dismiss=dismiss_removal)
def confirm_remove(engine, location):
    if isinstance(location, str):
        location = next(loc for loc in locations(engine.creative) if loc.location_id == location)
    st.write(f"Remove {location.name} from the current background library? Saved media and episode snapshots will be retained.")
    confirm, cancel = st.columns(2)
    if confirm.button("Remove background", key="confirm-remove-background", disabled=busy(), type="primary"):
        try:
            BackgroundLibrary(engine).remove(location.location_id)
            st.session_state.pop("background-removal-target-" + engine.project_id, None)
            st.rerun()
        except (ValueError, OSError) as exc:
            render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('background_assets_ui:20', locals()))
    if cancel.button("Cancel", key="cancel-remove-background"):
        st.session_state.pop("background-removal-target-" + engine.project_id, None)
        st.rerun()


def launch_background(engine, callback, label, *, service=None, subject=None):
    """Start before rendering controls; progress belongs to the background tab."""
    from threading import Event
    if generation_busy():
        return
    event = Event()
    def run():
        engine.stop_requested = event.is_set
        return callback()
    st.session_state.pop("background-ui-message-" + engine.project_id, None)
    task = launch(run, label, stop_event=event, progress_path=engine.state_path, asset_engine=engine,
           rerun_on_start=False, inline=True, queue_enabled=True, allow_parallel=True, service=service or engine.settings.image_provider)
    if task:
        task["stable_background_project"] = engine.project_id
        if subject:
            task["asset_subject"] = subject
        for location in locations(engine.creative):
            if label == "Approving background " + location.name:
                task["asset_subject"] = location.location_id
    return task


def background_cards(engine, state=None):
    target = "background-list-" + engine.project_id
    st.fragment(key=target)(_background_cards)(engine, target)


def _background_cards(engine, target):
    from .dialog_ui import show_pending_dialog
    if st.session_state.get("dialog-open-requested"):
        show_pending_dialog()
    from .asset_view_ui import belongs, poll_background_list, background_list_signature
    ids = [loc.location_id for loc in locations(engine.creative)]
    task = find_task(lambda item: (item.get("stable_background_project") == engine.project_id or
                     item.get("visual_design_project") == engine.project_id and item.get("visual_design_stage") == "backgrounds")
                     and not any(belongs(item, engine, lid) for lid in ids))
    if task and task["future"].done():
        collect(task)
        result = take_task_result(engine.project_id, [task.get("generation_button_key") or task.get("label","")])
        if result:
            st.session_state["background-ui-message-" + engine.project_id] = result
    result = take_task_result(engine.project_id,
        ["Adding background", "Importing panorama", "Saving location reference settings", "Saving location detail reference", "Generating location detail reference", "Removing location detail reference"],
        prefixes=("generate-background-", f"visual-generation-{engine.project_id}-backgrounds-", "Approving background "))
    if result:
        st.session_state["background-ui-message-" + engine.project_id] = result
    state = engine.load()
    library = BackgroundLibrary(engine)
    available = locations(engine.creative)
    snapshot_key = "background-ui-tasks-" + engine.project_id
    try:
        tasks = engine.tasks(state)
        st.session_state[snapshot_key] = tasks
    except (OSError, ValueError, KeyError) as exc:
        # Adding a location writes its catalog before rebuilding the identity
        # registry. Keep the card tree present throughout that brief interval.
        tasks = st.session_state.get(snapshot_key, [])
        if not busy():
            render_error(str(exc), project_path=engine.path, key="background-registry-error-" + engine.project_id)
    background_toolbar(engine)
    with st.container(border=True, key="background-add-" + engine.project_id):
        st.subheader("Add background")
        with st.form("add-background-" + engine.project_id, clear_on_submit=True):
            name_key, description_key = "background-new-name-" + engine.project_id, "background-new-description-" + engine.project_id
            st.text_input("Title", key=name_key)
            st.text_area("Description", key=description_key)
            type_key = "background-new-type-" + engine.project_id
            st.selectbox("Location type", ["DETAIL", "SIMPLE"], key=type_key,
                         help="DETAIL: floor plan and four directional masters. SIMPLE: detail prompt, optional guidance image and one master.")
            def add():
                name, description = st.session_state[name_key], st.session_state[description_key]
                kind = st.session_state[type_key]
                launch_background(engine, lambda: library.add(name, description, reference_type=kind), "Adding background")
            st.form_submit_button("Add draft", disabled=busy(), on_click=add)
        st.caption("Background drafts are reusable asset settings; approved World canon stays unchanged.")
    from .asset_view_ui import background_summary
    st.html('<style>[class*="st-key-background-card-"] summary code {font-size:0.75em !important;font-family:inherit;background:transparent;padding:0;}</style>')
    slots = []
    for location in available:
        approved, _ = background_summary(engine, location.location_id, state, tasks)
        slot = st.empty()
        slot.markdown("**"+location.name+"** · `"+location.location_id+"` · "+(":green[Approved]" if approved else ":orange[Pending]"))
        slots.append(slot)
    for slot, location in zip(slots, available):
        card_target = "background-subject-" + engine.project_id + "-" + location.location_id
        with slot.container():
            st.fragment(key=card_target)(_background_card)(engine, location.location_id, card_target)
    poll_background_list(engine, target, background_list_signature(engine))
    target = st.session_state.get("background-removal-target-" + engine.project_id)
    selected = next((loc for loc in available if loc.location_id == target), None)
    if selected:
        confirm_remove(engine, selected)


@polling_fragment
def background_toolbar(engine):
    from .asset_view_ui import background_summary
    from .storyboard_assets_ui import show_requests
    show_requests(engine.manager,engine.project_id,"background")
    state = engine.load()
    library = BackgroundLibrary(engine)
    available = locations(engine.creative)
    from .asset_view_ui import tasks_for_view
    tasks = tasks_for_view(engine, state)
    approved_count = sum(background_summary(engine, loc.location_id, state, tasks)[0] for loc in available)
    st.caption(f"{approved_count}/{len(available)} backgrounds approved. DETAIL: floor plan -> camera regions -> four directional masters. SIMPLE: detail prompt + optional guidance image -> one master.")
    message = st.session_state.get("background-ui-message-" + engine.project_id)
    if message and message[0][0] == "error":
        render_error(message[0][1], project_path=engine.path, task_key=message[1].get("task"), key="background-ui-error-" + engine.project_id)
    approve, stop = st.columns(2)
    approving = find_task(lambda task: task.get("stable_background_project") == engine.project_id and task.get("label","") == "Approving pending backgrounds" and not task["future"].done())
    approve.button("Approving..." if approving else "Approve all backgrounds", key="studio-approve-backgrounds",
                 disabled=busy() or approved_count == len(available),
                 on_click=lambda: launch_background(engine, lambda: library.approve(review_choices=review_choices(engine, engine.load())), "Approving pending backgrounds"))
    task = find_task(lambda item: item.get("stable_background_project") == engine.project_id)
    active = bool(task and task.get("stable_background_project") == engine.project_id and not task["future"].done())
    stopping = bool(active and task["stop_event"].is_set())
    stop.button("Stopping…" if stopping else "Stop after running tasks", key="stop-background-task-" + engine.project_id,
                disabled=not active or stopping, on_click=stop_tasks)


def _background_card(engine, lid, target, *, default_expanded=None, include_details=True, panel_key=None, check_asset=False):
    if st.session_state.get("dialog-open-requested"):
        from .dialog_ui import show_pending_dialog
        show_pending_dialog()
    from .asset_view_ui import belongs, subject_signature, poll_subject, background_summary
    from .ui_tasks import workspace_tasks
    message_key = f"background-card-message-{engine.project_id}-{lid}"
    for task in list(workspace_tasks()):
        if task["future"].done() and belongs(task, engine, lid):
            collect(task)
            result = take_task_result(engine.project_id, [task.get("generation_button_key") or task.get("label","")])
            if result:
                st.session_state[message_key] = result
    baseline = subject_signature(engine, "backgrounds", lid)
    state = engine.load()
    library = BackgroundLibrary(engine)
    from .asset_view_ui import tasks_for_view
    tasks = engine.tasks(state,references_only=True) if check_asset else tasks_for_view(engine, state)
    location = next((loc for loc in locations(engine.creative) if loc.location_id == lid), None)
    if location is None:
        return
    message = st.session_state.get(message_key)
    if message and message[0][0] == "error":
        render_error(message[0][1], project_path=engine.path, task_key=message[1].get("task"), key=message_key + "-error")
    lid = location.location_id
    approved, digest = background_summary(engine, lid, state, tasks)
    from .ui_panels import persistent_expander,asset_title
    label = "**"+location.name+"** · `"+lid+"` · "+(":green[Approved]" if approved else ":orange[Pending]")
    if check_asset:
        masters = [task for task in tasks if task.stage == "backgrounds" and library.subject_id(task.subject) == lid]
        label = asset_title(label,bool(masters) and all(engine._intact(state.records.get(task.key)) for task in masters))
    panel = persistent_expander(label,
                        key=panel_key or "background-card-" + engine.project_id + "-" + lid, expanded=not approved if default_expanded is None else default_expanded)
    if not panel.open:
        poll_subject(engine, "backgrounds", lid, target, baseline)
        return
    with panel:
        with st.container(horizontal=True):
            approving = find_task(lambda task: task.get("stable_background_project") == engine.project_id and task.get("asset_subject") == lid and task.get("label","").startswith("Approving background ") and not task["future"].done())
            from .shared_room import enabled
            needs_canonical_review = enabled(state.location_reference_sets.get(lid)) and not review_choices(engine,state).get(lid,{}).get("canonicalGeometryConfirmed")
            st.button("Approving..." if approving else "Approved" if approved else "Approve background", key="approve-background-" + lid,
                      disabled=busy() or approved or digest is None or needs_canonical_review,
                      help="Compare the eight raw/enhanced pairs and confirm geometry consistency below." if needs_canonical_review else None,
                      on_click=lambda lid=lid, name=location.name: launch_background(engine, lambda: library.approve(lid, review_choices=review_choices(engine, engine.load())), "Approving background " + name))
            def open_removal(lid=lid):
                from .dialog_ui import request_open
                st.session_state["background-removal-target-" + engine.project_id] = lid
                request_open()
            st.button("Remove background", key="remove-background-" + lid, disabled=busy(), on_click=open_removal)
        with st.expander("Description", expanded=False):
            from .files import input_hash
            description_key = f"background-description-{engine.project_id}-{lid}-"+input_hash(location.description)[:12]
            st.session_state.setdefault(description_key,location.description)
            draft = st.text_area("Background description",key=description_key,height=180,disabled=busy())
            changed = draft.strip() != location.description
            saving = find_task(lambda job:job.get("stable_background_project") == engine.project_id and
                job.get("asset_subject") == lid and job.get("label","") == "Saving background description" and not job["future"].done())
            with st.container(horizontal=True):
                def save_description():
                    text = st.session_state[description_key]
                    launch_background(engine,lambda:library.edit_description(lid,text,expected=location.description),
                                      "Saving background description",service="local",subject=lid)
                st.button("Saving..." if saving else "Save description",key="save-background-description-"+lid,
                          disabled=busy() or not changed or not draft.strip(),on_click=save_description)
                def revert_description():
                    st.session_state[description_key] = location.description
                st.button("Revert changes",key="revert-background-description-"+lid,
                          disabled=busy() or not changed,on_click=revert_description)
            if not draft.strip():
                st.caption(":red[Description cannot be empty.]")
            elif changed:
                st.caption(":orange[Unsaved changes]")
            st.caption("Used by AI layout generation. Edit or regenerate the layout to incorporate description changes into the camera-region masters.")
        from .location_references import reference_type_label
        from .simple_locations import save as save_location
        kind = reference_type_label(state.location_reference_sets.get(lid,{}).get("reference_type"))
        type_key = f"background-type-{engine.project_id}-{lid}-{kind}"
        selected = st.selectbox("Location type",["DETAIL","SIMPLE"],index=0 if kind == "DETAIL" else 1,key=type_key)
        if selected != kind:
            st.button("Save location type",key="save-background-type-"+lid,disabled=busy(),
                on_click=lambda:launch_background(engine,lambda:save_location(engine,lid,reference_type=st.session_state[type_key]),
                    "Saving location type",service="local",subject=lid))
            st.caption("Save to switch workflows. Existing images and revisions are retained; review the selected master(s) again.")
            poll_subject(engine,"backgrounds",lid,target,baseline)
            return
        if kind == "SIMPLE":
            from .simple_locations_ui import render
            render(engine,lid,state)
            poll_subject(engine,"backgrounds",lid,target,baseline)
            return
        from .visual_generation_ui import generation_section
        generation_section(engine, state, "backgrounds", lid,
                           available=bool(location.name.strip() and location.description.strip()))
        background_outputs(engine, lid)
        if lid in state.location_reference_sets:
            if include_details:
                from .location_details_ui import render as detail_references
                detail_references(engine,lid)
            poll_subject(engine, "backgrounds", lid, target, baseline)
            return
        with st.expander("Use an existing 360° panorama", expanded=False):
            upload_key = f"background-native-panorama-{engine.project_id}-{lid}"
            st.file_uploader("Native 2:1 panorama", type=["png", "jpg", "jpeg", "webp"],
                             key=upload_key, disabled=busy(),
                             help="A complete 360° × 180° equirectangular image, up to 50 MB. This is the actual background, not a style reference.")
            upload = st.session_state.get(upload_key)
            def use_panorama(lid=lid, upload=upload):
                if upload:
                    data = upload.getvalue()
                    launch_background(engine, lambda: library.import_panorama(lid, data), "Importing panorama")
            st.button("Use panorama", key="use-native-panorama-" + lid, disabled=busy() or upload is None,
                      on_click=use_panorama)
    poll_subject(engine, "backgrounds", lid, target, baseline)


def loading_result(label):
    # A background Future cannot hold a st.spinner context open. This persistent
    # result placeholder is replaced by the polling fragment when media arrives.
    st.html('<style>.background-result-loading{min-height:130px;display:flex;flex-direction:column;'
        'align-items:center;justify-content:center;gap:12px}.background-result-circle{width:30px;height:30px;'
        'border:3px solid rgba(128,128,128,.3);border-top-color:#ff4b4b;border-radius:50%;'
        'animation:background-result-spin .8s linear infinite}@keyframes background-result-spin{to{transform:rotate(360deg)}}'
        '</style><div class="background-result-loading" role="status" aria-live="polite">'
        '<span class="background-result-circle" aria-hidden="true"></span><span>' + label + '</span></div>')


def background_image_title(engine, title, record, subject):
    from .background_request_info import details, hover_text, render
    info = details(record)
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown("**" + title + "**", width="content")
        panel = st.popover("", icon=":material/info:", help=hover_text(info), width="content",
                           key=f"background-prompt-info-{engine.project_id}-{subject}", on_change="rerun")
        if panel.open:
            with panel:
                render(engine, info)


def background_outputs(engine, lid):
    state = engine.load()
    from .shared_room import enabled
    if enabled(state.location_reference_sets.get(lid)):
        from .shared_room_ui import outputs
        outputs(engine,lid)
        return
    from .dual_panorama import is_key
    if is_key(state.location_reference_sets.get(lid)):
        from .dual_panorama_ui import outputs
        outputs(engine, lid)
        return
    if lid in state.location_reference_sets:
        from .location_references import view_names
        spec = state.location_reference_sets[lid]
        names = view_names(spec)
        from .dual_panorama import is_key, MASTERS, seam_info
        if is_key(spec):
            from .dual_panorama import approved as panorama_approved, recheck
            qa = seam_info(engine, state, lid)
            if qa:
                from .dual_panorama import pair_ready
                if qa["passed"] and not pair_ready(engine, state, lid):
                    st.warning("Seam QA passed, but the world texture is missing or changed. Recheck seams & rebuild world texture before projection.")
                else:
                    (st.success if qa["passed"] else st.error)(f"Panorama seam QA: {qa['score']:.2f}" + (" — passed" if qa["passed"] else " — conflicts detected; regenerate the back panorama or correct the masters. Yaw projection is blocked."))
                with st.expander("Seam QA details", expanded=False):
                    st.json(qa)
            else:
                st.caption("Approve front panorama, then generate/approve back panorama. Seam QA runs before any local yaw projection.")
            st.button("Recheck seams & rebuild world texture", key="recheck-panorama-pair-"+lid,
                      disabled=busy() or not all(panorama_approved(engine, state, lid, name) for name in MASTERS),
                      on_click=lambda: launch_background(engine, lambda: recheck(engine, lid), "Checking panorama seams"))
        from .location_references import reference_type_label
        st.caption(f"{reference_type_label(spec['reference_type'])} · {len(names)} required references · clockwise yaw: 0° front, 90° right, 180° rear, 270° left")
        for index, name in enumerate(names):
            if index % 3 == 0:
                # Separate row containers keep later rows aligned even when
                # one tile has extra warnings, approval controls or a tall image.
                columns = st.columns(3, vertical_alignment="top")
            subject = lid + "__" + name
            with columns[index % 3]:
                record = state.records.get("backgrounds:" + subject)
                background_image_title(engine, name.replace("view_", "Yaw ") + ("°" if name.startswith("view_") else ""), record, subject)
                activity = asset_activity(engine, "backgrounds", subject)
                status = "Generating…" if activity == "running" else "Queuing…" if activity == "queued" else record.status if record else "pending"
                st.caption(f"{status} · revision {record.revision if record else 0}")
                if activity == "running":
                    loading_result("Generating…")
                elif record:
                    if record.asset and engine._intact(record):
                        st.image(str(engine.local_path(record.asset.file_path)))
                        review = record.asset.generation_params.get("yawReview", {})
                        if review.get("requiresReview"):
                            st.warning("Camera-angle review: this view looks almost identical to a supplied reference. The requested rotation may be missing.")
                            st.caption("Similar to: " + ", ".join(item["reference"].split(":", 1)[-1] for item in review.get("matches", [])))
                if record and record.error:
                    render_error(record.error, project_path=engine.path, task_key="assets:backgrounds:" + subject, key="location-error-" + subject)
                from .background_revisions_ui import actions
                if is_key(spec) and name in MASTERS:
                    from .background_revisions_ui import master_control
                    master_control(engine, lid, name)
                actions(engine, subject)
        return
    if panorama_mode(state, lid):
        panorama_panel(engine, lid, state)
    else:
        st.caption("Legacy four-image background retained in history. Generate replaces the active background with one panorama.")


def reference_set_settings(engine, library, lid, state):
    spec = state.location_reference_sets.get(lid, {})
    panel_key = f"floor-plan-settings-{engine.project_id}-{lid}"
    # Streamlit includes expanded= in expander identity. Keep its initial
    # default fixed as a draft becomes saved; track later toggles by key.
    initial = st.session_state.setdefault(panel_key+"-initial",bool(spec and spec.get("reference_type") in ("KEY", "KEY_360") and not spec.get("floor_plan")))
    with st.expander("Floor plan, object layout & reference settings",expanded=initial,key=panel_key,on_change="rerun"):
        prefix = "location-reference-set-" + engine.project_id + "-" + lid
        restored = st.session_state.pop(f"applied-floor-plan-{engine.project_id}-{lid}",None)
        if restored:
            state = engine.load()
            spec = state.location_reference_sets[lid]
            data = restored["revision"]["layout"]
            st.session_state[prefix+"-floor"] = data["floor_plan"]
            st.session_state[prefix+"-layout"] = json.dumps(data["object_layout"],ensure_ascii=False,indent=2)
            st.session_state[prefix+"-camera-plan"] = data["camera_plan"]
            st.session_state[prefix+"-camera-signature"] = data["camera_plan_signature"]
            st.session_state[prefix+"-guides"] = restored["guides"]
            st.session_state.pop(prefix+"-check-result",None)
        from .floor_plan_revisions_ui import controls
        controls(engine,lid)
        kind = "KEY_360"  # Saved compatibility marker; one location workflow.
        st.session_state.setdefault(prefix + "-floor", spec.get("floor_plan", ""))
        st.session_state.setdefault(prefix + "-layout", json.dumps(spec.get("object_layout", {}), ensure_ascii=False, indent=2))
        floor = st.text_area("Floor plan", key=prefix + "-floor", disabled=busy())
        layout = st.text_area("Object layout JSON", key=prefix + "-layout", disabled=busy())
        st.caption("Save the fixed camera center/height, walls, entrances and object positions. Camera regions select visible objects for the four directional masters; optional local details refine appearance.")
        def generate_layout(update="both"):
            floor_text = st.session_state[prefix+"-floor"]
            layout_text = st.session_state[prefix+"-layout"]
            selected_kind = kind
            try:
                parsed = json.loads(layout_text)
                from .location_references import LocationReferenceSet
                LocationReferenceSet(reference_type=selected_kind,floor_plan=floor_text,object_layout=parsed)
            except ValueError as exc:
                st.session_state["background-ui-message-"+engine.project_id] = (("error",str(exc)),{})
                return
            from .location_layout import generate_location_layout,generate_floor_plan_images,save_layout_result
            from .floor_plan_guides import source_signature
            from .floor_plan_revisions import signature as layout_signature
            expected_saved = layout_signature(engine.load().location_reference_sets[lid])
            from threading import Event
            def generated(result):
                st.session_state[prefix+"-floor"] = result.floor_plan
                st.session_state[prefix+"-layout"] = json.dumps(result.object_layout(),ensure_ascii=False,indent=2)
                st.session_state[prefix+"-camera-plan"] = result.camera_plan.model_dump(mode="json",by_alias=True) if result.camera_plan else None
                st.session_state[prefix+"-camera-signature"] = source_signature(result.floor_plan,result.object_layout())
                st.session_state[prefix+"-guides"] = result.camera_guides
                if update == "check":
                    st.session_state[prefix+"-check-result"] = (source_signature(result.floor_plan,result.object_layout()),result.references_match)
            mode_labels = {"both":"Generating location layout","check":"Checking reference consistency","image":"Generating floor plan image"}
            def run():
                if update == "image":
                    return generate_floor_plan_images(engine,lid,selected_kind,floor_text,parsed,camera_plan=staged_plan,expected_layout=expected_saved)
                if update == "both":
                    result = generate_location_layout(engine,lid,selected_kind,floor_text,parsed,render_guides=False)
                else:
                    result = generate_location_layout(engine,lid,selected_kind,floor_text,parsed,update=update,
                        camera_plan=staged_plan,render_guides=False)
                save_layout_result(engine,lid,result,expected_layout=expected_saved)
                return result
            staged_plan = st.session_state.get(prefix+"-camera-plan",spec.get("camera_plan"))
            if update == "image" and st.session_state.get(prefix+"-camera-signature",spec.get("camera_plan_signature")) != source_signature(floor_text,parsed):
                staged_plan = None
            task = launch(run,mode_labels[update],success=generated,rerun_on_start=False,
                          concurrent_safe=True,queue_enabled=True,stop_event=Event(),
                          resource=f"location-layout:{engine.project_id}:{lid}",project_path=engine.path,service="local" if update == "image" and staged_plan else "openai")
            if task:
                task.update(stable_background_project=engine.project_id,asset_subject=lid)
        generation_button("Generate floor plan & object layout with AI",key=prefix+"-generate-layout",
                          disabled=generation_busy(),on_click=generate_layout)
        from .location_layout_modify_ui import controls as modify_layout_controls
        modify_layout_controls(engine,lid,spec,prefix)
        with st.container(horizontal=True):
            generation_button("Check reference",key=prefix+"-check-reference",
                              disabled=generation_busy() or not floor.strip() or not layout.strip(),on_click=lambda:generate_layout("check"))
            generation_button("Generate floor plan image",key=prefix+"-generate-plan-image",
                              disabled=generation_busy() or not floor.strip() or not layout.strip(),on_click=lambda:generate_layout("image"))
        st.caption("Successful layout actions save their changes automatically. Generate floor plan image also saves the four directional camera guides required before master generation. OpenAI layout usage is recorded.")
        from .floor_plan_guides import source_signature,current_guides
        try:
            parsed_draft = json.loads(layout)
            draft_signature = source_signature(floor,parsed_draft)
        except ValueError:
            parsed_draft,draft_signature = None,None
        checked = st.session_state.get(prefix+"-check-result")
        if checked and checked[0] == draft_signature:
            st.success("References match." if checked[1] else "References reconciled. Review the updated drafts, then generate floor plan image.")
        pending_guides = st.session_state.get(prefix+"-guides")
        saved_guides = current_guides(engine,lid,spec) if draft_signature == source_signature(spec.get("floor_plan",""),spec.get("object_layout",{})) else None
        displayed_guides = saved_guides
        if pending_guides and st.session_state.get(prefix+"-camera-signature") == draft_signature:
            displayed_guides = pending_guides
        elif pending_guides:
            st.caption("Layout text changed. Check reference, then generate new floor-plan and camera-guide images.")
        if displayed_guides:
            st.markdown("**Floor-plan images & camera guides**")
            st.caption("Object colors and IDs match across all views. Blue arrows after labels show fixed object fronts; no arrow means unspecified. Labels stay above the camera overlay.")
            if displayed_guides.get("geometrySource") == "schematic":
                st.warning("Schematic, not to scale. Check reference to generate spatial metadata.")
            if not displayed_guides.get("floorPlan"):
                st.caption("Generate floor plan image again to create one complete object-layout image.")
            from .floor_plan_preview_ui import thumbnails
            thumbnails(engine,lid,displayed_guides,include_camera_guides=spec.get("generation_method") != "shared_3d")

    target = st.session_state.get("floor-plan-revisions-target")
    if target and target["project_id"] == engine.project_id and target["lid"] == lid:
        from .floor_plan_revisions_ui import revision_dialog
        revision_dialog(engine,lid)
    target = st.session_state.get("layout-modify-target")
    if target and target["project_id"] == engine.project_id and target["lid"] == lid:
        from .location_layout_modify_ui import show
        show(engine.manager,target)


def review_choices(engine, state):
    result = {}
    for location in locations(engine.creative):
        lid = location.location_id
        from .shared_room import enabled
        if enabled(state.location_reference_sets.get(lid)):
            from .room_canonical_ui import review_key
            digest = BackgroundLibrary(engine).digest(lid,state,engine.tasks(state))
            result[lid] = {"signature":digest,"canonicalGeometryConfirmed":st.session_state.get(review_key(engine,lid,digest),False)}
            continue
        record = state.records.get("backgrounds:" + lid + "__panorama")
        if record and record.asset:
            prefix = f"panorama-review-{engine.project_id}-{record.asset.asset_id}"
            result[lid] = {"assetId": record.asset.asset_id,
                "coverage360": st.session_state.get(prefix + "-coverage", False),
                "seamContinuous": st.session_state.get(prefix + "-seam", False)}
    return result


def panorama_panel(engine, lid, state):
    from .background_panorama import assess_panorama
    from .panorama_viewer_ui import panorama_viewer, seam_preview
    subject = lid + "__panorama"
    key = "backgrounds:" + subject
    record = state.records.get(key)
    background_image_title(engine, "Background panorama", record, subject)
    activity = asset_activity(engine, "backgrounds", subject)
    if activity == "running" or activity is None and record and record.status == "running" and busy():
        loading_result("Generating panorama…")
    elif activity == "queued":
        st.caption("Queued…")
    elif record and record.asset and engine.local_path(record.asset.file_path).is_file():
        path = engine.local_path(record.asset.file_path)
        assessment = assess_panorama(path)
        st.image(str(path), caption=f"Panorama candidate · revision {record.revision}")
        if assessment["isEquirectangularLayout"]:
            with st.expander("Inspect 360° coverage and start/end seam", expanded=False):
                panorama_viewer(path)
                seam_preview(path)
            if assessment["seamSuspected"]:
                st.warning("The image edges differ noticeably. Inspect the centered join for broken walls, shelves or lighting.")
            prefix = f"panorama-review-{engine.project_id}-{record.asset.asset_id}"
            reviewed = BackgroundLibrary(engine).panorama_reviewed(lid, state)
            if reviewed and not engine.demo:
                st.markdown(":green[360° coverage and seam reviewed for this revision]")
            else:
                st.checkbox("Full 360° surroundings are present, including behind the camera", key=prefix + "-coverage", disabled=busy())
                st.checkbox("The start and end connect continuously", key=prefix + "-seam", disabled=busy())
        else:
            st.warning(f"Returned {assessment['width']} × {assessment['height']}: not a 2:1 spherical panorama. "
                       "This image has been retained without stretching. It cannot be approved as a 360° background.")
        st.download_button("Download panorama", lambda path=path: path.read_bytes(), file_name=lid + "-panorama.png", mime="image/png",
                           key="download-background-panorama-" + lid)
    from .background_revisions_ui import actions
    actions(engine, subject)
    if record and record.error:
        render_error(record.error, project_path=engine.path, task_key=f"assets:{key}", key="panorama-error-" + engine.project_id + "-" + lid)
    st.caption("Only one image is saved; no four directional images are generated. Runway is a general image generator: "
               "a prompt or 2:1 shape alone cannot guarantee full 360° geometry or a seamless join. Review before approval.")

