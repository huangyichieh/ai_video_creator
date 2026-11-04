"""Project-local rough USD estimates, including historical unpriced usage.

Fallback rates are an approximate October 2026 rate card, not invoice amounts.
Configured rates and already-recorded nonzero costs take precedence.
"""
from decimal import Decimal


def estimate_usage(record, settings):
    def rate(name, fallback):
        value = getattr(settings, name)
        return value if value is not None else Decimal(fallback)

    def amount(value):
        return Decimal(str(value))

    provider, model = record.provider.lower(), record.model.lower()
    if provider in {"demo", "local", "ffmpeg"}:
        return Decimal(0), "Offline / local"
    if record.cost or record.estimated_cost:
        return record.cost or record.estimated_cost, "Recorded cost"
    if (record.input_tokens or record.output_tokens) and not record.image_count:
        if provider != "openai":
            return None, "No token rate"
        return (record.input_tokens * rate("input_cost_per_million", "2") +
                record.output_tokens * rate("output_cost_per_million", "10")) / 1000000, "Token rates (fallback $2 input / $10 output per million)"
    if record.image_count:
        field = "runway_image_cost_per_image" if provider == "runway" else "image_cost_per_image"
        fallback = ".02" if model == "gen4_image_turbo" else ".08"
        return record.image_count * rate(field, fallback), "Image rate (rough size/quality allowance)"
    if record.video_seconds:
        if provider == "runway":
            field = "act_two_cost_per_second" if model == "act_two" else "runway_video_cost_per_second"
            fallback = {"act_two": ".05", "gen4_turbo": ".05", "gen4.5": ".12"}.get(model, ".17")
        else:
            field, fallback = ("lip_sync_cost_per_second", ".05") if provider == "sync" else ("video_cost_per_second", ".10")
        return amount(record.video_seconds) * rate(field, fallback), "Video rate per generated second"
    if record.audio_characters:
        field = "elevenlabs_tts_cost_per_million_characters" if provider == "elevenlabs" else "tts_cost_per_million_characters"
        fallback = "40" if any(word in model for word in ("turbo", "flash")) else "80"
        return record.audio_characters * rate(field, fallback if provider == "elevenlabs" else "15") / 1000000, "Speech rate per character"
    if record.audio_seconds:
        if provider == "elevenlabs" and model.startswith("music_"):
            return amount(record.audio_seconds)/60*rate("music_cost_per_minute",".30"),"Rough ElevenLabs music allowance per minute"
        if any(abs(record.audio_seconds * sample_rate * width - 4294967294) <= 8
               for sample_rate in (16000, 24000, 44100, 48000) for width in (2, 4)):
            return None, "Invalid legacy streaming-WAV duration; excluded"
        sfx = provider == "elevenlabs" or "sfx" in record.step or "sound" in model
        value = amount(record.audio_seconds)
        return (value * rate("sfx_cost_per_second", ".01"), "Rough SFX allowance per second") if sfx else (value / 60 * rate("alignment_cost_per_minute", ".006"), "Audio alignment rate per minute")
    if record.billing_units and record.billing_unit == "characters" and provider == "elevenlabs":
        return amount(record.billing_units) * rate("elevenlabs_tts_cost_per_million_characters", "80") / 1000000, "Character-credit conversion (rough)"
    return (Decimal(0), "Recorded zero cost") if record.pricing_known else (None, "Usage unavailable")


def project_usage_records(manager, project_id):
    from .costs import CostTracker
    project = manager.load(project_id)
    ids = {project_id}
    if project.workflow_version == "world_episode" and not project.world_project_id:
        ids.update(p.project_id for p in manager.list_projects() if p.world_project_id == project_id)
    return [record for pid in ids for record in CostTracker(manager, pid).records()
            if not record.story_project_id or record.story_project_id in ids]


def project_cost_estimate(manager, project_id):
    from .costs import CostTracker
    entries, seen = [], set()
    jobs = {}
    for record in project_usage_records(manager, project_id):
        if record.record_id in seen:
            continue
        seen.add(record.record_id)
        value, basis = estimate_usage(record, manager.settings)
        entry = {"record": record, "amount": value, "basis": basis}
        if record.provider_job_id:
            key = (record.provider, record.provider_job_id)
            previous = jobs.get(key)
            if previous is not None:
                # Polls/retries of one remote generation must not bill it twice.
                if (value or Decimal(0)) > (previous["amount"] or Decimal(0)):
                    previous["amount"], previous["basis"] = value, basis
                continue
            jobs[key] = entry
        entries.append(entry)
    return sum((e["amount"] for e in entries if e["amount"] is not None), Decimal(0)), entries
