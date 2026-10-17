"""Сгенерированные схемы и OpenAPI не отстают от контрактов; поверхность API соответствует ТЗ."""

import json

from cashguard.contracts import export

REQUIRED_PATHS = {
    ("post", "/api/v1/analyze"),
    ("get", "/api/v1/jobs/{job_id}"),
    ("get", "/api/v1/admin/status"),
    ("post", "/api/ui/jobs"),
    ("get", "/api/ui/jobs/{job_id}"),
    ("get", "/api/ui/jobs/{job_id}/video.mp4"),
    ("get", "/api/ui/jobs/{job_id}/frames/{name}"),
    ("get", "/api/ui/catalog"),
    ("get", "/health"),
    ("get", "/ready"),
}


def test_generated_files_are_fresh() -> None:
    assert export.main(["--check"]) == 0, "запустите: uv run just schemas"


def _openapi() -> dict[str, object]:
    return json.loads(export.OPENAPI_PATH.read_text(encoding="utf-8"))


def test_openapi_has_all_routes() -> None:
    paths = _openapi()["paths"]
    assert isinstance(paths, dict)
    found = {(method, path) for path, ops in paths.items() for method in ops}
    assert found >= REQUIRED_PATHS


def test_analyze_is_multipart_with_file_and_api_key() -> None:
    op = _openapi()["paths"]["/api/v1/analyze"]["post"]  # pyright: ignore[reportIndexIssue]
    body = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body["required"] == ["file"]
    header_names = {p["name"] for p in op["parameters"] if p["in"] == "header"}
    assert "X-API-Key" in header_names


def test_jury_job_response_in_openapi() -> None:
    op = _openapi()["paths"]["/api/v1/jobs/{job_id}"]["get"]  # pyright: ignore[reportIndexIssue]
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/JuryJobResponse")
