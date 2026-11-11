"""Per-track speech edits, leaving approved visual/storyboard sources intact."""
from .files import input_hash
from .locking import project_lock


def apply_override(task,state,cast):
    task.voice_line_source_hash=input_hash({'speaker':task.speaker,'text':task.prompt,'voice':task.voice_id})
    override=state.voice_line_overrides.get(task.subject)
    if not override or override.get('sourceHash')!=task.voice_line_source_hash:
        return
    speaker=override['characterId']
    if speaker!='narrator' and speaker not in cast:
        raise ValueError('The saved voice-track character no longer exists. Reset its Character Lines selection.')
    task.speaker=speaker
    task.voice_id='narrator' if speaker=='narrator' else cast[speaker].voice_id
    task.prompt=override['text']


def track(engine,state,subject):
    task=next((t for t in engine.tasks(state) if t.stage=='voices' and t.subject==subject),None)
    if task is None:
        raise ValueError('This voice track no longer exists. Reload the current scene.')
    return task


def signature(task):
    return input_hash({'source':task.voice_line_source_hash,'speaker':task.speaker,'text':task.prompt})


def save(engine,subject,character_id,text,*,expected):
    if not isinstance(text,str) or not text.strip():
        raise ValueError('Enter a line before saving.')
    if len(text)>3000:
        raise ValueError('Keep this audio chunk within 3000 characters.')
    cast={c.character_id for c in engine.creative.artifact('characters').characters}
    if character_id not in cast|{'narrator'}:
        raise ValueError('Select an existing character ID.')
    with project_lock(engine.path):
        state=engine.load()
        current=track(engine,state,subject)
        if signature(current)!=expected:
            raise ValueError('This line changed in another editor. Reset to load the saved line.')
        if current.speaker==character_id and current.prompt==text:
            return state
        state.voice_line_overrides[subject]={'sourceHash':current.voice_line_source_hash,
            'characterId':character_id,'text':text}
        # Keep older audio as revision history; only its edited track and
        # dependent subtitles/composed audio need to be regenerated.
        for key in ('voices:'+subject,'subtitles:'+subject):
            if record:=state.records.get(key):
                record.status='stale'
                record.stale_reason='Character or spoken line changed. Regenerate this voice track and subtitles.'
        state.scenes.pop(current.scene_id,None)
        state.consistency_report={}
        engine._save(state)
        return engine._refresh()[0]
