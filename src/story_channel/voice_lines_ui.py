"""Compact character and line form for one speech track."""
import streamlit as st

from . import voice_lines
from .ui_tasks import busy


def editor(engine,state,subject,*,task=None):
    task=task or voice_lines.track(engine,state,subject)
    saved=voice_lines.signature(task)
    prefix=f'voice-line-{engine.project_id}-{subject}'
    character_key=prefix+'-character-'+saved
    text_key=prefix+'-text-'+saved
    choices=[c.character_id for c in engine.creative.artifact('characters').characters]
    if 'narrator' not in choices:
        choices.append('narrator')
    st.session_state.setdefault(character_key,task.speaker)
    st.session_state.setdefault(text_key,task.prompt)
    def reset():
        latest=voice_lines.track(engine,engine.load(),subject)
        st.session_state[character_key]=latest.speaker
        st.session_state[text_key]=latest.prompt
        st.session_state.pop(prefix+'-error',None)
        st.session_state.pop(prefix+'-success',None)
    def save():
        try:
            voice_lines.save(engine,subject,st.session_state[character_key],st.session_state[text_key],expected=saved)
        except (OSError,ValueError,RuntimeError) as exc:
            st.session_state[prefix+'-error']=str(exc)
        else:
            st.session_state.pop(prefix+'-error',None)
            st.session_state[prefix+'-success']=True
    with st.container(border=True):
        st.markdown('**Character Lines**')
        with st.form(prefix+'-form',border=False):
            character,line=st.columns([1,3])
            character.selectbox('Character',choices,key=character_key,disabled=busy())
            line.text_input('Line',key=text_key,disabled=busy(),max_chars=3000)
            save_col,reset_col=st.columns(2)
            save_col.form_submit_button('Save',on_click=save,disabled=busy())
            reset_col.form_submit_button('Reset',on_click=reset,disabled=busy())
        if error:=st.session_state.get(prefix+'-error'):
            st.error(error)
        elif st.session_state.get(prefix+'-success'):
            st.caption('Saved. Regenerate this track to use the selected character and line.')
        st.caption('Character IDs use their saved provider Voice IDs for speech. Narrator uses the narrator voice setting.')
