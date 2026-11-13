"""Compact, read-only presentation of structured generation data."""
import html
import re

import streamlit as st


def readable_key(key):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key)).replace("_", " ").capitalize()


def detail_rows(value, path=""):
    if isinstance(value, dict) and value:
        for key, item in value.items():
            label = (path + " › " if path else "") + readable_key(key)
            yield from detail_rows(item, label)
    elif isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
        for index, item in enumerate(value, 1):
            yield from detail_rows(item, f"{path} {index}")
    else:
        def display(item):
            return "Not set" if item is None else "Yes" if item is True else "No" if item is False else str(item)
        text = "\n".join(display(item) for item in value) if isinstance(value, list) else display(value)
        yield path or "Value", text or "—"


def compact_json(value):
    rows = "".join('<div class="story-json-field"><dt>' + html.escape(label) + "</dt><dd>" + html.escape(text) + "</dd></div>"
                   for label, text in detail_rows(value))
    st.markdown("""<style>
    .story-json-details {
        display: grid; grid-template-columns: minmax(0, 1fr);
        gap: 0.4rem; margin: 0; padding-right: 0.35rem;
        max-height: 20rem; overflow-y: auto; font-size: 0.9rem;
    }
    .story-json-field {
        display: grid; grid-template-columns: minmax(6rem, 24%) minmax(0, 1fr);
        gap: 0.75rem; padding: 0.45rem 0.6rem;
        border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 0.4rem;
    }
    .story-json-details dt { font-weight: 600; margin: 0; overflow-wrap: anywhere; }
    .story-json-details dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
    </style><dl class="story-json-details">""" + rows + "</dl>", unsafe_allow_html=True)
