"""Edit reusable prop drafts without changing their stable IDs or visual history."""
import json
from pydantic import TypeAdapter

from .models import Identifier
from .files import atomic_write,input_hash


def save(media,identity,title,description,modes,*,expected):
    TypeAdapter(Identifier).validate_python(identity)
    from .studio import WorldStudio
    from .locking import project_lock
    from .ledger_lock import ledger_lock
    from .identity import IdentityManager,Prop
    manifest = media.manager.load(media.project_id)
    if manifest.world_project_id:
        raise ValueError("Reusable prop drafts belong to the parent World's Assets library.")
    if manifest.workflow_version == "world_episode":
        WorldStudio(media.manager,media.project_id).require_approved()
    title,description = title.strip(),description.strip()
    modes = list(dict.fromkeys(mode.strip() for mode in modes if mode.strip()))
    if not title or not description or not modes:
        raise ValueError("Enter a prop title, description and at least one mode.")
    with project_lock(media.path),ledger_lock(media.path):
        state = media.load()
        previous = state["props"].get(identity)
        if input_hash(previous) != expected:
            raise ValueError("This prop changed in another session. Reload its draft before saving.")
        if previous:
            atomic_write(media.path/"history"/"prop_drafts"/identity/(input_hash(previous)+".json"),json.dumps(previous,ensure_ascii=False,indent=2))
        state["props"][identity] = {**(previous or {}),"title":title,"description":description,"modes":modes}
        if identity in state.get("removedProps",[]):
            state["removedProps"].remove(identity)
        if identity in state.get("removedPropModes",{}):
            state["removedPropModes"][identity] = [mode for mode in state["removedPropModes"][identity] if mode not in modes]
        media.save(state)
        manager = IdentityManager(media.manager,media.project_id)
        library = manager.load()
        if library:
            previous_identity = library.props.get(identity)
            library.props[identity] = previous_identity.model_copy(update={"description":description}) if previous_identity else Prop(prop_id=identity,description=description)
            library.approval = None
            manager._write(library)
    return state["props"][identity]
