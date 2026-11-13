"""Reusable locations live beside immutable World canon and episode snapshots."""
import json
from contextlib import ExitStack
from uuid import uuid4

from .creative import Location
from .files import atomic_write, input_hash, read_text
from .locking import project_lock
from .models import ApprovalRecord


def detail_yaw(value):
    """Saved detail metadata includes numeric angles serialized as strings."""
    import math
    try:
        if isinstance(value,bool):
            raise ValueError
        number = float(value)
    except (TypeError,ValueError,OverflowError) as exc:
        raise ValueError("Detail camera direction must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError("Detail camera direction must be a finite number.")
    return number


def catalog(path):
    file = path / "background_library.json"
    from .file_cache import read_json
    return read_json(file) if file.exists() else {"added": [], "removed": []}


def locations(pipeline):
    data = catalog(pipeline.path)
    result = {loc.location_id: loc for loc in pipeline.artifact("world").locations}
    result.update({loc.location_id: loc for loc in map(Location.model_validate, data["added"])})
    for lid,patch in data.get("overrides",{}).items():
        if lid in result:
            result[lid] = result[lid].model_copy(update={"description":patch["description"]})
    return [loc for lid, loc in result.items() if lid not in data["removed"]]


class BackgroundLibrary:
    def __init__(self, engine):
        self.engine = engine

    def subject_id(self, subject):
        ids = {loc.location_id for loc in locations(self.engine.creative)}
        if subject in ids:
            return subject
        for lid in ids:
            if subject.startswith(lid + "__") and subject[len(lid)+2:] in {
                    "simple_master","room_panorama","master", "master_panorama", "east_bridge","west_bridge","front_panorama", "back_panorama", "east_panorama", "west_panorama", "master_wide", "reverse", "focal", *(f"view_{angle:03d}" for angle in range(0, 360, 45)),
                    *(f"{kind}_{angle:03d}" for kind in ("raw","enhanced") for angle in range(0,360,45))}:
                return lid
            if subject.startswith(lid+"__shot_"):
                return lid
            if subject in {lid + "__" + view for view in ("left", "right", "back", "panorama")}:
                return lid
        raise ValueError("Unknown background")

    def image(self, state, subject):
        from .media_utils import file_hash
        reference = state.background_images[self.subject_id(subject)]
        path = self.engine.local_path(reference["file"])
        if not path.is_file() or file_hash(path) != reference["hash"]:
            raise ValueError("The background reference is missing or modified. Upload it again or remove it.")
        return path

    def set_image(self, subject, data, *, timeout=1):
        import hashlib
        import io
        from PIL import Image, UnidentifiedImageError
        from .media_utils import write_bytes
        self._root()
        subject = self.subject_id(subject)
        reference = None
        if data is not None:
            if not isinstance(data, bytes) or len(data) > 20 * 1024 * 1024:
                raise ValueError("Upload a PNG, JPEG or WebP image up to 20 MB.")
            try:
                with Image.open(io.BytesIO(data)) as image:
                    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(image.format)
                    image.verify()
                if extension is None:
                    raise ValueError("Upload a PNG, JPEG or WebP image.")
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise ValueError("The background reference is not a valid image.") from exc
            digest = hashlib.sha256(data).hexdigest()
            reference = {"file": f"references/backgrounds/{subject}/{digest[:32]}.{extension}", "hash": digest}
        with project_lock(self.engine.path, timeout=timeout):
            state = self.engine.load()
            if reference:
                write_bytes(self.engine.local_path(reference["file"]), data)
                state.background_images[subject] = reference
            else:
                state.background_images.pop(subject, None)
            self.engine._save(state)
            return self.engine._refresh()[0]

    def set_prompt(self, subject, text):
        self._root()
        subject = self.subject_id(subject)
        with project_lock(self.engine.path):
            state = self.engine.load()
            for item in [subject] + [subject + "__" + view for view in ("left", "right", "back", "panorama")]:
                state.style_prompts["backgrounds:" + item] = text.strip()
            self.engine._save(state)
            return self.engine._refresh()[0]

    def _root(self):
        manifest = self.engine.manager.load(self.engine.project_id)
        if manifest.world_project_id or manifest.workflow_version != "world_episode":
            raise ValueError("Edit reusable backgrounds on the World Assets page.")

    def add(self, name, description, *, reference_type=None, floor_plan="", object_layout=None,generation_method="dual_panorama"):
        self._root()
        from .location_references import LocationReferenceSet
        config = LocationReferenceSet(reference_type=reference_type, floor_plan=floor_plan,
            object_layout=object_layout or {},generation_method=generation_method,
            simple_prompt=description.strip() if reference_type == "SIMPLE" else "")
        location = Location(location_id="background_" + uuid4().hex[:12],
                            name=name.strip(), description=description.strip())
        with project_lock(self.engine.path):
            data = catalog(self.engine.path)
            data["added"].append(location.model_dump(mode="json", by_alias=True))
            atomic_write(self.engine.path / "background_library.json", json.dumps(data, ensure_ascii=False, indent=2))
            if config is not None:
                state = self.engine.load()
                state.location_reference_sets[location.location_id] = config.model_dump(mode="json")
                self.engine._save(state)
        self._rebuild()
        return location

    def edit_description(self,subject,description,*,expected):
        """Edit asset metadata without rewriting approved World canon or snapshots."""
        self._root()
        description = description.strip()
        if not description:
            raise ValueError("Background description cannot be empty.")
        with project_lock(self.engine.path):
            current = next((loc for loc in locations(self.engine.creative) if loc.location_id == subject),None)
            if current is None:
                raise ValueError("Background no longer exists.")
            if current.description != expected:
                raise ValueError("Background description changed in another session. Revert changes to load the latest version.")
            if description == current.description:
                return self.engine.load()
            from .identity import IdentityManager
            identity = IdentityManager(self.engine.manager,self.engine.project_id)
            library = identity.load()
            synchronized = bool(library and library.source_hash == identity.source())
            data = catalog(self.engine.path)
            data.setdefault("overrides",{})[subject] = {"description":description}
            atomic_write(self.engine.path/"background_library.json",json.dumps(data,ensure_ascii=False,indent=2))
            if synchronized and subject in library.locations:
                original = library.model_dump_json(by_alias=True,indent=2)
                library.locations[subject].description = description
                library.source_hash = identity.source()
                library.approval = None
                identity.validate(library)
                atomic_write(self.engine.path/"history"/"identity"/(uuid4().hex+".json"),original)
                identity._write(library)
            return self.engine._refresh()[0]

    def configure_references(self, subject, reference_type=None, floor_plan="", object_layout=None,*,camera_plan=None,camera_plan_signature=None,generation_method=None,bridge_warp=None,expected_layout=None):
        from .location_references import LocationReferenceSet
        self._root()
        subject = self.subject_id(subject)
        with project_lock(self.engine.path):
            state = self.engine.load()
            previous = state.location_reference_sets.get(subject, {})
            from .floor_plan_revisions import capture,signature as layout_signature
            if expected_layout is not None and layout_signature(previous) != expected_layout:
                raise ValueError("Floor-plan settings changed in another session. Reload before applying a revision.")
            capture(self.engine,subject,previous)
            from .bridge_warp import options as warp_options
            warp = warp_options({"bridge_warp":bridge_warp if bridge_warp is not None else previous.get("bridge_warp",{})})
            from .floor_plan_guides import source_signature,CameraPlan
            signature = source_signature(floor_plan,object_layout or {})
            if camera_plan is not None:
                camera_plan = CameraPlan.model_validate(camera_plan).model_dump(mode="json",by_alias=True)
            elif previous.get("camera_plan_signature") == signature:
                camera_plan = previous.get("camera_plan")
            config = LocationReferenceSet(reference_type=reference_type or previous.get("reference_type"), floor_plan=floor_plan,
                object_layout=object_layout or {}, detail_references=previous.get("detail_references", {}),
                removed_detail_ids=previous.get("removed_detail_ids", []),
                simple_prompt=previous.get("simple_prompt",""),
                camera_plan=camera_plan if camera_plan_signature in (None,signature) else None,
                camera_plan_signature=signature if camera_plan and camera_plan_signature in (None,signature) else "",
                generation_method=generation_method or previous.get("generation_method","dual_panorama"),bridge_warp=warp)
            state.location_reference_sets[subject] = config.model_dump(mode="json")
            state.background_approvals.pop(subject, None)
            self.engine._save(state)
            capture(self.engine,subject,state.location_reference_sets[subject])
            manifest = self.engine.manager.load(self.engine.project_id)
            for gate in ("backgrounds", "assets"):
                manifest.asset_approvals.pop(gate, None)
            self.engine.manager.save(manifest)
            return self.engine._refresh()[0]

    def add_detail_reference(self, subject, area, data, *, source_yaw=None, source_assets=None,
                             detail_id=None,object_ids=None,description="",position=None,generation=None,expected_reference=None):
        import io
        import hashlib
        from PIL import Image
        from .media_utils import write_bytes, file_hash
        from .models import GeneratedAsset
        from .asset_models import AssetRecord
        self._root()
        subject, area = self.subject_id(subject), area.strip()
        if source_yaw is not None:
            source_yaw = detail_yaw(source_yaw)
        from .location_details import identifier,validate_links,layout_hash
        identity = identifier(subject,area,detail_id)
        if not area or len(data) > 20 * 1024 * 1024:
            raise ValueError("Enter the detail's target area and upload an image up to 20 MB.")
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError("Use PNG, JPEG or WebP.")
            image.verify()
        with project_lock(self.engine.path):
            state = self.engine.load()
            if subject not in state.location_reference_sets:
                raise ValueError("Choose a location reference type first.")
            spec = state.location_reference_sets[subject]
            if expected_reference is not None and input_hash(spec.get("detail_references",{}).get(identity)) != expected_reference:
                raise ValueError("This detail changed during generation. Reopen Zoom on its latest image.")
            links = validate_links(self.engine,subject,spec,object_ids or [])
            if position is not None:
                import math
                if not isinstance(position,dict) or not all(isinstance(position.get(axis),(int,float)) and math.isfinite(position[axis]) for axis in ("x","y")):
                    raise ValueError("Detail position requires finite x and y coordinates.")
            from .dual_panorama import is_key,pair_ready,MASTERS
            if is_key(spec):
                if not pair_ready(self.engine,state,subject):
                    raise ValueError("Approve all four directional masters before adding a detail reference.")
                current_sources = {f"backgrounds:{subject}__{name}":[state.records[f"backgrounds:{subject}__{name}"].asset.asset_id,
                    state.records[f"backgrounds:{subject}__{name}"].file_hash] for name in MASTERS}
                if source_assets is None:
                    source_assets = current_sources
            if source_assets and any(not (record := state.records.get(key)) or not record.asset
                    or [record.asset.asset_id,record.file_hash] != value or record.status != "succeeded"
                    for key,value in source_assets.items()):
                raise ValueError("Source assets changed before the optional reference could be registered.")
            digest = input_hash({"area": area, "image": hashlib.sha256(data).hexdigest()})
            relative = f"references/details/{subject}/{digest[:24]}.png"
            # Normalize the uploaded bytes to match the saved image extension.
            buffer = io.BytesIO()
            with Image.open(io.BytesIO(data)) as image:
                image.convert("RGB").save(buffer, "PNG")
            path = self.engine.local_path(relative)
            write_bytes(path, buffer.getvalue())
            detail_subject = subject + "__detail_" + identity
            key = "backgrounds:" + detail_subject
            asset = GeneratedAsset(type="image", provider="manual", model="location-detail", file_path=relative, prompt_hash=digest)
            if generation:
                asset.provider,asset.model = generation["provider"],generation["model"]
                asset.cost = generation.get("cost",0)
                asset.generation_params = generation
            previous = state.records.get(key)
            if previous and previous.asset:
                atomic_write(self.engine.path/"history"/"assets"/"backgrounds"/detail_subject/(previous.asset.asset_id+".json"),previous.model_dump_json(by_alias=True,indent=2))
            state.records[key] = AssetRecord(stage="backgrounds", subject=detail_subject, status="succeeded",
                asset=asset, file_hash=file_hash(path), input_hash=digest, revision=previous.revision+1 if previous else 1)
            spec.setdefault("detail_references", {})[identity] = {
                "id":identity,"area":area,"description":description.strip(),"objectIds":links,
                "position":position,"role":"local_detail_only","layoutHash":layout_hash(spec),
                "key": key, "file": relative, "hash": file_hash(path),
                "sourceAssets":json.dumps(source_assets or {},sort_keys=True)}
            if identity in spec.get("removed_detail_ids",[]):
                spec["removed_detail_ids"].remove(identity)
            if source_yaw is not None:
                state.location_reference_sets[subject]["detail_references"][identity].update(
                    yaw=str(source_yaw), sourceAssets=json.dumps(source_assets or {},sort_keys=True))
            # Local details supplement the approved masters, without reopening
            # the location's master approval.
            self.engine._save(state)
            return self.engine._refresh()[0]

    def _rebuild(self):
        if not (self.engine.path / "characters.json").exists():
            return self.engine.load()
        from .identity import IdentityManager
        IdentityManager(self.engine.manager, self.engine.project_id).prepare()
        return self.engine.inspect()

    def digest(self, subject, state, tasks):
        from .location_references import master_approved
        if state.location_reference_sets.get(subject, {}).get("reference_type") in ("KEY", "KEY_360") and not master_approved(self.engine, state, subject):
            return None
        selected = [task for task in tasks if task.stage == "backgrounds"
                    and self.subject_id(task.subject) == subject]
        if not selected or not all(self.engine.record_current(state.records.get(t.key), t, state) for t in selected):
            return None
        content = {t.key: state.records[t.key].model_dump(mode="json", include={"revision", "asset", "file_hash", "input_hash", "applied_input_hash"}) for t in selected}
        if subject in state.location_reference_sets:
            content["referenceSet"] = {**state.location_reference_sets[subject],"detail_references":{}}
            content["referenceSet"].pop("removed_detail_ids",None)
        return input_hash(content)

    def approved(self, subject, state, tasks=None):
        from .location_references import master_approved
        if state.location_reference_sets.get(subject, {}).get("reference_type") in ("KEY", "KEY_360") and not master_approved(self.engine, state, subject):
            return False
        approval = state.background_approvals.get(subject)
        return bool(approval and self.panorama_reviewed(subject, state)
                    and approval.content_hash == self.digest(subject, state, tasks if tasks is not None else self.engine.tasks(state)))

    def panorama_reviewed(self, subject, state):
        if subject in state.location_reference_sets:
            return True
        from .background_panorama import panorama_mode
        if self.engine.demo or not panorama_mode(state, subject):
            return True
        record = state.records.get("backgrounds:" + subject + "__panorama")
        review = state.background_panorama_reviews.get(subject, {})
        return bool(record and record.asset and review.get("assetId") == record.asset.asset_id
                    and review.get("fileHash") == record.file_hash and review.get("coverage360") and review.get("seamContinuous"))

    def _review(self, subject, state, choice):
        from .background_panorama import assess_panorama
        from .models import utc_now
        record = state.records.get("backgrounds:" + subject + "__panorama")
        if not record or not record.asset or not choice or choice.get("assetId") != record.asset.asset_id:
            raise ValueError("Review the current panorama's full 360° coverage and wrap seam before approving.")
        assessment = assess_panorama(self.engine.local_path(record.asset.file_path))
        if not assessment["isEquirectangularLayout"]:
            raise ValueError("This is not a 2:1 panorama. An ordinary wide image cannot be approved as 360°. Use a native 360° source.")
        if not choice.get("coverage360") or not choice.get("seamContinuous"):
            raise ValueError("Confirm that the panorama includes the rear surroundings and that its start/end seam connects.")
        state.background_panorama_reviews[subject] = {"assetId": record.asset.asset_id, "fileHash": record.file_hash,
            "coverage360": True, "seamContinuous": True, "reviewedAt": utc_now().isoformat(), "method": "human-review"}

    def sync(self, state, tasks, manifest):
        subjects = {self.subject_id(t.subject) for t in tasks if t.stage == "backgrounds"}
        from .dual_panorama import is_key,pair_ready
        from .shared_room import enabled
        for subject in subjects:
            spec = state.location_reference_sets.get(subject)
            if is_key(spec) and not enabled(spec) and pair_ready(self.engine,state,subject):
                # Human approval of both current masters is authoritative. This
                # also restores old approvals invalidated by optional details.
                value = self.digest(subject,state,tasks)
                if value:
                    previous = state.background_approvals.get(subject)
                    if not previous or previous.content_hash != value:
                        state.background_approvals[subject] = ApprovalRecord(content_hash=value,note="Current four directional masters approved")
        old = manifest.asset_approvals.get("backgrounds")
        if old and not state.background_approvals and self.engine._stage_done("backgrounds", state, tasks) and old.content_hash == self.engine._gate_hash("backgrounds", state, tasks):
            state.background_approvals = {s: old.model_copy(update={"content_hash": self.digest(s, state, tasks)}) for s in subjects}
        state.background_approvals = {s: a for s, a in state.background_approvals.items() if s in subjects and self.approved(s, state, tasks)}
        if "characters" in manifest.asset_approvals and (subjects or old) and all(self.approved(s, state, tasks) for s in subjects):
            digest = self.engine._gate_hash("backgrounds", state, tasks)
            if not old or old.content_hash != digest:
                manifest.asset_approvals["backgrounds"] = ApprovalRecord(content_hash=digest, note="All current backgrounds approved")
        else:
            manifest.asset_approvals.pop("backgrounds", None)

    def approve(self, subject=None, *, review_choices=None):
        self.engine.creative.inspect()
        with project_lock(self.engine.path):
            state, tasks = self.engine._refresh()
            manifest = self.engine.manager.load(self.engine.project_id)
            self.engine._require_stage("backgrounds", manifest)
            subjects = {self.subject_id(t.subject) for t in tasks if t.stage == "backgrounds"}
            if subject is not None and subject not in subjects:
                raise ValueError("Background no longer exists.")
            pending = [s for s in subjects if (subject is None or subject == s) and not self.approved(s, state, tasks)]
            digests = {s: self.digest(s, state, tasks) for s in pending}
            if any(value is None for value in digests.values()):
                raise ValueError("Generate or apply a current panorama and all required background outputs before approving.")
            from .shared_room import enabled
            for lid in pending:
                if enabled(state.location_reference_sets.get(lid)):
                    choice = (review_choices or {}).get(lid,{})
                    if not choice.get("canonicalGeometryConfirmed") or choice.get("signature") != digests[lid]:
                        raise ValueError("Compare all eight enhanced views with the raw 3D renders and confirm fixed objects are unchanged before approving the reference pack.")
            for lid in pending:
                if enabled(state.location_reference_sets.get(lid)):
                    from .room_canonical import publish_pack
                    publish_pack(self.engine,state,lid,digests[lid])
            if not self.engine.demo:
                from .background_panorama import panorama_mode
                for lid in pending:
                    if panorama_mode(state, lid) and not self.panorama_reviewed(lid, state):
                        self._review(lid, state, (review_choices or {}).get(lid))
            for s, digest in digests.items():
                state.background_approvals[s] = ApprovalRecord(content_hash=digest)
            self.sync(state, tasks, manifest)
            self.engine.manager.save(manifest)
            state.next_stage = self.engine._next(state, tasks, manifest)
            self.engine._save(state)
            return state

    def remove(self, subject):
        self._root()
        from .character_removal import CharacterRemoval
        with ExitStack() as locks:
            locks.enter_context(project_lock(self.engine.path))
            projects = [p for p in self.engine.manager.list_projects() if p.project_id == self.engine.project_id or p.world_project_id == self.engine.project_id]
            for project in sorted(projects, key=lambda p: p.project_id):
                if project.project_id != self.engine.project_id:
                    locks.enter_context(project_lock(self.engine.manager.project_path(project.project_id)))
            location = next((loc for loc in locations(self.engine.creative) if loc.location_id == subject), None)
            if location is None:
                raise ValueError("Background no longer exists.")
            state, tasks = (self.engine._refresh() if (self.engine.path / "characters.json").exists()
                            else (self.engine.load(), []))
            if self.approved(subject, state, tasks):
                # Use the same approved-and-used protection as Characters.
                from types import SimpleNamespace
                token = SimpleNamespace(character_id=subject, name=location.name)
                for project in projects:
                    path = self.engine.manager.project_path(project.project_id)
                    for filename in ("story.json", "script.json", "scenes.json", "storyboard.json", "animation.json", "outline.json"):
                        file = path / filename
                        if not file.exists() or filename == "outline.json" and not project.world_project_id:
                            continue
                        value = json.loads(read_text(file)) if file.suffix == ".json" else read_text(file)
                        if filename == "animation.json":
                            plan = value.get("plan", {})
                            value = {"shots": plan.get("shots", []), "generatedShots": value.get("shots", {})}
                        if CharacterRemoval.mentioned(value, token):
                            raise ValueError("This approved background is used in a story or animation and cannot be removed.")
            data = catalog(self.engine.path)
            atomic_write(self.engine.path / "history" / "removed_backgrounds" / (uuid4().hex + ".json"),
                         json.dumps({"location": location.model_dump(mode="json", by_alias=True), "catalog": data,
                                     "production": state.model_dump(mode="json", by_alias=True)}, ensure_ascii=False, indent=2))
            data["removed"].append(subject)
            atomic_write(self.engine.path / "background_library.json", json.dumps(data, ensure_ascii=False, indent=2))
            state.background_approvals.pop(subject, None)
            state.background_images.pop(subject, None)
            for key in list(state.records):
                if key == "backgrounds:" + subject or key in {"backgrounds:" + subject + "__" + v for v in ("left", "right", "back", "panorama")}:
                    state.records.pop(key)
            state.background_modes.pop(subject, None)
            state.background_panorama_reviews.pop(subject, None)
            self.engine._save(state)
        self._rebuild()

    def prepare_generation(self, state, *, subject=None, subjects=None, force=False):
        """Any new generation upgrades selected legacy views to one panorama."""
        chosen = [subject] if subject else list(subjects or [])
        requested = {self.subject_id(item) for item in chosen}
        if not chosen:
            requested = {loc.location_id for loc in locations(self.engine.creative)}
        for lid in requested:
            if lid in state.location_reference_sets:
                continue
            state.background_modes[lid] = "panorama"
        return bool(requested)

    def apply_panorama_revision(self, subject, asset_id):
        self._root()
        lid = self.subject_id(subject)
        return self.engine.apply_revision("backgrounds", lid + "__panorama", asset_id)

    def import_panorama(self, subject, data):
        """Register a native panorama without resampling or making API requests."""
        from io import BytesIO
        from PIL import Image, UnidentifiedImageError
        from .models import GeneratedAsset, utc_now
        from .media_utils import write_bytes, file_hash
        from .background_panorama import assess_panorama
        self._root()
        lid = self.subject_id(subject)
        if not isinstance(data, bytes) or len(data) > 50 * 1024 * 1024:
            raise ValueError("Upload a panorama image up to 50 MB.")
        try:
            with Image.open(BytesIO(data)) as image:
                if image.width != image.height * 2:
                    raise ValueError("Upload a native 2:1 equirectangular panorama; ordinary wide images are not stretched.")
                image = image.convert("RGB")
                output = BytesIO()
                image.save(output, "PNG")
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError("Upload a valid panorama image.") from exc
        with project_lock(self.engine.path):
            state = self.engine.load()
            state.background_modes[lid] = "panorama"
            self.engine._save(state)
            state, tasks = self.engine._refresh()
            task = next(t for t in tasks if t.key == "backgrounds:" + lid + "__panorama")
            from .asset_models import AssetRecord
            record = state.records.setdefault(task.key, AssetRecord(stage="backgrounds", subject=task.subject))
            asset = GeneratedAsset(type="image", provider="local", model="uploaded-panorama",
                file_path=f"assets/backgrounds/{task.subject}/{uuid4().hex}.png", prompt_hash=input_hash(task.prompt),
                generation_params={"source": "uploaded-panorama", "coverage360Verified": False,
                                   "geometryReviewRequired": True, "seamReviewRequired": True})
            path = self.engine.local_path(asset.file_path)
            write_bytes(path, output.getvalue())
            asset.generation_params["panoramaAssessment"] = assess_panorama(path)
            record.revision = max([record.revision, *(r.revision for r in self.engine.revisions(task.stage, task.subject))]) + 1
            record.asset, record.file_hash = asset, file_hash(path)
            record.input_hash = self.engine._hash(task, state)
            record.applied_input_hash = ""
            record.status, record.error, record.stale_reason = "succeeded", None, ""
            record.updated_at = utc_now()
            record.remote_job_id, record.remote_job_hash, record.remote_status = None, "", ""
            state.background_approvals.pop(lid, None)
            state.background_panorama_reviews.pop(lid, None)
            atomic_write(self.engine.path / "history/assets/backgrounds" / task.subject / (asset.asset_id + ".json"),
                         record.model_dump_json(by_alias=True, indent=2))
            self.engine._save(state)
            return self.engine._refresh()[0]
