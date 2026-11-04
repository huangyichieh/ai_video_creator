"""Early audio generation and provenance-backed Pixabay selection."""
import streamlit as st

from .generation_ui import generation_button
from .ui_tasks import generation_busy,launch


def start(manager,project_id,callback,label,service,resource):
    from threading import Event
    st.session_state.pop("audio-item-error-"+project_id+"-"+resource,None)
    task = launch(callback,label,project_path=manager.project_path(project_id),service=service,
        resource=resource,stop_event=Event(),concurrent_safe=True,allow_parallel=True,queue_enabled=True,rerun_on_start=False,inline=True)
    if task:
        task["ui_owned_asset_project"] = project_id
    return task


def sfx_batch_activity(project_id):
    from .ui_tasks import workspace_tasks
    tasks = list(workspace_tasks())+list(st.session_state.get("workspace-task-queue",[]))
    active = [task for task in tasks if task.get("sfx_batch_project") == project_id and not task["future"].done()]
    queued = sum(task.get("phase") in ("queued","waiting") for task in active)
    return len(active)-queued,queued


def missing_sfx(manager,project_id,rows):
    from .audio_assets import binding
    from .storyboard_asset_check import intact
    return [row for row in rows if row["Category"] == "SFX" and not (
        intact(manager.project_path(project_id),binding(manager,project_id,"SFX",row["ID"]))
        if manager is not None else row.get("FileExists",row.get("Ready",False)))]


def queue_all_sfx(manager,project_id,rows):
    from .storyboard_audio import generate
    from .ui_tasks import workspace_tasks
    st.session_state.pop("sfx-batch-errors-"+project_id,None)
    seen = set()
    for row in missing_sfx(manager,project_id,rows):
        if row["Category"] != "SFX" or row["ID"] in seen:
            continue
        identity = row["ID"]
        seen.add(identity)
        resource = "storyboard-audio:SFX:"+identity
        tasks = list(workspace_tasks())+list(st.session_state.get("workspace-task-queue",[]))
        if any(task.get("ui_owned_asset_project") == project_id and task.get("resource") == resource and not task["future"].done() for task in tasks):
            continue
        prefix = f"check-sfx-{project_id}-{identity}"
        from .check_asset_sync import audio_settings
        preferences = audio_settings(manager,project_id,"SFX",identity) if manager is not None else {}
        prompt = st.session_state.get(prefix+"-generate-prompt",preferences.get("prompt","")).strip() or row.get("Description",row["Asset"])
        duration = st.session_state.get(prefix+"-duration",preferences.get("duration",3.))
        loop = st.session_state.get(prefix+"-loop",preferences.get("loop",False))
        def generate_one(identity=identity,prompt=prompt,duration=duration,loop=loop):
            # An upload or another session may have filled the asset while queued.
            from .audio_assets import binding
            from .storyboard_asset_check import intact
            if manager is not None and intact(manager.project_path(project_id),binding(manager,project_id,"SFX",identity)):
                return
            from .check_asset_sync import save_audio_settings
            save_audio_settings(manager,project_id,"SFX",identity,prompt,duration,loop)
            return generate(manager,project_id,"SFX",identity,prompt,duration,loop=loop)
        task = start(manager,project_id,generate_one,
            "Generating storyboard SFX: "+row["Asset"],"elevenlabs",resource)
        if task:
            task["sfx_batch_project"] = project_id


def generate_all_sfx_button(manager,project_id,rows):
    target = "sfx-batch-control-"+project_id
    st.fragment(key=target)(_generate_all_sfx_button)(manager,project_id,rows,target)


def batch_tasks(project_id):
    from .ui_tasks import workspace_tasks
    return [task for task in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[]))
        if task.get("sfx_batch_project") == project_id]


def batch_signature(project_id):
    return [(task.get("id",id(task)),task.get("phase"),task["future"].done()) for task in batch_tasks(project_id)]


def collect_batch(project_id):
    from .ui_tasks import collect
    finished = [task for task in batch_tasks(project_id) if task["future"].done()]
    for task in finished:
        if not task["future"].cancelled() and (error := task["future"].exception()) and not isinstance(error,InterruptedError):
            errors = st.session_state.setdefault("sfx-batch-errors-"+project_id,{})
            errors[task["resource"]] = {"message":str(error),"path":getattr(error,"error_report_project_path",None) or task.get("error_report_path"),
                "task":getattr(error,"error_report_task",None) or task.get("error_report_task")}
        collect(task)
    return finished


def _generate_all_sfx_button(manager,project_id,rows,target):
    collect_batch(project_id)
    running,queued = sfx_batch_activity(project_id)
    missing = missing_sfx(manager,project_id,rows)
    generation_button("Generate all missing assets (ElevenLabs)",key="check-sfx-generate-all-"+project_id,
        activity="running" if running else "queued" if queued else None,
        disabled=generation_busy() or not missing,
        help="Generates only missing required SFX using each item's current prompt, duration and loop setting. Existing files (including unapproved sounds) and queued/running items are skipped.",
        on_click=lambda:queue_all_sfx(manager,project_id,rows))
    if running or queued:
        st.caption(f"SFX: {running} generating · {queued} queued. Uses the shared ElevenLabs concurrency limit.")
        from .ui_tasks import stop_tasks
        st.button("Stop after running tasks",key="check-sfx-stop-"+project_id,
            disabled=bool(st.session_state.get("workspace-stopping")),on_click=stop_tasks,
            help="Finish current requests, cancel queued work, and retain completed sounds and costs.")
    if batch_tasks(project_id):
        watch_batch(project_id,target,batch_signature(project_id))
    errors = st.session_state.get("sfx-batch-errors-"+project_id,{})
    if errors:
        from .ui_panels import persistent_expander
        panel = persistent_expander(f"Failed sounds ({len(errors)})",key="sfx-batch-errors-panel-"+project_id)
        if panel.open:
            with panel:
                for index,error in enumerate(errors.values()):
                    from .error_report_ui import render_error
                    render_error(error["message"],project_path=error["path"],task_key=error["task"],key=f"sfx-batch-error-{project_id}-{index}")


@st.fragment(run_every=1)
def watch_batch(project_id,target,baseline):
    finished = collect_batch(project_id)
    if finished or batch_signature(project_id) != baseline:
        from .asset_view_ui import refresh_target
        refresh_target(target)


def controls(manager,project_id,row,key):
    from .storyboard_audio import generate,status,task_key
    from .storyboard_asset_check import document,intact
    kind,identity = row["Category"],row["ID"]
    path = manager.project_path(project_id)
    from .audio_assets import binding
    saved = binding(manager,project_id,kind,identity)
    if intact(path,saved):
        st.audio(str(path/saved["file"]))
        st.caption(saved.get("source",""))
        if kind == "BGM":
            from pathlib import Path
            st.download_button("Download BGM",lambda: (path/saved["file"]).read_bytes(),file_name=identity+Path(saved["file"]).suffix,key=key+"-download")
    from .check_asset_sync import audio_settings,save_audio_settings
    preferences = audio_settings(manager,project_id,kind,identity)
    defaults = {key+"-generate-prompt":preferences.get("prompt",""),
        key+"-duration":float(preferences.get("duration",30. if kind == "BGM" else 3.)),key+"-loop":preferences.get("loop",False)}
    for widget_key,value in defaults.items():
        previous = st.session_state.get(widget_key+"-shared",value)
        if widget_key not in st.session_state or previous != value and st.session_state[widget_key] == previous:
            st.session_state[widget_key] = value
        st.session_state[widget_key+"-shared"] = value
    draft_prompt = st.text_area("Music prompt (optional)" if kind == "BGM" else "Sound effect prompt (optional)",placeholder=row.get("Description",row["Asset"]),key=key+"-generate-prompt",persist_state="session")
    prompt = draft_prompt.strip() or row.get("Description",row["Asset"])
    duration = st.number_input("Duration (seconds)",min_value=3. if kind == "BGM" else .5,max_value=600. if kind == "BGM" else 30.,
        value=None,step=1.,key=key+"-duration",persist_state="session")
    loop = st.checkbox("Loopable sound",key=key+"-loop",value=None) if kind == "SFX" else False
    def save_preferences():
        save_audio_settings(manager,project_id,kind,identity,draft_prompt,duration,loop)
    if st.button("Save generation settings",key=key+"-save-settings",disabled=generation_busy() or duration is None):
        try:
            save_preferences()
            st.success("Generation settings saved to Assets.")
        except (OSError,ValueError,RuntimeError) as exc:
            st.error(str(exc))
    def revert_preferences():
        st.session_state.update(defaults)
    changed = any(st.session_state.get(widget_key) != value for widget_key,value in defaults.items())
    st.button("Revert generation settings",key=key+"-revert-settings",disabled=not changed,on_click=revert_preferences)
    def generate_audio():
        save_preferences()
        return generate(manager,project_id,kind,identity,prompt,duration,loop=loop)
    model = "music_v2" if kind == "BGM" else "eleven_text_to_sound_v2"
    from .ui_tasks import workspace_tasks
    active = next((task for task in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[]))
        if task.get("ui_owned_asset_project") == project_id and task.get("resource") == "storyboard-audio:"+kind+":"+identity and not task["future"].done()),None)
    activity = ("queued" if active.get("phase") in ("queued","waiting") else "saving" if active.get("phase") == "saving" else "running") if active else None
    generation_button(f"Generate with ElevenLabs ({model})",key=key+"-elevenlabs",activity=activity,disabled=generation_busy() or not prompt.strip() or duration is None,
        on_click=lambda:start(manager,project_id,generate_audio,
            "Generating storyboard "+kind+": "+row["Asset"],"elevenlabs","storyboard-audio:"+kind+":"+identity))
    if kind == "BGM":
        st.caption("Instrumental music only. Uses ELEVENLABS_API_KEY; Music API access and commercial rights depend on your ElevenLabs plan.")
        from .pixabay_music import eligible,choose
        owner = manager.load(project_id).world_project_id or project_id
        candidates = eligible(manager,owner)
        generation_button("AI choose from Pixabay Music",key=key+"-pixabay",disabled=generation_busy() or not candidates,
            help="Chooses from your imported Pixabay library after license evidence and non-Content-ID screening. Import tracks in Pixabay Music library below first.",
            on_click=lambda:start(manager,project_id,lambda:choose(manager,project_id,identity),"Choosing Pixabay music: "+row["Asset"],"openai","storyboard-audio:"+kind+":"+identity))
        if not candidates:
            st.caption("No eligible imported Pixabay tracks yet. Add tracks with license evidence in Pixabay Music library below.")
        else:
            with st.expander("Pixabay Music library selection",expanded=False):
                selected = st.selectbox("Pixabay track",list(candidates),format_func=lambda identity:candidates[identity]["title"],key=key+"-pixabay-track")
                track = candidates[selected]
                st.audio(str(manager.project_path(owner)/track["file"]))
                from .pixabay_music import use_track
                generation_button("Use selected Pixabay track",key=key+"-pixabay-use",disabled=generation_busy(),
                    on_click=lambda:start(manager,project_id,lambda:use_track(manager,project_id,identity,selected,expected_track=track),
                        "Using Pixabay music: "+row["Asset"],"local","storyboard-audio:"+kind+":"+identity))
    current = status(manager,project_id,kind,identity)
    if current.get("status") == "failed":
        from .error_report_ui import render_error
        render_error(current.get("error","Audio generation failed"),project_path=path,task_key=task_key(kind,identity),key=key+"-generation-error")


def pixabay_library(manager,project_id):
    from .pixabay_music import register,eligible,catalog,LICENSE_URL
    from .ui_tasks import busy
    owner = manager.load(project_id).world_project_id or project_id
    with st.expander("Pixabay Music library",expanded=False):
        st.caption("Pixabay's public API does not search music. Import tracks downloaded from the official site; AI selects only tracks with saved license evidence and confirmed non-Content-ID status. Free commercial use does not guarantee no future copyright claims.")
        st.link_button("Browse Pixabay Music","https://pixabay.com/music/")
        st.link_button("Pixabay license",LICENSE_URL)
        prefix = "pixabay-library-"+project_id
        title = st.text_input("Track title",key=prefix+"-title")
        author = st.text_input("Artist / contributor",key=prefix+"-author")
        description = st.text_area("Music description / mood / tags",key=prefix+"-description")
        url = st.text_input("Pixabay track URL",key=prefix+"-url")
        audio = st.file_uploader("Downloaded Pixabay audio",type=["wav","mp3","m4a","ogg","flac"],key=prefix+"-audio")
        evidence = st.file_uploader("License / download evidence",type=["pdf","txt","png","jpg","jpeg"],key=prefix+"-evidence")
        commercial = st.checkbox("I verified this track is free under the Pixabay Content License for use in my monetized video",key=prefix+"-commercial")
        content_id = st.selectbox("Content ID status",["Unknown","Not registered (checked on track page)","Registered"],key=prefix+"-content-id")
        if st.button("Add Pixabay track",key=prefix+"-add",disabled=busy() or not title.strip() or not author.strip() or not url.strip() or audio is None or evidence is None or not commercial or content_id != "Not registered (checked on track page)"):
            data,audio_name,proof,proof_name = audio.getvalue(),audio.name,evidence.getvalue(),evidence.name
            start(manager,project_id,lambda:register(manager,owner,title,author,url,data,audio_name,proof,proof_name,
                commercial_confirmed=True,content_id_status="not_registered",description=description),"Adding Pixabay music track","local","pixabay-catalog:"+owner)
        tracks,screened = catalog(manager,owner),eligible(manager,owner)
        if tracks:
            st.dataframe([{"Title":item["title"],"Artist":item["author"],"URL":item["url"],"Eligible":identity in screened}
                for identity,item in tracks.items()],hide_index=True,width="stretch")
