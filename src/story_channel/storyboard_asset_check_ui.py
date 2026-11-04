import streamlit as st
import pandas as pd
from .storyboard_asset_check import check,GROUPS
from .ui_tasks import busy,launch
from .ui_panels import polling_fragment


def audio_work_pending(project_id):
    from .ui_tasks import workspace_tasks
    return any(task.get("ui_owned_asset_project") == project_id
        and str(task.get("resource","")).startswith("storyboard-audio:") and not task["future"].done()
        for task in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[])))


@polling_fragment
def watch_shared(manager,project_id,baseline):
    from .check_asset_sync import owner,signature
    if not busy() and not audio_work_pending(project_id) and signature(manager,owner(manager,project_id)) != baseline:
        st.rerun(scope="app")


def checklist_table(selected,project_id,group):
    frame = pd.DataFrame([{k:v for k,v in row.items() if k not in ("Category","Ready","Approved","ContentHash","PreviewFiles","FileExists")} for row in selected])
    styled = frame.style.apply(lambda row: [
        "background-color: #174b35; color: #ecfdf5" if selected[row.name]["Ready"]
        else "background-color: #5c232b; color: #fff1f2"
    ]*len(row),axis=1)
    st.dataframe(styled,hide_index=True,width="stretch",key=f"check-assets-table-{project_id}-{group}")


@polling_fragment
def audio_checklist_table(manager,project_id,group):
    selected = [row for row in check(manager,project_id) if row["Category"] == group]
    from .audio_asset_cards import audio_tasks
    for row in selected:
        active = next((task for task in audio_tasks(project_id,"storyboard-audio:"+group+":"+row["ID"]) if not task["future"].done()),None)
        if active:
            row["Status"] = "Queued" if active.get("phase") in ("queued","waiting") else "Saving…" if active.get("phase") == "saving" else "Generating…"
    if selected:
        checklist_table(selected,project_id,group)


@polling_fragment
def prop_checklist_table(manager,project_id):
    selected = [row for row in check(manager,project_id) if row["Category"] == "Props"]
    if selected:
        checklist_table(selected,project_id,"Props")


def render(manager,project_id,rows=None,*,allow_animation=True):
    if manager.load(project_id).world_project_id and not busy() and not audio_work_pending(project_id):
        from .check_asset_sync import owner,signature,synchronize
        key = "check-assets-shared-version-"+project_id
        stamp = signature(manager,owner(manager,project_id))
        if st.session_state.get(key) != stamp:
            try:
                with st.spinner("Synchronizing shared assets…"):
                    synchronize(manager,project_id)
                st.session_state[key] = signature(manager,owner(manager,project_id))
                rows = None
            except (OSError,ValueError,RuntimeError) as exc:
                st.warning("Shared asset updates could not be synchronized: "+str(exc))
    rows = check(manager,project_id) if rows is None else rows
    missing = sum(not row["Ready"] for row in rows)
    st.subheader("Check assets")
    st.caption("Only assets used by this episode's storyboard are required. Created assets are automatically approved when their files are intact. Saved edits and generated files synchronize with Assets.")
    st.caption("🟢 File exists · 🔴 File missing or incomplete. No separate asset approval is needed.")
    if missing:
        st.warning(f"{missing} required asset(s) need attention. Animation stages are locked until all are ready.")
    else:
        st.success("All storyboard assets are ready. Continue to Scene images." if allow_animation
            else "All storyboard assets are ready. Asset preparation is complete.")
    manifest = manager.load(project_id)
    if manifest.world_project_id:
        st.button("Refresh shared assets",key="check-assets-refresh-"+project_id,disabled=busy() or audio_work_pending(project_id),
            on_click=lambda:refresh(manager,project_id))
    path = manager.project_path(project_id)
    for group in GROUPS:
        with st.container(border=True):
            st.markdown("### "+group)
            if group == "Background":
                st.caption("Backgrounds and selected detail backgrounds each appear as one item; repeated scene references are combined.")
                st.caption("Backgrounds and details are ready automatically when their required image files exist.")
            selected = [row for row in rows if row["Category"] == group]
            if not selected:
                st.caption("No required assets in this category.")
                continue
            if group == "SFX":
                from .storyboard_audio_ui import generate_all_sfx_button
                generate_all_sfx_button(manager,project_id,selected)
            if group in ("SFX","BGM"):
                audio_checklist_table(manager,project_id,group)
            elif group == "Props":
                prop_checklist_table(manager,project_id)
            else:
                checklist_table(selected,project_id,group)
            if group == "Props":
                from .storyboard_prop_removal_ui import control
                control(manager,project_id)
            for row in selected:
                if group in ("Props","BGM","SFX"):
                    continue  # Shared asset cards own previews, generation and approval.
                review_key = f"check-review-{project_id}-{row.get('Category')}-{row.get('ID','storyboard')}"
                from .ui_panels import persistent_expander,asset_title
                with persistent_expander(asset_title("Preview · "+row["Asset"],row.get("FileExists",row["Ready"])),expanded=False,key=review_key):
                    for relative in row.get("PreviewFiles",[]) if row["Ready"] else []:
                        file = path/relative
                        if group in ("BGM","SFX"):
                            st.audio(str(file))
                        else:
                            st.image(str(file),width=400)
                    if not row["Ready"]:
                        st.caption("Create this asset or refresh shared assets to make it ready.")
            if group == "Background":
                from .check_background_drafts_ui import render as background_drafts
                background_drafts(manager,project_id,selected)
            if group == "Props":
                from .check_prop_drafts_ui import render as prop_drafts
                prop_drafts(manager,project_id,selected)
            if group in ("SFX","BGM"):
                for row in selected:
                    key = f"check-sfx-{project_id}-{row['ID']}"
                    from .audio_asset_cards import card
                    card(manager,project_id,row,key,approval=True)
                if group == "BGM":
                    from .storyboard_audio_ui import pixabay_library
                    pixabay_library(manager,project_id)
            elif group == "Background":
                st.caption("Assets and Check assets share the saved library. Changes synchronize automatically; Refresh shared assets forces a recheck.")
    if manager.load(project_id).world_project_id:
        from .check_asset_sync import owner,signature
        watch_shared(manager,project_id,signature(manager,owner(manager,project_id)))
    return rows


def refresh(manager,project_id):
    from .check_asset_sync import synchronize
    launch(lambda:synchronize(manager,project_id),"Refreshing storyboard assets")


