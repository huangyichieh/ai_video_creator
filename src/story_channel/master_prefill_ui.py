"""Visible local input canvases before AI master completion."""
import streamlit as st


def previews(engine,lid):
    from .master_prefill import prepare
    from .dual_panorama import MASTERS
    from .location_bridges import require_approved
    state = engine.load()
    try:
        require_approved(engine,state,lid)
    except ValueError:
        st.caption("Approve both bridges to compute the master input previews.")
        return
    with st.expander("Master input previews · black regions will be filled",expanded=True):
        st.caption("Spherical inverse warping maps approved bridge pixels into each master before any AI request. Black space is unknown; opaque bridge cores stay exact, with a soft angular transition at their edges.")
        for column,name in zip(st.columns(2,vertical_alignment="top"),MASTERS):
            with column:
                st.markdown("**North master input**" if name == MASTERS[0] else "**South master input**")
                try:
                    metadata = prepare(engine,state,lid,name)
                    st.image(str(engine.local_path(metadata["canvas"])),caption="Actual input canvas · 2560 × 1440")
                    total = metadata["knownPixelCount"]+metadata["unknownPixelCount"]
                    st.caption(f"Known: {metadata['knownPixelCount']/total:.0%} · Unknown: {metadata['unknownPixelCount']/total:.0%}")
                    for region in metadata["overlaps"]:
                        low,high = region["masterYawRange"]
                        st.caption(f"{region['bridge'].replace('_',' ').title()}: yaw {low:g}° to {high:g}°")
                    with st.expander("Overlap coordinates & fill mask",expanded=False):
                        st.json(metadata)
                except (OSError,ValueError) as exc:
                    st.error(str(exc))
