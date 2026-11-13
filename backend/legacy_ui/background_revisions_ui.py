"""Per-image background actions and a deferred revision browser dialog."""
import streamlit as st

from .dialog_ui import dialog, Deferred, request_open
from .generation_ui import generation_button
from .ui_tasks import generation_busy, asset_activity


def close_revisions():
    st.session_state.pop("background-revisions-target", None)


def close_and_refresh():
    close_revisions()
    st.rerun(scope="app")


def actions(engine, subject,*,allow_generate=True):
    from .background_library import BackgroundLibrary
    from .background_assets_ui import launch_background
    lid = BackgroundLibrary(engine).subject_id(subject)
    state = engine.load()
    from .location_references import master_approved
    needs_master = "__view_" in subject and state.location_reference_sets.get(lid, {}).get("reference_type") in ("KEY", "KEY_360") and not master_approved(engine, state, lid)
    from .location_bridges import BRIDGES
    name = subject.rsplit("__",1)[-1]
    master = name in (*BRIDGES,"front_panorama","back_panorama")
    if name in ("front_panorama","back_panorama"):
        from .dual_panorama import approved
        needs_master = not all(approved(engine,state,lid,bridge) for bridge in BRIDGES)
    layout = state.location_reference_sets.get(lid, {})
    missing_layout = master and (not layout.get("floor_plan", "").strip() or not layout.get("object_layout"))
    from .floor_plan_guides import current_guides
    missing_guides = master and not current_guides(engine,lid,layout)
    if master:
        import json
        prefix = f"location-reference-set-{engine.project_id}-{lid}"
        try:
            draft_floor = st.session_state.get(prefix+"-floor",layout.get("floor_plan",""))
            draft_objects = json.loads(st.session_state.get(prefix+"-layout",json.dumps(layout.get("object_layout",{}))))
            missing_guides = missing_guides or draft_floor != layout.get("floor_plan","") or draft_objects != layout.get("object_layout",{})
        except (TypeError,ValueError):
            missing_guides = True
    def browse():
        st.session_state["dialog-context-manager"] = engine.manager
        st.session_state["background-revisions-target"] = {"project_id": engine.project_id, "subject": subject, "demo": engine.demo}
        request_open()
    def regenerate():
        task = launch_background(engine, lambda: engine.generate("backgrounds", subject=subject, force=True), "Regenerating background " + subject,
                                 service="openai" if master else "local" if "__view_" in subject or subject.endswith("__room_panorama") else None, subject=lid)
        if task:
            task["asset_subject"] = lid
    with st.container(horizontal=True, gap="small"):
        st.button("View / apply saved revisions", key=f"background-revisions-{engine.project_id}-{subject}", on_click=browse)
        label = ("Regenerate" if state.records.get("backgrounds:"+subject) and state.records["backgrounds:"+subject].asset else "Generate") + " (OpenAI)" if master else "Reproject" if "__view_" in subject else "Regenerate"
        if allow_generate:
            generation_button(label, key=f"background-regenerate-{engine.project_id}-{subject}",
                              disabled=generation_busy() or needs_master or missing_layout or missing_guides or not engine.can_generate("backgrounds", state),
                              activity=asset_activity(engine, "backgrounds", subject), on_click=regenerate)
    if needs_master:
        st.caption("Approve both East/West bridges before generating either master; approve masters and pass overlap QA before projection.")
    if missing_guides:
        st.caption("Generate floor plan image for the current references to enable master generation.")


def master_control(engine,lid,name="front_panorama"):
    target = f"panorama-approval-{engine.project_id}-{lid}-{name}"
    st.fragment(key=target)(_master_control)(engine,lid,name,target)


def _approval_stamp(engine,lid,name,target):
    from .ui_tasks import workspace_tasks,busy
    from .file_cache import version
    try:
        stamp = version(engine.state_path)
    except FileNotFoundError:
        stamp = None
    return (stamp,busy(),
            tuple((task["id"],task["future"].done(),task.get("phase")) for task in workspace_tasks()
                  if task.get("panorama_approval_control") == target))


def _master_control(engine,lid,name,target):
    from .dual_panorama import approve,digest
    from .ui_tasks import busy,workspace_tasks,collect,take_task_result
    from .background_assets_ui import launch_background
    button_key = f"approve-location-master-{engine.project_id}-{lid}-{name}"
    for task in list(workspace_tasks()):
        if task.get("panorama_approval_control") == target and task["future"].done():
            collect(task)
    message = take_task_result(engine.project_id,[button_key])
    if message:
        st.session_state[target+"-message"] = message
    pending = any(task.get("panorama_approval_control") == target and not task["future"].done() for task in workspace_tasks())
    state = engine.load()
    value = digest(engine,state,lid,name)
    approval = state.location_master_approvals.get(lid+":"+name)
    approved = bool(value and approval and approval.content_hash == value)
    record = state.records.get(f"backgrounds:{lid}__{name}")
    from .location_bridges import BRIDGES
    is_bridge = name in BRIDGES
    from .dual_panorama import approved as reference_approved
    anchors_ready = is_bridge or all(reference_approved(engine,state,lid,bridge) for bridge in BRIDGES)
    kind = "Bridge" if is_bridge else "Master"
    st.markdown(":orange[Approving "+kind.lower()+"...]" if pending else ":green["+kind+" approved]" if approved else ":orange["+kind+" needs approval]")
    review_key = f"review-panorama-layout-{engine.project_id}-{lid}-{record.asset.asset_id if record and record.asset else name}"
    label = ("The bridge preserves visible persistent objects, walls, windows, furniture and lighting at the saved camera position" if is_bridge else
             "Visible layout matches the floor plan and BOTH approved bridges; shared objects are unchanged without redesign or duplication")
    reviewed = st.checkbox(label,key=review_key,disabled=approved or pending or not anchors_ready,width="stretch",wrap=True)
    def approve_current():
        st.session_state.pop(target+"-message",None)
        expected = record.asset.asset_id
        task = launch_background(engine,lambda:approve(engine,lid,name,expected),"Approving "+name,service="local",subject=lid)
        if task:
            task.update(panorama_approval_control=target,generation_button_key=button_key,generation_button_project=engine.project_id)
    st.button("Approving..." if pending else "Approved" if approved else "Approve bridge" if is_bridge else "Approve master",key=button_key,
              disabled=busy() or approved or not reviewed or not value or not anchors_ready,on_click=approve_current)
    if message := st.session_state.get(target+"-message"):
        if message[0][0] == "error":
            from .error_report_ui import render_error
            render_error(message[0][1],project_path=engine.path,task_key=message[1].get("task"),key=target+"-error")
    # Idle approvals create no timer. During work the poller reads only a file
    # stamp and Future states, rather than repeatedly loading the whole project.
    if pending or (busy() and not approved) or any(task.get("panorama_approval_control") == target for task in workspace_tasks()):
        _poll_approval(engine,lid,name,target,_approval_stamp(engine,lid,name,target))


@st.fragment(run_every=.25)
def _poll_approval(engine,lid,name,target,baseline):
    from .ui_tasks import workspace_tasks
    completed = any(task.get("panorama_approval_control") == target and task["future"].done() for task in workspace_tasks())
    if completed or _approval_stamp(engine,lid,name,target) != baseline:
        from .asset_view_ui import refresh_target
        refresh_target(target)


@dialog("Background revisions", width="large", on_dismiss=close_revisions)
def revisions_dialog(engine, subject):
    target = f"background-revision-dialog-{engine.project_id}-{subject}"
    st.fragment(key=target)(revision_content)(engine, subject, target)
    st.button("Close", key="close-background-revisions", on_click=close_and_refresh)


def revision_content(engine, subject, target):
    from .character_assets_ui import revision_browser
    from .background_library import BackgroundLibrary
    from .asset_view_ui import belongs, subject_signature, poll_subject
    from .ui_tasks import workspace_tasks, collect, take_task_result
    lid = BackgroundLibrary(engine).subject_id(subject)
    message_key = f"background-revision-message-{engine.project_id}-{subject}"
    for task in list(workspace_tasks()):
        if task["future"].done() and belongs(task, engine, lid):
            collect(task)
            result = take_task_result(engine.project_id, [task.get("generation_button_key") or task["label"]])
            if result:
                st.session_state[message_key] = result
    baseline = subject_signature(engine, "backgrounds", lid)
    st.markdown("**" + subject.replace("__", " · ") + "**")
    message = st.session_state.get(message_key) or st.session_state.get(f"background-card-message-{engine.project_id}-{lid}")
    if message and message[0][0] == "error":
        from .error_report_ui import render_error
        render_error(message[0][1], project_path=engine.path, task_key=message[1].get("task"), key=message_key + "-error")
    from .dual_panorama import is_key
    projected = "__view_" in subject and is_key(engine.load().location_reference_sets.get(lid))
    revision_browser(engine, "backgrounds", subject, in_dialog=True, allow_apply=not projected,
                     apply_hint="Preview only: apply and approve panorama master revisions, then reproject views from that world texture." if projected else None)
    poll_subject(engine, "backgrounds", lid, target, baseline)


def show_revisions(manager, target):
    from .assets import AssetPipeline
    revisions_dialog(Deferred(lambda: AssetPipeline(manager, target["project_id"], demo=target["demo"])), target["subject"])
