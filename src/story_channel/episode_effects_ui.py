"""Read-only, episode-scoped VFX and SFX inventories."""
import streamlit as st

from .storyboard_asset_check import document,sound_id,sounds,intact


def catalog(engine,kind):
    board=engine.assets.creative.artifact('storyboard').scenes
    scene_ids={scene.scene_id for scene in board}
    finishing=engine.finishing.load()
    rows={}
    if kind=='VFX':
        for effect in finishing.plan.effects:
            if effect.scene_id not in scene_ids:
                continue
            record=finishing.records.get('vfx:'+effect.effect_id)
            exists=bool(record and engine.finishing._intact(record))
            rows[effect.effect_id]={'ID':effect.effect_id,'Title':effect.kind,'Scenes':effect.scene_id,
                'Description':effect.prompt or effect.kind,'Start (s)':effect.at,'Duration (s)':effect.duration,
                'Status':'Disabled' if not effect.enabled else 'Ready' if exists else record.status if record else 'Not generated',
                'Preview':(record.asset.generation_params.get('previewFile') or record.asset.file_path) if exists else None}
        return list(rows.values())
    audio=sounds(engine.manager,engine.project_id)
    metadata=document(engine.path,'sfx_library.json').get('entries',{})
    def add(identity,title,sid,description,file,duration=None,status=None):
        if identity in rows:
            if sid not in rows[identity]['Scenes'].split(', '):
                rows[identity]['Scenes']+=', '+sid
            return
        ready=bool(file and engine.assets.local_path(file).is_file())
        rows[identity]={'ID':identity,'Title':title,'Scenes':sid,'Description':description,
            'Duration (s)':duration,'Status':'Ready' if ready else status or 'Not generated','Preview':file if ready else None}
    for scene in board:
        for text in scene.sfx:
            if not text.strip():
                continue
            identity=sound_id(text)
            item=audio.get(identity,{})
            definition=metadata.get(identity,{})
            add(identity,definition.get('title',text),scene.scene_id,definition.get('description',text),
                item.get('file') if intact(engine.path,item) else None,definition.get('duration'))
    render=engine.load()
    known_files={row['Preview']:key for key,row in rows.items() if row['Preview']}
    for sid,sound in render.music_plan.scenes.items():
        if sid not in scene_ids:
            continue
        for group in ('cues','ambience'):
            for index,cue in enumerate(getattr(sound,group)):
                key=f'sfx:{sid}:{group}:{index}'
                record=finishing.records.get(key)
                file=cue.file
                if not file and engine.finishing.task_intact(record,('sfx',group,index,cue)):
                    file=record.asset.file_path
                identity=known_files.get(file) or (sound_id(cue.prompt or cue.name) if cue.prompt or cue.name else key)
                add(identity,cue.name or group,sid,cue.prompt or cue.name,file,cue.duration,
                    record.status if record else None)
    return list(rows.values())


def render(manager,project_id,kind):
    from .rendering import RenderPipeline
    engine=RenderPipeline(manager,project_id)
    rows=catalog(engine,kind)
    st.subheader(kind)
    st.caption(f'{kind} items used by this episode. Asset creation stays on Assets / Check assets; final timing and mixing stay on Final.')
    if not rows:
        st.info(f'No {kind} items in this episode.')
        return
    st.dataframe([{key:value for key,value in row.items() if key!='Preview'} for row in rows],hide_index=True,width='stretch')
    for row in rows:
        with st.expander(row['Title']+' · '+row['Status'],expanded=False):
            st.caption(row['ID']+' · '+row['Scenes'])
            st.write(row['Description'])
            if row['Preview']:
                file=engine.assets.local_path(row['Preview'])
                if file.is_file():
                    st.audio(str(file)) if kind=='SFX' else st.video(str(file))
