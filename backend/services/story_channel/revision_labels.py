"""Display immutable generation times, never a revision's last-applied time."""
from datetime import datetime,timedelta,timezone
from collections.abc import Mapping

DISPLAY_ZONE=timezone(timedelta(hours=8))


def generated_time(record):
    if hasattr(record,"model_dump"):
        record=record.model_dump(mode="python",by_alias=True)
    if not isinstance(record,Mapping):
        return None
    value=record.get("createdAt") or record.get("created_at")
    if value:
        try:
            stamp=value if isinstance(value,datetime) else datetime.fromisoformat(value.replace("Z","+00:00"))
            return stamp if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
        except (TypeError,ValueError,AttributeError):
            return None
    for key in ("asset","audioAsset"):
        stamp=generated_time(record.get(key))
        if stamp is not None:
            return stamp
    if isinstance(record.get("records"),Mapping):
        stamps=[stamp for item in record["records"].values() if (stamp:=generated_time(item)) is not None]
        return max(stamps) if stamps else None
    return None


def generation_date(record):
    stamp=generated_time(record)
    return stamp.astimezone(DISPLAY_ZONE).strftime("%Y-%m-%d %H:%M:%S (UTC+08:00)") if stamp else "Generation time unavailable"


def revision_label(record,revision=None):
    if revision is None:
        revision=record.get("revision",0) if isinstance(record,Mapping) else record.revision
    return f"Revision {revision} · {generation_date(record)}"
