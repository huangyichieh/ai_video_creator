"""Compile animation prompts locally after episode storyboard approval."""
from .files import input_hash
from .models import ApprovalRecord,StepState

COMPILER_VERSION = "storyboard-action-camera-v2"


def preparation_current(pipeline,manifest):
    from .file_cache import read_json
    try:
        receipt = read_json(pipeline.path/"prompts"/"preparation.json")
        return (receipt.get("compilerVersion") == COMPILER_VERSION
            and receipt.get("storyboardHash") == manifest.steps["storyboard"].output_hash
            and receipt.get("sourceHash") == pipeline._fingerprint("prompts"))
    except (OSError,ValueError,KeyError):
        return False


def prepare_locked(pipeline):
    """Caller owns the project lock; never acquire it recursively or call an API."""
    manifest = pipeline._load()
    if not manifest.world_project_id or "storyboard" not in manifest.approvals:
        return manifest
    required = ("world","characters","story","review","script","storyboard")
    if any(manifest.steps.get(name,StepState()).status != "succeeded" for name in required):
        raise ValueError("Approve a current Storyboard before preparing animation prompts.")
    digest = input_hash({name:manifest.steps[name].output_hash for name in required})
    if manifest.approvals["storyboard"].content_hash != digest:
        raise ValueError("Storyboard approval is no longer current.")
    state = manifest.steps.get("prompts")
    if not state or state.status != "succeeded" or not preparation_current(pipeline,manifest):
        # This internal stage uses the deterministic provider adapter, even if
        # an older project used an LLM to generate prompts manually.
        manifest.provider_config.pop("llm:prompts",None)
        state = manifest.steps.setdefault("prompts",StepState())
        state.status,state.error = "running",None
        state.attempts += 1
        pipeline.manager.save(manifest)
        try:
            from .modules import visual_prompt_adapter,validate_output
            context = pipeline._context("prompts")
            output = visual_prompt_adapter(pipeline.settings.prompt_provider).generate(
                context,pipeline.settings.prompt_provider,pipeline.settings.prompt_model)
            validate_output("prompts",output,context)
            pipeline._commit("prompts",output,pipeline._fingerprint("prompts",context=context))
        except Exception as exc:
            manifest = pipeline._load()
            manifest.steps["prompts"].status = "failed"
            manifest.steps["prompts"].error = str(exc)
            manifest.content_approvals.pop("prompts",None)
            pipeline.manager.save(manifest)
            from .error_reports import record_failure
            record_failure(pipeline.path,"prompts",exc)
            raise
    manifest = pipeline._load()
    from .files import atomic_write
    from .file_cache import read_json
    import json
    path = pipeline.path/"prompts"/"preparation.json"
    receipt = {"compilerVersion":COMPILER_VERSION,"storyboardHash":manifest.steps["storyboard"].output_hash,
        "sourceHash":pipeline._fingerprint("prompts"),"promptHash":manifest.steps["prompts"].output_hash}
    if not path.exists() or read_json(path) != receipt:
        atomic_write(path,json.dumps(receipt,ensure_ascii=False,indent=2))
    manifest.content_approvals["prompts"] = ApprovalRecord(
        content_hash=input_hash(pipeline.artifact("prompts").model_dump(mode="json",by_alias=True)),
        note="Prepared automatically from the approved Storyboard; no separate prompt approval required")
    pipeline._set_status(manifest)
    pipeline.manager.save(manifest)
    return manifest
