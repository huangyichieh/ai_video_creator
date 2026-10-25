"""Local credential editing without exposing saved values to browser widgets."""
import os
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

import streamlit as st
from dotenv import dotenv_values, set_key

from .files import atomic_write
from .ui_tasks import busy

API_KEYS = ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "RUNWAYML_API_SECRET", "ELEVENLABS_API_KEY")
API_GROUPS = (("OpenAI", ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY")),
              ("Runway", ("RUNWAYML_API_SECRET",)),
              ("ElevenLabs", ("ELEVENLABS_API_KEY",)))
_save_lock = Lock()


def quota_fingerprint(key, current):
    return sha256(("reported-spending-v1\0" + key + current).encode()).hexdigest()


def checked_quota(key, current):
    from .provider_quota import fetch_quota
    fingerprint = quota_fingerprint(key, current)
    try:
        return fingerprint, fetch_quota(key, current), None
    except ValueError as exc:
        return fingerprint, None, str(exc)


def refresh_all_quotas(currents):
    # Independent provider reads run together; worker threads never access Streamlit.
    available = {key: value for key, value in currents.items() if value and key != "OPENAI_API_KEY"}
    if not available:
        return
    with st.spinner("Checking provider spending and balances…"):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {key: pool.submit(checked_quota, key, value) for key, value in available.items()}
            for key, future in futures.items():
                st.session_state["api-quota-" + key] = future.result()


def usage_metric(key, label, value, *, spent):
    with st.container(key=("api-spent-" if spent else "api-remaining-") + key):
        st.metric(label, value)


def quota_status(key, current):
    state_key = "api-quota-" + key
    fingerprint = quota_fingerprint(key, current)
    cached = st.session_state.get(state_key)
    if cached and cached[0] != fingerprint:
        st.session_state.pop(state_key, None)
        cached = None
    if st.button("Refresh spending" if key == "OPENAI_ADMIN_KEY" else "Refresh quota",
                 key="refresh-quota-" + key, disabled=not current):
        with st.spinner("Checking account quota…"):
            cached = checked_quota(key, current)
            st.session_state[state_key] = cached
    if not cached:
        if key == "OPENAI_ADMIN_KEY" and not current:
            st.caption("Save OPENAI_ADMIN_KEY to show reported monthly spending.")
        else:
            st.caption("Remaining quota: not checked. Save key changes before refreshing.")
    elif cached[2]:
        st.warning(cached[2])
    else:
        quota = cached[1]
        if quota.unit == "USD":
            usage_metric(key, "OpenAI reported spending (USD)", f"{quota.used:,.2f}", spent=True)
            st.caption("Month to date (UTC calendar month). Reported spending may lag.")
        elif quota.unit == "credits":
            usage_metric(key, "Remaining Runway credits", f"{quota.remaining:,.2f}", spent=False)
        else:
            spent, remaining = st.columns(2)
            with spent:
                usage_metric(key, "Used character quota", f"{quota.used:,.0f}", spent=True)
            with remaining:
                usage_metric(key, "Remaining character quota", f"{quota.remaining:,.0f}", spent=False)
            st.caption(f"Period allowance: {quota.limit:,.0f}. Overage billing may apply beyond this allowance.")
            if quota.resets_at:
                st.caption("Resets: " + quota.resets_at.strftime("%Y-%m-%d %H:%M UTC"))
        st.caption("Last checked: " + quota.checked_at.strftime("%Y-%m-%d %H:%M:%S UTC"))


def masked_key(value):
    """Never reveal a complete short credential in the saved-value preview."""
    return value[:4] + "…" + value[-4:] if len(value) > 8 else "••••••••"


def save_api_keys(path, changes):
    if not set(changes) <= set(API_KEYS):
        raise ValueError("Unknown API key setting")
    if any(not isinstance(value, str) or any(char in value for char in "\r\n\x00") for value in changes.values()):
        raise ValueError("API keys must be single-line text")
    changes = {key: value.strip() for key, value in changes.items()}
    with _save_lock:
        original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        # Let python-dotenv quote values correctly; replace the real file only
        # after the complete update succeeds. Preserve unrelated local settings.
        with TemporaryDirectory() as directory:
            draft = Path(directory) / ".env"
            draft.write_text(original, encoding="utf-8")
            for key, value in changes.items():
                set_key(str(draft), key, value, quote_mode="always")
            atomic_write(path, draft.read_text(encoding="utf-8"))
        os.environ.update(changes)


@st.dialog("API key", on_dismiss=lambda: st.session_state.update({"api-key-dialog-open": False}))
def api_key_dialog():
    path = Path.cwd() / ".env"
    st.caption("Saved locally in .env for this app. Changes apply to future requests without restarting. Leave a field blank to keep its saved value.")
    try:
        saved = dotenv_values(path, interpolate=False, encoding="utf-8-sig")
    except (OSError, ValueError):
        st.error("Cannot read the local .env file.")
        return
    st.markdown("**Production:** OpenAI (story), Runway (Kling O3 Pro / Act-Two / references), ElevenLabs (voices / SFX). The admin billing key is optional.")
    selectors = ", ".join(".st-key-api-provider-" + name for name, _ in API_GROUPS)
    st.markdown(f"""<style>
    {selectors} {{ background: #262730; border-radius: 0.7rem; padding: 1rem; color: #fafafa; }}
    {", ".join(".st-key-api-provider-" + name + " .stMarkdown p" for name, _ in API_GROUPS)} {{ color: #fafafa; }}
    [class*="st-key-api-spent-"] [data-testid="stMetricValue"] {{ color: #ff6b6b; }}
    [class*="st-key-api-remaining-"] [data-testid="stMetricValue"] {{ color: #4ade80; }}
    </style>""", unsafe_allow_html=True)
    currents = {key: os.environ.get(key, saved.get(key) or "").strip() for key in API_KEYS}
    if st.session_state.pop("api-quota-refresh-on-open", False):
        refresh_all_quotas(currents)
    drafts, removals = {}, {}
    for name, keys in API_GROUPS:
        with st.container(key="api-provider-" + name, border=True):
            st.markdown(f"**{name}**")
            for key in keys:
                drafts[key], removals[key] = api_key_fields(key, currents[key])
            quota_key = "OPENAI_ADMIN_KEY" if name == "OpenAI" else keys[0]
            quota_status(quota_key, currents[quota_key])
    st.caption("Spending and balances are account totals shared across projects. Opening this dialog refreshes saved keys; no generation requests are made.")
    st.caption("Keys apply to all Story Studio projects. Existing saved keys are never prefilled in these fields.")
    if busy():
        st.info("Wait for the current operation to finish before changing keys.")
    save, cancel = st.columns(2)
    if save.button("Save API keys", key="save-api-keys", type="primary", disabled=busy()):
        changes = {key: "" if removals[key] else value for key, value in drafts.items() if removals[key] or value.strip()}
        try:
            save_api_keys(path, changes)
            st.session_state["api-keys-saved"] = True
            st.session_state["api-key-dialog-open"] = False
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except OSError:
            st.error("Could not save API keys. Check file access and use single-line key values.")
    if cancel.button("Cancel", key="cancel-api-keys"):
        st.session_state["api-key-dialog-open"] = False
        st.rerun()


def api_key_fields(key, current):
    configured = bool(current)
    st.markdown(f"**{key}**")
    if configured:
        st.text("Current key: " + masked_key(current))
    else:
        st.markdown(":red[API key is not provided.]")
    draft = st.text_input(key, type="password", key=f"api-key-draft-{key}",
                          placeholder="Configured — enter a replacement" if configured else "Not configured",
                          disabled=busy(), label_visibility="collapsed")
    removal = st.checkbox("Clear " + key, key=f"api-key-clear-{key}", disabled=busy())
    return draft, removal


def api_key_button():
    os.environ.pop("SYNC_API_KEY", None)
    st.markdown("""<style>
    [data-testid="stAppDeployButton"] { anchor-name: --story-deploy; }
    .st-key-api-key-toolbar {
        position: fixed; top: 0.5rem; right: 9rem; width: 2.5rem;
        z-index: 999991; margin: 0;
    }
    .st-key-api-key-toolbar button {
        width: 2.5rem; min-height: 2rem; height: 2rem;
        padding: 0; border: 0; background: transparent;
    }
    @supports (position-anchor: --story-deploy) {
        .st-key-api-key-toolbar {
            position-anchor: --story-deploy;
            top: anchor(top, 0.5rem); right: calc(anchor(left, 9rem) + 0.5rem);
        }
    }
    @media (max-width: 640px) {
        .st-key-api-key-toolbar { right: 4rem; }
        @supports (position-anchor: --story-deploy) {
            .st-key-api-key-toolbar { right: calc(anchor(left, 4rem) + 0.5rem); }
        }
    }
    </style>""", unsafe_allow_html=True)
    def open_dialog():
        # Start with blank replacement fields on every opening.
        for key in API_KEYS:
            st.session_state.pop(f"api-key-draft-{key}", None)
            st.session_state.pop(f"api-key-clear-{key}", None)
        st.session_state["api-key-dialog-open"] = True
        st.session_state["api-quota-refresh-on-open"] = True
    with st.container(key="api-key-toolbar", width="content"):
        st.button("", icon=":material/key:", help="API key", key="open-api-keys", on_click=open_dialog)
    if st.session_state.get("api-key-dialog-open"):
        api_key_dialog()
    if st.session_state.pop("api-keys-saved", False):
        st.success("API keys saved locally. Future requests use the updated keys.")
