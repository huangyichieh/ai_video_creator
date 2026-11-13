"""Paired prompts, coherent revisions and atomic approval/application for location images."""
import json
from uuid import uuid4

from .files import atomic_write,input_hash,read_text,atomic_copy
from .locking import project_lock


def names(group):
    from .location_bridges import BRIDGES
    from .dual_panorama import MASTERS
    if group not in ("bridges","masters"):
        raise ValueError("Choose bridges or masters.")
    return BRIDGES if group == "bridges" else MASTERS


def folder(engine,lid,group):
    return engine.local_path(f"history/location_pairs/{lid}/{group}")


def current(engine,state,lid,group):
    from .dual_panorama import digest
    result = {name:state.records.get(f"backgrounds:{lid}__{name}") for name in names(group)}
    if not all(digest(engine,state,lid,name) for name in result):
        return None
    ids = {record.asset.generation_params.get("locationPairId") for record in result.values()}
    return result if len(ids) == 1 else None


def archive_current(engine,state,lid,group):
    records = {name:state.records.get(f"backgrounds:{lid}__{name}") for name in names(group)}
    if not all(engine._intact(record) and record.status in ("succeeded","stale") for record in records.values()):
        return
    ids = {record.asset.generation_params.get("locationPairId") for record in records.values()}
    if len(ids) != 1:
        return
    selection = {name:record.asset.asset_id for name,record in records.items()}
    identity = next(iter(ids)) or "legacy_"+input_hash(selection)[:16]
    path = folder(engine,lid,group)/(identity+".json")
    if not path.exists():
        atomic_write(path,json.dumps({"id":identity,"assets":selection},indent=2))


def assign_batches(engine,state,selected,*,force=False):
    engine._location_pair_batches = {}
    jobs = {task.key:task for task in selected if task.operation == "location_panorama"}
    for lid in state.location_reference_sets:
        for group in ("bridges","masters"):
            keys = [f"backgrounds:{lid}__{name}" for name in names(group)]
            if not all(key in jobs for key in keys):
                continue
            archive_current(engine,state,lid,group)
            valid = [state.records.get(key) for key in keys if engine.record_current(state.records.get(key),jobs[key],state)]
            cohorts = {record.asset.generation_params.get("locationPairId") for record in valid}
            if not force and len(valid) == len(keys) and len(cohorts) == 1:
                continue
            resume = next(iter(cohorts)) if not force and len(cohorts) == 1 else None
            engine._location_pair_batches[lid+":"+group] = resume or uuid4().hex[:16]


def reusable(engine,task,record):
    lid,name = task.subject.rsplit("__",1) if "__" in task.subject else ("","")
    group = "bridges" if name in names("bridges") else "masters"
    wanted = getattr(engine,"_location_pair_batches",{}).get(lid+":"+group)
    return not wanted or bool(record and record.asset and record.asset.generation_params.get("locationPairId") == wanted)


def set_prompt(engine,lid,group,text):
    from .background_library import BackgroundLibrary
    BackgroundLibrary(engine)._root()
    names(group)
    with project_lock(engine.path):
        state = engine.load()
        state.location_pair_prompts.setdefault(lid,{})[group] = text.strip()
        engine._save(state)
        return engine._refresh()[0]


def generate(engine,lid,group,prompt):
    set_prompt(engine,lid,group,prompt)
    return engine.generate("backgrounds",subjects=[lid+"__"+name for name in names(group)],force=True)


def regenerate_one(engine,lid,name):
    """Replace one view, keep other media, then review the resulting complete set."""
    from .background_library import BackgroundLibrary
    from .floor_plan_guides import require_guides
    BackgroundLibrary(engine)._root()
    if name not in names("masters"):
        raise ValueError("Choose a North, East, South or West master.")
    subject = lid+"__"+name
    with project_lock(engine.path):
        before,tasks = engine._refresh()
        require_guides(engine,lid,before.location_reference_sets[lid])
        archive_current(engine,before,lid,"masters")
        snapshots = {}
        for task in tasks:
            if task.subject in {lid+"__"+member for member in names("masters")}:
                record = before.records.get(task.key)
                if engine._intact(record) and (record.applied_input_hash or record.input_hash) == engine._hash(task,before):
                    snapshots[task.key] = (record.asset.asset_id,record.file_hash,
                        input_hash([task.prompt,engine._config(task,before).model_dump(mode="json")]))
        old = before.records.get("backgrounds:"+subject)
        old_id = old.asset.asset_id if old and old.asset else None
    engine._individual_location_master = subject
    try:
        engine.generate("backgrounds",subject=subject,force=True)
    finally:
        engine._individual_location_master = None
        engine._panorama_master_tasks = None
    with project_lock(engine.path):
        state = engine.load()
        replacement = state.records.get("backgrounds:"+subject)
        if not replacement or replacement.status != "succeeded" or not replacement.asset or replacement.asset.asset_id == old_id:
            return engine._refresh()[0]
        for task in engine.tasks(state,references_only=True):
            if task.subject not in {lid+"__"+member for member in names("masters")}:
                continue
            record = state.records.get(task.key)
            snapshot = snapshots.get(task.key)
            same_source = snapshot and engine._intact(record) and snapshot[:2] == (record.asset.asset_id,record.file_hash)
            same_inputs = snapshot and snapshot[2] == input_hash([task.prompt,engine._config(task,state).model_dump(mode="json")])
            if task.subject == subject or (same_source and same_inputs):
                # Keep original generation hashes/provenance. Acknowledge only
                # changed neighbor references, without regenerating these images.
                record.applied_input_hash = engine._hash(task,state)
                record.status,record.stale_reason = "succeeded",""
        members = {member:state.records.get(f"backgrounds:{lid}__{member}") for member in names("masters")}
        if all(engine._intact(record) and record.status == "succeeded" for record in members.values()):
            cohort = uuid4().hex[:16]
            for record in members.values():
                record.asset.generation_params["locationPairId"] = cohort
            archive_current(engine,state,lid,"masters")
        for member in members:
            state.location_master_approvals.pop(lid+":"+member,None)
        state.background_approvals.pop(lid,None)
        engine._save(state)
        return engine._refresh()[0]


def approve(engine,lid,group,*,expected):
    from .models import ApprovalRecord
    from .dual_panorama import digest,root
    with project_lock(engine.path):
        state = engine.load()
        records = current(engine,state,lid,group)
        if not records or {name:record.asset.asset_id for name,record in records.items()} != expected:
            raise ValueError("The reference set changed or is incomplete. Review every current image before approving.")
        if group == "masters":
            from .directional_masters import assess
            assess(engine,state,lid)
        for name,record in records.items():
            state.location_master_approvals[lid+":"+name] = ApprovalRecord(content_hash=digest(engine,state,lid,name))
            from .directional_masters import alias as master_alias
            alias = name if group == "bridges" else master_alias(name)
            atomic_copy(engine.local_path(record.asset.file_path),root(engine,lid)/(alias+".png"))
        engine._save(state)
        return engine._refresh()[0]


def versions(engine,lid,group):
    records = {name:{record.asset.asset_id:record for record in engine.revisions("backgrounds",lid+"__"+name)
                     if engine._intact(record) and record.status in ("succeeded","stale")} for name in names(group)}
    cohorts = {}
    for name,items in records.items():
        for record in items.values():
            identity = record.asset.generation_params.get("locationPairId")
            if identity:
                cohorts.setdefault(identity,{})[name] = record
    for path in folder(engine,lid,group).glob("*.json"):
        try:
            receipt = json.loads(read_text(path))
            cohorts.setdefault(receipt["id"],{name:records[name][identity] for name,identity in receipt["assets"].items()})
        except (OSError,ValueError,KeyError):
            continue
    state = engine.load()
    active = {name:state.records.get(f"backgrounds:{lid}__{name}") for name in names(group)}
    if all(engine._intact(record) and record.status in ("succeeded","stale") for record in active.values()) and len({r.asset.generation_params.get("locationPairId") for r in active.values()}) == 1:
        identity = next(iter(active.values())).asset.generation_params.get("locationPairId") or "legacy_"+input_hash({n:r.asset.asset_id for n,r in active.items()})[:16]
        cohorts.setdefault(identity,active)
    return sorted([{"id":identity,"records":items} for identity,items in cohorts.items() if set(items) == set(names(group))],
                  key=lambda item:max(record.updated_at for record in item["records"].values()),reverse=True)


def apply(engine,lid,group,identity):
    from .dual_panorama import invalidate_world,root
    with project_lock(engine.path):
        state,tasks = engine._refresh()
        choice = next((item for item in versions(engine,lid,group) if item["id"] == identity),None)
        if not choice:
            raise ValueError("This saved image pair is unavailable or incomplete.")
        archive_current(engine,state,lid,group)
        for name,selected in choice["records"].items():
            task = next(t for t in tasks if t.key == f"backgrounds:{lid}__{name}")
            record = selected.model_copy(deep=True)
            record.asset.generation_params["locationPairId"] = choice["id"]
            previous = state.records.get(task.key)
            record.attempts = previous.attempts if previous else selected.attempts
            from .models import utc_now
            record.updated_at = utc_now()
            record.remote_job_id,record.remote_job_hash,record.remote_status = None,"",""
            record.applied_input_hash = engine._hash(task,state)
            record.status,record.error,record.stale_reason = "succeeded",None,""
            state.records[task.key] = record
            state.location_master_approvals.pop(lid+":"+name,None)
            from .directional_masters import alias as master_alias
            alias = name if group == "bridges" else master_alias(name)
            atomic_copy(engine.local_path(record.asset.file_path),root(engine,lid)/(alias+".png"))
        invalidate_world(engine,lid)
        if group == "bridges":
            from .dual_panorama import MASTERS
            for master in MASTERS:
                state.location_master_approvals.pop(lid+":"+master,None)
        state.background_approvals.pop(lid,None)
        manifest = engine.manager.load(engine.project_id)
        for gate in ("backgrounds","assets"):
            manifest.asset_approvals.pop(gate,None)
        engine.manager.save(manifest)
        engine._save(state)
        return engine._refresh()[0]


def apply_legacy(engine,lid,group,selection):
    """Let users explicitly pair older independent revisions; never infer historical pairings."""
    if set(selection) != set(names(group)):
        raise ValueError("Choose one saved revision for each image.")
    with project_lock(engine.path):
        for name,identity in selection.items():
            record = next((r for r in engine.revisions("backgrounds",lid+"__"+name) if r.asset.asset_id == identity),None)
            if not engine._intact(record) or record.asset.generation_params.get("locationPairId"):
                raise ValueError("Choose intact legacy revisions; generated pairs are selected together above.")
        identity = "legacy_"+input_hash(selection)[:16]
        atomic_write(folder(engine,lid,group)/(identity+".json"),json.dumps({"id":identity,"assets":selection},indent=2))
    return apply(engine,lid,group,identity)
