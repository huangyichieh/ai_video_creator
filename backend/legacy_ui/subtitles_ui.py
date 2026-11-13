"""Subtitle alignment and scene-export progress, including not-yet-started tracks."""
import json
import streamlit as st

from .media_utils import file_hash, text_chunks, timestamp
from .ui_tasks import busy


def subtitle_progress(engine, state, *, working=False):
    board = engine.creative.artifact("storyboard")
    try:
        tasks = {t.subject: t for t in engine.tasks(state) if t.stage == "subtitles"}
    except (OSError, ValueError):
        tasks = {}
    tracks, scenes = [], []
    for scene in board.scenes:
        lines = ([("narrator", scene.narration)] if scene.narration.strip() else [])
        lines += [(line.character_id, line.text) for line in scene.dialogue]
        scene_tracks = []
        for line_index, (speaker, text) in enumerate(lines):
            for chunk_index, chunk in enumerate(text_chunks(text)):
                subject = f"{scene.scene_id}_{line_index:04d}_{chunk_index:04d}"
                if task:=tasks.get(subject):
                    speaker,chunk=task.speaker,task.prompt
                record = state.records.get(f"subtitles:{subject}")
                voice = state.records.get(f"voices:{subject}")
                if record and record.status == "running":
                    status = "Generating…" if working else "Interrupted"
                elif record and record.status == "failed":
                    status = "Failed"
                elif record and record.status == "succeeded":
                    task = tasks.get(subject)
                    current = engine._intact(record) and (not task or record.input_hash == engine._hash(task, state))
                    status = "Completed" if current else "Outdated"
                elif record and record.status == "stale":
                    status = "Outdated"
                elif voice and voice.status == "succeeded" and engine._intact(voice):
                    status = "Queued" if state.next_stage == "subtitles" and (working or state.stopped) else "Ready"
                else:
                    status = "Waiting for voice"
                row = {"Scene": scene.scene_id, "Track": subject, "Speaker": speaker, "Status": status,
                       "Revision": record.revision if record else 0, "Attempts": record.attempts if record else 0,
                       "Error": record.error or "" if record else ""}
                tracks.append(row)
                scene_tracks.append(row)
        export = state.scenes.get(scene.scene_id)
        available = False
        if export:
            try:
                paths = (export.audio, export.srt, export.vtt, export.timing)
                image = state.records.get(f"scene_images:{scene.scene_id}")
                available = bool(image and image.asset and image.asset.file_path == export.image
                                 and all(path in export.hashes and file_hash(engine.local_path(path)) == export.hashes[path]
                                         for path in paths))
            except (OSError, ValueError):
                available = False
        completed = sum(row["Status"] == "Completed" for row in scene_tracks)
        statuses = {row["Status"] for row in scene_tracks}
        if "Generating…" in statuses:
            status = "Generating…"
        elif "Failed" in statuses:
            status = "Failed"
        elif "Outdated" in statuses or "Interrupted" in statuses:
            status = "Needs regeneration"
        elif completed == len(scene_tracks):
            status = "Completed" if available else "Waiting for export"
        else:
            status = "Waiting for voice" if "Waiting for voice" in statuses else "Queued / ready"
        scenes.append({"Scene": scene.scene_id, "Completed tracks": completed, "Total tracks": len(scene_tracks),
                       "Status": status, "Subtitle files": "Available" if available and status == "Completed" else "Outdated" if export else "Pending"})
    return tracks, scenes


def show_subtitle_progress(engine, state):
    st.subheader("Subtitle generation progress")
    try:
        tracks, scenes = subtitle_progress(engine, state, working=busy())
    except (OSError, ValueError):
        st.info("Generate the Storyboard to see planned subtitle tracks and progress.")
        return
    total = len(tracks)
    completed = sum(row["Status"] == "Completed" for row in tracks)
    running = sum(row["Status"] == "Generating…" for row in tracks)
    queued = sum(row["Status"] in ("Queued", "Ready") for row in tracks)
    failed = sum(row["Status"] == "Failed" for row in tracks)
    for column, label, count in zip(st.columns(5), ("Subtitle tracks", "Completed", "Generating", "Queued / ready", "Failed"),
                                    (total, completed, running, queued, failed)):
        column.metric(label, count)
    progress = completed / total if total else float(bool(scenes) and all(row["Status"] == "Completed" for row in scenes))
    ready_exports = sum(row["Status"] == "Completed" for row in scenes)
    st.progress(progress, text=f"{completed} / {total} subtitle tracks completed" if total else f"{ready_exports} / {len(scenes)} scene subtitle exports ready")
    st.caption(f"Scene subtitle exports ready: {ready_exports} / {len(scenes)}")
    waiting = sum(row["Status"] == "Waiting for voice" for row in tracks)
    outdated = sum(row["Status"] in ("Outdated", "Interrupted") for row in tracks)
    st.caption(f"Waiting for voice: {waiting} · Needs regeneration: {outdated}")
    st.caption("Up to 5 tracks align simultaneously. Tracks wait for their voice recordings; scene SRT, VTT, and timing files are exported when alignment finishes. Progress updates while generation runs.")
    if running:
        st.status(f"Generating subtitles… {running}/5 tracks running", state="running")
    st.dataframe(scenes, hide_index=True, use_container_width=True)
    if tracks:
        st.dataframe(tracks, hide_index=True, use_container_width=True)
    elif scenes:
        st.info("These scenes have no spoken lines. Their empty subtitle files will be created during the Subtitles stage.")


def show_subtitle_scenes(engine, state):
    try:
        scenes = sorted(engine.creative.artifact("storyboard").scenes, key=lambda scene: scene.order)
    except (OSError, ValueError):
        return
    st.caption("Scene headers show the story timeline. Subtitle timestamps inside each scene start from zero. Pending scenes use planned durations.")
    cursor = 0.0
    planned_timeline = False
    for source in scenes:
        scene = state.scenes.get(source.scene_id)
        duration = scene.duration if scene else source.duration_sec
        planned_timeline = planned_timeline or scene is None
        interval = f"{timestamp(cursor, vtt=True)} – {timestamp(cursor + duration, vtt=True)}"
        label = f"{source.scene_id} · {interval}" + (" (planned)" if planned_timeline else "")
        cursor += duration
        with st.expander(label, expanded=False):
            if scene is None:
                st.info("Subtitle files have not been generated for this scene yet.")
                continue
            st.caption(f"Scene duration: {duration:g} seconds")
            for relative in (scene.audio, scene.srt, scene.vtt, scene.timing):
                path = engine.local_path(relative)
                if not path.exists():
                    st.warning(f"Missing {relative}")
                    continue
                if path.suffix == ".wav":
                    st.audio(str(path))
                elif path.suffix == ".json":
                    st.json(json.loads(path.read_text(encoding="utf-8")))
                elif path.suffix != ".vtt":
                    st.code(path.read_text(encoding="utf-8"), language=None)
                st.download_button("Download " + path.name, path.read_bytes(), file_name=path.name, key=f"download-scene-{relative}")
