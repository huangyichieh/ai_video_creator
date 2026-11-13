"""Field editor backed by the existing canonical JSON draft."""
import json

import streamlit as st

from .files import input_hash
from .ui_tasks import busy


FIELDS = ("name", "age", "appearance", "clothing", "personality", "background", "speakingStyle", "voiceId", "immutableTraits")


def character_fields(data, prefix, draft_key, *, on_edit=None):
    signature = input_hash(st.session_state[draft_key])
    sync_key = prefix + "-field-source"
    for field in FIELDS:
        key = prefix + "-field-" + field
        if st.session_state.get(sync_key) != signature or key not in st.session_state:
            value = data.get(field)
            st.session_state[key] = "\n".join(value or []) if field == "immutableTraits" else value
    st.session_state[sync_key] = signature

    def update(field):
        current = json.loads(st.session_state[draft_key])
        value = st.session_state[prefix + "-field-" + field]
        current[field] = [line.strip() for line in value.splitlines() if line.strip()] if field == "immutableTraits" else value
        st.session_state[draft_key] = json.dumps(current, ensure_ascii=False, indent=2)
        if on_edit:
            on_edit()

    # IDs are displayed as text so there is no editable widget for them.
    with st.container(border=True):
        st.markdown("**Character ID**")
        st.text(data["characterId"])
    name, age, voice = st.columns([2, 1, 2])
    name.text_input("Name", key=prefix + "-field-name", disabled=busy(), on_change=update, args=("name",))
    age.number_input("Age", key=prefix + "-field-age", min_value=0, step=1, disabled=busy(), on_change=update, args=("age",))
    voice.text_input("Logical voice ID", key=prefix + "-field-voiceId", disabled=busy(), on_change=update, args=("voiceId",),
        help="Internal character voice label. Configure the provider Voice ID separately in the Voice tab.")
    fields = (("appearance", "Appearance"), ("clothing", "Clothing"), ("personality", "Personality"),
              ("background", "Background"), ("speakingStyle", "Speaking style"), ("immutableTraits", "Fixed traits (one per line)"))
    for offset in range(0, len(fields), 2):
        for column, (field, label) in zip(st.columns(2), fields[offset:offset + 2]):
            column.text_area(label, key=prefix + "-field-" + field, height=100,
                             disabled=busy(), on_change=update, args=(field,))
