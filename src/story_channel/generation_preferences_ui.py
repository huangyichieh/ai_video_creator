from .dialog_ui import dialog
"""Top-right generation preferences dialog."""
import streamlit as st
from .generation_preferences import MODELS, PROCESSES, MODEL_HELP, preferences, preset, save_preferences
from .ui_tasks import busy


def dismiss():
    st.session_state.pop("generation-preferences-open", None)


@dialog("Generation preferences", width="large", on_dismiss=dismiss)
def preferences_dialog(manager, project_id):
    owner = project_id or "new"
    prefix = f"generation-choice-{owner}-"
    choices = preferences(manager, project_id) if project_id else st.session_state.get("new-generation-preferences", preset())
    for process, model in choices.items():
        st.session_state.setdefault(prefix + process, model if model in MODELS else preset()[process])
    with st.tabs(["OpenAI"])[0]:
        economic, balance, info = st.columns([2, 2, 1])
        def choose(name):
            for process, model in preset(name).items():
                st.session_state[prefix + process] = model
        economic.button("Economic", key="generation-economic", on_click=choose, args=("Economic",), disabled=busy())
        balance.button("Balance", key="generation-balance", on_click=choose, args=("Balance",), disabled=busy())
        info.button("", icon=":material/info:", help=MODEL_HELP, key="generation-model-info")
        st.caption("Balance is the default: Sol for creative work and reviews; Luna for metadata. For difficult or important episodes, complex canon conflicts, or an optional deeper final story review, select Astra below.")
        mode_key = f"project-setting-{owner}-generation-mode"
        st.session_state.setdefault(mode_key, "Demo (offline)" if manager.settings.llm_provider == "demo" else "OpenAI")
        st.selectbox("Generation mode", ("OpenAI", "Demo (offline)"), key=mode_key, disabled=busy())
        st.caption("These choices control OpenAI text requests, including regeneration and prompt-guided edits. Image, video and speech generation retain their dedicated media models. Metadata choices apply when those fields are requested separately; fields generated together use their parent process model.")
        with st.container():
            for process, label in PROCESSES.items():
                st.selectbox(label, list(MODELS), format_func=MODELS.get, key=prefix + process, disabled=busy())
            if st.button("Save preferences", key="save-generation-preferences", type="primary", disabled=busy()):
                models = {process: st.session_state[prefix + process] for process in PROCESSES}
                try:
                    if project_id:
                        save_preferences(manager, project_id, models, demo=st.session_state[mode_key].startswith("Demo"))
                    else:
                        st.session_state["new-generation-preferences"] = models
                    dismiss()
                    st.session_state["generation-preferences-saved"] = True
                    st.rerun()
                except (OSError, ValueError, RuntimeError) as exc:
                    st.error(str(exc))


def generation_preferences_button(manager, project_id):
    st.markdown("""<style>
    .st-key-api-key-toolbar { anchor-name: --story-key-button; }
    .st-key-generation-preferences-toolbar {
        position: fixed; top: 0.5rem; right: 12rem; width: 12.5rem;
        z-index: 999991; margin: 0;
    }
    .st-key-generation-preferences-toolbar button {
        min-height: 2rem; height: 2rem; padding: 0 0.4rem;
        border: 0; background: transparent;
    }
    @supports (position-anchor: --story-key-button) {
        .st-key-generation-preferences-toolbar {
            position-anchor: --story-key-button;
            top: anchor(top, 0.5rem); right: calc(anchor(left, 12rem) + 0.5rem);
        }
    }
    @media (max-width: 640px) {
        .st-key-generation-preferences-toolbar { right: 7rem; width: 10rem; }
        .st-key-generation-preferences-toolbar button p { font-size: 0.8rem; }
    }
    </style>""", unsafe_allow_html=True)
    def open_dialog():
        from .dialog_ui import request_open
        request_open()
        st.session_state["api-key-dialog-open"] = False
        for key in list(st.session_state):
            if key.startswith("generation-choice-"):
                st.session_state.pop(key)
        st.session_state["generation-preferences-open"] = True
    with st.container(key="generation-preferences-toolbar", width="content"):
        st.button("Generation preferences", key="open-generation-preferences", on_click=open_dialog)
    if st.session_state.get("generation-preferences-open"):
        preferences_dialog(manager, project_id)
    if st.session_state.pop("generation-preferences-saved", False):
        st.success("Generation preferences saved for future requests.")
