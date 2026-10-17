import json

import pytest
from pydantic import ValidationError

from cashguard.contracts.surface import build_contract_app
from cashguard.contracts.ui import UiHttpError


def test_ui_error_body_shape():
    body = UiHttpError(code="RATE_LIMITED", message_ru="Повторите через 30 с.", retry_after_sec=30)
    assert json.loads(body.model_dump_json()) == {
        "code": "RATE_LIMITED",
        "message_ru": "Повторите через 30 с.",
        "retry_after_sec": 30,
    }
    with pytest.raises(ValidationError):
        UiHttpError.model_validate({"code": "TEAPOT", "message_ru": "?"})
    with pytest.raises(ValidationError):
        UiHttpError.model_validate({"code": "INTERNAL", "message_ru": ""})
    with pytest.raises(ValidationError):
        UiHttpError.model_validate({"code": "INTERNAL", "message_ru": "x", "detail": "extra"})


def test_ui_upload_errors_are_in_openapi():
    spec = build_contract_app().openapi()
    responses = spec["paths"]["/api/ui/jobs"]["post"]["responses"]
    for status in ("413", "415", "422", "429", "507"):
        ref = responses[status]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/UiHttpError")
    assert "/api/v1/analyze" in spec["paths"]
    jury = spec["paths"]["/api/v1/analyze"]["post"]["responses"]
    assert not any("UiHttpError" in json.dumps(r) for r in jury.values()), (
        "API жюри не отвечает телом UiHttpError"
    )
