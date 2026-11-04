"""Preview and restore one location's floor-plan revisions in a dialog."""
import streamlit as st

from .dialog_ui import dialog,Deferred,request_open
from .ui_tasks import busy


def close():
    st.session_state.pop("floor-plan-revisions-target",None)


def close_and_refresh():
    close()
    st.rerun(scope="app")


def controls(engine,lid):
    def browse():
        st.session_state["dialog-context-manager"] = engine.manager
        st.session_state["floor-plan-revisions-target"] = {"project_id":engine.project_id,"lid":lid,"demo":engine.demo}
        st.session_state.pop(f"floor-plan-revision-choice-{engine.project_id}-{lid}",None)
        st.session_state.pop(f"floor-plan-revision-message-{engine.project_id}-{lid}",None)
        request_open()
    st.button("View / apply floor-plan revisions",key=f"floor-plan-revisions-{engine.project_id}-{lid}",on_click=browse)


@dialog("Floor-plan revisions",width="large",on_dismiss=close)
def revision_dialog(engine,lid):
    from .floor_plan_revisions import capture,revisions,preview,apply,signature
    from .floor_plan_guides import current_guides
    state = engine.load()
    capture(engine,lid,state.location_reference_sets[lid])
    saved = revisions(engine,lid)
    st.caption("A revision includes floor-plan text, object layout and its camera geometry. Previewing and applying use local processing, without an AI request. Applying reopens master approval; generated media is retained.")
    if not saved:
        st.info("No floor-plan revisions yet. Generate a layout or save reference settings first.")
    else:
        identity = st.selectbox("Floor-plan revision",[item["id"] for item in saved],
            format_func=lambda value:next(f"Revision {item['revision']} · {item['source']}" for item in saved if item["id"] == value),
            key=f"floor-plan-revision-choice-{engine.project_id}-{lid}")
        try:
            item = preview(engine,lid,identity)
        except (OSError,ValueError,RuntimeError) as exc:
            st.error("Cannot preview this revision: "+str(exc))
            st.button("Close",key="close-floor-plan-revisions",on_click=close_and_refresh)
            return
        data = item["layout"]
        st.markdown("**Floor plan**")
        st.write(data["floor_plan"] or "No floor-plan text")
        st.markdown("**Object layout**")
        st.dataframe([{"Object":name,"Position":position} for name,position in data["object_layout"].items()],hide_index=True,width="stretch")
        guides = item.get("guides")
        if guides:
            st.caption("Metric camera layout" if guides.get("geometrySource") == "metric" else "Schematic layout — not to scale")
            from .directional_masters import NAMES,LABELS
            images = [("Complete object layout",guides["floorPlan"]),
                *[(LABELS[name]+"-facing camera guide",guides["views"][name]["cameraGuide"])
                  for name in NAMES if name in guides["views"]]]
            columns = st.columns(3)
            for index,(label,relative) in enumerate(images):
                with columns[index % 3]:
                    st.image(str(engine.local_path(relative)),caption=label,width="stretch")
        expected = signature(state.location_reference_sets[lid])
        active = identity == expected and (not data["floor_plan"].strip() or not data["object_layout"] or
            bool(current_guides(engine,lid,state.location_reference_sets[lid])))
        message_key = f"floor-plan-revision-message-{engine.project_id}-{lid}"
        def apply_selected():
            try:
                with st.spinner("Applying floor-plan revision…"):
                    result = apply(engine,lid,identity,expected=expected)
                st.session_state[f"applied-floor-plan-{engine.project_id}-{lid}"] = result
                st.session_state[message_key] = ("success","Floor plan, object layout and camera guides applied. Review the master images before approving them again.")
            except (OSError,ValueError,RuntimeError) as exc:
                st.session_state[message_key] = ("error",str(exc))
        st.button("Applied" if active else "Apply floor-plan revision",key="apply-floor-plan-revision",disabled=busy() or active,on_click=apply_selected)
        if message := st.session_state.get(message_key):
            (st.success if message[0] == "success" else st.error)(message[1])
        with st.expander("Revision metadata",expanded=False):
            st.json({"revision":item["revision"],"createdAt":item["createdAt"],"cameraPlan":data["camera_plan"]})
    st.button("Close",key="close-floor-plan-revisions",on_click=close_and_refresh)


def show(manager,target):
    from .assets import AssetPipeline
    revision_dialog(Deferred(lambda:AssetPipeline(manager,target["project_id"],demo=target["demo"])),target["lid"])
