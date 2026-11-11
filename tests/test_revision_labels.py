from datetime import datetime,timezone

from story_channel.revision_labels import revision_label,generation_date
from story_channel.models import GeneratedAsset
from story_channel.asset_models import AssetRecord


def test_revision_uses_generation_date_in_taipei_not_last_applied():
    record={"revision":3,"updatedAt":"2026-11-09T12:00:00Z","appliedAt":"2026-11-10T12:00:00Z",
        "asset":{"createdAt":"2026-11-07T16:30:05Z"}}
    assert revision_label(record)=="Revision 3 · 2026-11-08 00:30:05 (UTC+08:00)"
    record["updatedAt"]="2027-01-01T00:00:00Z"
    assert "2026-11-08 00:30:05" in revision_label(record)


def test_model_records_and_reference_sets():
    asset=GeneratedAsset(type="image",provider="demo",model="test",file_path="image.png",prompt_hash="a"*64,
        created_at=datetime(2026,11,7,1,2,3,tzinfo=timezone.utc))
    record=AssetRecord(stage="backgrounds",subject="office",revision=1,asset=asset)
    assert revision_label(record)=="Revision 1 · 2026-11-07 09:02:03 (UTC+08:00)"
    assert generation_date({"records":{"north":record}})==generation_date(record)


def test_voice_revision_timestamp_and_legacy_fallback():
    audio={"createdAt":"2026-11-01T00:00:00+00:00"}
    assert generation_date({"audioAsset":audio})=="2026-11-01 08:00:00 (UTC+08:00)"
    assert generation_date({"createdAt":"2026-11-07T00:00:00+00:00","audioAsset":audio})=="2026-11-07 08:00:00 (UTC+08:00)"
    for record in ({},{"updatedAt":"2026-11-07T00:00:00Z"},{"createdAt":"invalid"},{"createdAt":"2026-11-07T00:00:00"}):
        assert generation_date(record)=="Generation time unavailable"
