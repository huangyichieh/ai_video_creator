import base64
from io import BytesIO

import httpx
import pytest
from openai import OpenAI
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.error_reports import reports,record_failure
from story_channel.llm import ProviderError
from story_channel.location_details import add_draft,generate,generation_status,task_key
from story_channel.location_details import validate_image_size
from story_channel.media_providers import OpenAIImageProvider
from test_location_details import prepare
from test_location_master_pack import master_pack
from test_studio import world


@pytest.mark.parametrize("size",[(1536,864),(1920,1088),(1280,720),(2560,1440)])
def test_detail_resolution_accepts_native_images_of_sufficient_quality(size):
    validate_image_size(size)


@pytest.mark.parametrize("size",[(1024,576),(1280,704),(1200,1200)])
def test_detail_resolution_rejects_low_resolution_without_upscaling(size):
    with pytest.raises(ValueError,match="minimum"):
        validate_image_size(size)


def test_failed_detail_keeps_response_and_hint_then_success_saves_image_and_clears_reports(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    add_draft(engine,lid,"desk_area","Desk area","Desk close-up")
    failing = [True]
    image = BytesIO()
    Image.new("RGB",(1536,864),"green").save(image,format="PNG")
    def handle(request):
        assert b'1536x864' in request.content
        if failing[0]:
            return httpx.Response(400,json={"error":{"message":"Invalid size. Width and height must both be divisible by 16.",
                "type":"image_generation_user_error","param":"size","code":"invalid_value"}},headers={"x-request-id":"req_detail_test_failure"})
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(image.getvalue()).decode()}]})
    engine.panorama_image_provider = OpenAIImageProvider(OpenAI(api_key="mock-detail-secret",max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    with pytest.raises(ProviderError,match="HTTP 400"):
        generate(engine,lid,"desk_area","Desk area","Desk close-up")
    status = generation_status(engine,lid,"desk_area")
    assert status["status"] == "failed" and "Parameter: size" in status["error"]
    saved = reports(engine.path,task_key(lid,"desk_area"))
    assert len(saved) == 1
    assert saved[0]["responses"][0]["body"]["error"]["param"] == "size"
    assert saved[0]["responses"][0]["request_id"] == "req_detail_test_failure"
    assert "mock-detail-secret" not in str(saved)
    failing[0] = False
    state = generate(engine,lid,"desk_area","Desk area","Desk close-up")
    assert not reports(engine.path,task_key(lid,"desk_area"))
    assert generation_status(engine,lid,"desk_area")["status"] == "succeeded"
    detail = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    asset = state.records[detail["key"]].asset
    assert asset.generation_params["providerRequestSize"] == "1536x864"
    with Image.open(engine.local_path(detail["file"])) as result:
        assert result.size == (1536,864)


def test_existing_failure_is_visible_in_detail_box_and_opens_error_report(master_pack):
    engine,lid,_,_ = prepare(master_pack)
    add_draft(engine,lid,"desk_area","Desk area","Desk close-up")
    record_failure(engine.path,task_key(lid,"desk_area"),ProviderError("OpenAI media request failed (HTTP 400). Parameter: size."),
        [{"status_code":400,"method":"POST","endpoint":"/v1/images/edits","body":{"error":{"message":"Invalid size"}}}])
    def display(engine,lid):
        from story_channel.dialog_ui import show_pending_dialog
        from story_channel.location_details_ui import render
        show_pending_dialog()
        render(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    assert any("HTTP 400" in item.value for item in app.error)
    assert any("1536×864" in item.value for item in app.caption)

    key = f"location-details-{engine.project_id}-{lid}-error-desk_area"
    assert app.button(key=key).label == "Error Report"
    app.button(key=key).click().run()
    assert not app.exception
    assert app.get("dialog")


def test_validation_failure_is_logged_before_any_provider_request(master_pack):
    engine,lid,provider,_ = master_pack
    with pytest.raises(ValueError,match="Approve all four"):
        generate(engine,lid,"desk_area","Desk area","Desk close-up")
    assert not provider.calls
    assert generation_status(engine,lid,"desk_area")["status"] == "failed"
    assert reports(engine.path,task_key(lid,"desk_area"))[0]["responses"] == []
