"""A fresh, project-local waiting list for each Animations generation run."""
import json
from uuid import uuid4

from .files import atomic_write


class AnimationQueue:
    def __init__(self, engine, run_id=None):
        self.engine = engine
        self.path = engine.path / "animation_waiting_queue.json"
        self.run_id = run_id

    def load(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None

    def save(self, data):
        current = self.load()
        if current and current["run_id"] != self.run_id:
            return  # An older worker must not replace a newer run's queue.
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    def start(self, action, *, pipeline):
        self.run_id = uuid4().hex
        data = {"run_id": self.run_id, "mode": "Pipeline" if pipeline else "Next stage", "status": "queued", "active_stage": "", "items": []}
        # Replace the old list before computing/populating this run's work.
        atomic_write(self.path, json.dumps(data))
        production = self.engine.assets.load()
        render = self.engine.load()
        finishing = self.engine.finishing.load()

        def add(stage, subject, kind, key, record=None):
            data["items"].append({"stage": stage, "subject": subject, "kind": kind, "key": key,
                "revision": record.revision if record else 0, "file_hash": getattr(record, "file_hash", "") if record else "",
                "attempts": record.attempts if record else 0,
                "status": "Queued", "error": ""})

        stages = [action.stage]
        if pipeline and action.stage in ("voices", "subtitles"):
            ordered = ["voices", "subtitles", "consistency"]
            stages = ordered[ordered.index(action.stage):]
        elif pipeline and action.stage == "effects":
            stages.append("videos")
        tasks = self.engine.assets.tasks(production.model_copy(deep=True)) if any(stage in ("scene_images", "voices", "subtitles") for stage in stages) else []
        for stage in stages:
            if stage in ("scene_images", "voices", "subtitles"):
                for task in tasks:
                    if task.stage != stage:
                        continue
                    record = production.records.get(task.key)
                    if stage == action.stage and record and record.status == "succeeded" and self.engine.assets._intact(record):
                        continue
                    add(stage, task.subject, "asset", task.key, record)
            elif stage == "effects":
                board = self.engine.assets.creative.artifact("storyboard").scenes
                for key, (_, label, task_data) in self.engine.finishing.tasks(production, board, render).items():
                    record = finishing.records.get(key)
                    if not key.startswith("lipsync:") and not self.engine.finishing.task_intact(record, task_data):
                        add(stage, label, "effect", key, record)
            elif stage == "videos":
                for sid, record in render.scenes.items():
                    if action.stage == "effects" or record.status != "succeeded" or not self.engine._scene_intact(record):
                        add(stage, sid, "video", sid, record)
            elif action.runnable:
                add(stage, action.title if stage == action.stage else "Scene-input QA", "stage", stage)
        self.save(data)
        return self.run_id

    def rows(self, data=None):
        data = data or self.load()
        if not data:
            return []
        production = self.engine.assets.load()
        render = self.engine.load()
        effects = self.engine.finishing.load()
        titles = {"direction": "Direction setup", "scene_images": "Scene images", "voices": "Voice tracks",
                  "subtitles": "Subtitle alignment", "consistency": "Scene-input QA", "effects": "SFX / VFX", "videos": "Scene videos / shots"}
        rows = []
        for index, item in enumerate(data["items"]):
            record = (production.records.get(item["key"]) if item["kind"] == "asset" else
                      render.scenes.get(item["key"]) if item["kind"] == "video" else
                      effects.records.get(item["key"]) if item["kind"] == "effect" else None)
            status, error = item["status"], item["error"]
            if status not in ("Completed", "Reused", "Stopped", "Not run", "Failed"):
                if record and (record.revision > item["revision"] or record.file_hash != item["file_hash"]) and record.status == "succeeded":
                    status = "Completed"
                elif data["active_stage"] == item["stage"] and data["status"] == "running":
                    if item["kind"] == "stage" or record and record.status == "running":
                        status = "Generating…"
                    elif record and record.status == "failed" and record.attempts > item["attempts"]:
                        status, error = "Failed", record.error or ""
            progress = getattr(record, "remote_progress", getattr(record, "progress", 0)) if record else 0
            rows.append({"Order": index + 1, "Stage": titles.get(item["stage"], item["stage"]), "Subject": item["subject"],
                         "Status": status, "Progress": f"{progress}%" if progress else "", "Error": error})
        return rows

    def stage_started(self, stage):
        data = self.load()
        if not data or data["run_id"] != self.run_id:
            return
        data.update(status="running", active_stage=stage)
        self.save(data)

    def stage_finished(self):
        data = self.load()
        if not data or data["run_id"] != self.run_id:
            return
        for item, row in zip(data["items"], self.rows(data)):
            if item["stage"] == data["active_stage"]:
                item["status"] = row["Status"] if row["Status"] in ("Completed", "Failed") else "Reused" if item["kind"] != "stage" else "Completed"
                item["error"] = row["Error"]
        self.save(data)

    def finish(self, status, error=""):
        data = self.load()
        if not data or data["run_id"] != self.run_id:
            return
        rows = self.rows(data)
        failed = any(row["Status"] == "Failed" for row in rows)
        for item, row in zip(data["items"], rows):
            item["status"], item["error"] = row["Status"], row["Error"]
            if item["status"] in ("Queued", "Generating…"):
                if status == "failed" and not failed and item["stage"] == data["active_stage"]:
                    item["status"], item["error"], failed = "Failed", error, True
                else:
                    item["status"] = "Stopped" if status == "stopped" else "Not run"
        data["status"], data["error"] = status, error
        self.save(data)
