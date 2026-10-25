import streamlit as st

from .animation_queue import AnimationQueue


@st.fragment(run_every=1)
def waiting_table(engine):
    queue = AnimationQueue(engine)
    try:
        data = queue.load()
        rows = queue.rows(data) if data else []
    except (OSError, ValueError):
        st.caption("Refreshing the current animation queue…")
        return
    st.markdown("**Waiting / generating tasks**")
    if not data:
        st.caption("Press Generate next stage or Generate animations (pipeline) to start a fresh queue.")
        return
    generating = sum(row["Status"] == "Generating…" for row in rows)
    queued = sum(row["Status"] == "Queued" for row in rows)
    finished = sum(row["Status"] in ("Completed", "Reused") for row in rows)
    st.caption(f"{data['mode']} · {data['status'].title()} · Generating: {generating} · Queued: {queued} · Finished: {finished}/{len(rows)}")
    st.dataframe(rows, hide_index=True, width="stretch", key=f"animation-waiting-table-{engine.project_id}-{data['run_id']}")
    if data.get("error"):
        st.error(data["error"])
