"""Format selection precedes scene images so safe framing applies from the start."""
import streamlit as st

from .animation_formats import delivery_formats,LANDSCAPE,PORTRAIT
from .ui_tasks import busy
from .error_report_ui import render_error


def settings(engine):
    options = engine.load().options
    choices = {"Portrait and landscape":[LANDSCAPE,PORTRAIT],"Portrait only":[PORTRAIT],"Landscape":[LANDSCAPE]}
    current = next(name for name,formats in choices.items() if formats == delivery_formats(options))
    with st.expander("Animation delivery settings",expanded=False):
        selected = st.selectbox("Animation formats",list(choices),index=list(choices).index(current),
            key="animation-delivery-"+engine.project_id,disabled=busy())
        both = len(choices[selected]) == 2
        st.caption("Landscape is generated first, then portrait is made from its center crop without another video API call. Keep important characters, faces, dialogue actions and story-critical props in the central 31.6% of the landscape frame throughout motion. Review both versions before approval."
            if both else "Generate native 9:16 portrait animation." if selected == "Portrait only" else "Generate 16:9 landscape animation.")
        if st.button("Save animation formats",key="save-animation-delivery-"+engine.project_id,disabled=busy()):
            try:
                updated = options.model_dump()
                updated.update(delivery_formats=choices[selected],portrait_safe_area=both)
                engine.configure(options=updated)
                st.rerun()
            except (OSError,ValueError,RuntimeError) as exc:
                render_error(str(exc),project_path=engine.path,key="animation-delivery-error-"+engine.project_id)
        st.caption("Changing formats updates scene-image framing and invalidates affected generated scenes and approvals. Final composition uses the primary format: landscape when both are selected. Both silent animation versions can be previewed and downloaded under Animated scenes.")
