"""Reuse World-owned background editors below the episode's asset checklist."""
import streamlit as st


def render(manager,project_id,rows):
    pending = [row for row in rows if not row["Ready"] and row.get("ID")]
    if not pending:
        return
    from .assets import AssetPipeline
    from .background_library import locations
    owner = manager.load(project_id).world_project_id or project_id
    engine = AssetPipeline(manager,owner)
    available = {loc.location_id for loc in locations(engine.creative)}
    state = engine.load()
    st.caption("Open an item to edit or generate it. Saved changes update the shared Assets library and synchronize into this episode automatically.")
    for row in pending:
        lid,separator,identity = row["ID"].partition("__detail_")
        target = f"check-background-editor-{project_id}-{row['ID']}"
        if lid not in available or separator and identity not in state.location_reference_sets.get(lid,{}).get("detail_references",{}):
            missing_draft(manager,project_id,engine,lid,identity if separator else None,row,target)
        elif separator:
            st.fragment(key=target)(_detail_card)(engine,lid,identity,target,project_id)
        else:
            from .background_assets_ui import _background_card
            st.fragment(key=target)(_background_card)(engine,lid,target,default_expanded=False,include_details=False,panel_key=target,check_asset=True)


def missing_draft(manager,project_id,engine,lid,identity,row,key):
    from .ui_tasks import busy,launch
    from .pipeline import Pipeline
    from .ui_panels import persistent_expander,asset_title
    panel = persistent_expander(asset_title(row["Asset"]+" · Draft not added",False),expanded=False,key=key)
    if not panel.open:
        return
    with panel:
        board = Pipeline(manager,project_id).artifact("storyboard")
        if identity:
            detail = next((item for item in board.detail_backgrounds if item.location_id == lid and item.detail_id == identity),None)
            if not detail:
                st.info("Add this detail on Assets or refresh the episode if it already exists in the shared library.")
                return
            st.write(detail.area)
            prompt = st.text_area("Detail prompt",value=detail.description,key=key+"-prompt",persist_state="session")
            def add():
                from .storyboard_assets import sync
                from .location_details import add_draft
                sync(manager,engine.project_id)
                add_draft(engine,lid,identity,detail.area,prompt)
            if st.button("Add detail draft",key=key+"-add",disabled=busy() or not prompt.strip()):
                launch(add,"Adding required detail draft")
        else:
            location = next((item for item in board.backgrounds if item.location_id == lid),None)
            if location:
                st.write(location.name)
                st.write(location.description)
                if st.button("Add background draft",key=key+"-add",disabled=busy()):
                    from .storyboard_assets import sync
                    launch(lambda:sync(manager,engine.project_id),"Adding required background draft")
            else:
                st.info("Add or restore this background on Assets, then refresh shared assets.")


def _detail_card(engine,lid,identity,target,episode_id):
    from .dialog_ui import show_pending_dialog
    if st.session_state.get("dialog-open-requested"):
        show_pending_dialog()
    from .ui_tasks import workspace_tasks,collect,take_task_result
    for task in list(workspace_tasks()):
        if task["future"].done() and task.get("stable_background_project") == engine.project_id and task.get("asset_subject") == lid+"__detail_"+identity:
            collect(task)
            result = take_task_result(engine.project_id,[task.get("generation_button_key") or task.get("label","")])
            if result and result[0][0] == "error":
                st.error(result[0][1])
    from .asset_view_ui import subject_signature,poll_subject
    baseline = subject_signature(engine,"backgrounds",lid)
    from .location_details_ui import render
    render(engine,lid,only=identity,default_expanded=False,scope=episode_id)
    poll_subject(engine,"backgrounds",lid,target,baseline)
