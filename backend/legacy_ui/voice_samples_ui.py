"""Play only the saved casting, keeping draft edits and existing playback intact."""
import streamlit as st

from .ui_tasks import busy, launch
from .voice_samples import saved_voice_sample


def play_voice_button(manager, project_id, speaker, voice_id, *, demo, narrator=False):
    subject = "_narrator" if narrator else speaker
    data_key = f"asset-voice-sample-{project_id}-{subject}"
    play_key = data_key + "-play"
    mode = "demo" if demo else manager.settings.tts_provider
    label = f"Loading voice sample: {project_id}:{subject}"
    task = st.session_state.get("workspace-task")
    loading = bool(busy() and task and task["label"] == label)

    def play():
        cached = st.session_state.get(data_key)
        if cached and cached["voice_id"] == voice_id and cached.get("mode") == mode:
            st.session_state[play_key] = st.session_state.get(play_key, 0) + 1
            return

        def loaded(sample):
            st.session_state[data_key] = dict(sample, mode=mode)
            st.session_state[play_key] = st.session_state.get(play_key, 0) + 1

        launch(lambda: saved_voice_sample(manager, project_id, speaker, voice_id, demo=demo, narrator=narrator),
               label, loaded, rerun_on_start=False, inline=True)

    st.button("Loading..." if loading else "Play voice", icon=":material/play_arrow:",
              key=f"voice-sample-play-{project_id}-{subject}", disabled=busy() or not voice_id.strip(),
              help="Play the saved voice's existing sample. Unsaved edits are not used; no new speech is generated.", on_click=play)


def voice_sample_player(manager, project_id, speaker, voice_id, *, demo, narrator=False):
    subject = "_narrator" if narrator else speaker
    data_key = f"asset-voice-sample-{project_id}-{subject}"
    sample = st.session_state.get(data_key)
    mode = "demo" if demo else manager.settings.tts_provider
    if sample and sample["voice_id"] == voice_id and sample.get("mode") == mode:
        sequence = st.session_state.get(data_key + "-play", 0)
        # Remount only on an explicit Play click, so repeat playback starts at
        # the beginning while ordinary edits/reruns leave the player alone.
        with st.container(key=f"voice-player-{project_id}-{subject}-{sequence}"):
            st.caption(f"{sample['kind']} · {sample['name']}")
            # This Streamlit version has no audio key. Its autoplay ID includes
            # alt, but ignores the containing container's key. Scope it to the
            # project, speaker and explicit play request even for shared audio.
            identity = f"{project_id}:{subject}:{sequence}"
            st.audio(sample["source"], format=sample["format"], autoplay=True,
                     alt=f"Sample of {sample['name']} for {'Narrator' if narrator else speaker} ({identity})")
