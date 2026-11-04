"""Stable scene cards: update only the scene whose visible content changed."""
import streamlit as st
from .ui_panels import polling_fragment
from .files import input_hash


def signature(engine,sid):
    from .asset_view_ui import cached
    from .file_cache import version
    try:
        production_stamp = version(engine.state_path)
    except FileNotFoundError:
        production_stamp = None
    # Every scene watches the same production file. Reuse one read-only snapshot
    # per disk version instead of deep-copying the whole project on every timer.
    state = cached(engine,"scene-image-visible-state",production_stamp,engine.load)
    key = "scene_images:"+sid
    record = state.records.get(key)
    stamp = None
    if record and record.asset:
        try:
            stamp = version(engine.local_path(record.asset.file_path))
        except (OSError,ValueError):
            pass
    from .ui_tasks import workspace_tasks,asset_activity
    jobs = [(task.get("id"),task.get("phase"),task["future"].done())
        for task in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[]))
        if task.get("scene_image_project") == engine.project_id and task.get("asset_subject") == sid]
    return input_hash({"status":record.status if record else None,"error":record.error if record else None,
        "revision":record.revision if record else 0,"attempts":record.attempts if record else 0,
        "asset":record.asset.asset_id if record and record.asset else None,"file":stamp,
        "stale":record.stale_reason if record else None,"style":state.style_prompts.get(key),
        "modification":state.asset_modifications[key].model_dump(mode="json") if key in state.asset_modifications else None,
        "jobs":jobs,"activity":asset_activity(engine,"scene_images",sid)})


def render(engine):
    for scene in engine.creative.artifact("storyboard").scenes:
        target = f"scene-image-card-{engine.project_id}-{scene.scene_id}"
        st.fragment(key=target)(card)(engine,scene.scene_id,target)


def card(engine,sid,target):
    if st.session_state.get("dialog-open-requested"):
        from .dialog_ui import show_pending_dialog
        show_pending_dialog()
    from .character_assets_ui import draft_changed,generation_controls,character_details
    from .asset_sources import read_source
    from .ui_tasks import busy
    baseline = signature(engine,sid)
    state = engine.load()
    record = state.records.get("scene_images:"+sid)
    source = read_source(engine.creative,"scene_images",sid)
    with st.container(border=True,key=target+"-box"):
        st.markdown(f"**{sid} · {record.status if record else 'pending'} · revision {record.revision if record else 0}**")
        st.caption(source.scene.visual_description)
        has_asset = False
        if record and record.asset:
            try:
                path = engine.local_path(record.asset.file_path)
                if path.is_file():
                    has_asset = True
                    st.image(str(path),width="stretch")
                    st.download_button("Download",lambda path=path:path.read_bytes(),file_name=path.name,
                        key="download-asset-scene_images:"+sid,on_click="ignore")
                else:
                    st.warning("The scene image is missing.")
            except (OSError,ValueError) as exc:
                st.warning(str(exc))
        if record and record.error:
            from .error_report_ui import render_error
            render_error(record.error,project_path=engine.path,task_key="assets:scene_images:"+sid,key=target+"-error")
        if record and record.status == "stale":
            st.caption(record.stale_reason or "Scene inputs changed; regenerate this image when ready.")
        changed = draft_changed(engine,"scene_images",sid,source)
        generation_controls(engine,state,"scene_images",sid,has_asset,changed,
            available=not busy() and engine.can_generate("scene_images",state),stable_target=target)
        character_details(engine,source,record,stage="scene_images",subject=sid)
    watch(engine,sid,target,baseline)


@polling_fragment
def watch(engine,sid,target,baseline):
    try:
        changed = signature(engine,sid) != baseline
    except (OSError,ValueError):
        return  # Keep the displayed card during a transient metadata replacement.
    if changed:
        from .asset_view_ui import refresh_target
        refresh_target(target)
