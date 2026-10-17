from fastapi import APIRouter, Request, Security

from cashguard.api.deps import require_api_key
from cashguard.api.ingest_service import ingest_upload
from cashguard.api.views import jury_response

router = APIRouter(prefix="/api/v1", dependencies=[Security(require_api_key)])
MULTIPART_FILE_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


@router.post("/analyze", openapi_extra=MULTIPART_FILE_BODY)
@router.post("/analyze/", include_in_schema=False)
async def analyze(request: Request):
    return {"job_id": await ingest_upload(request)}


@router.get("/jobs/{job_id}")
@router.get("/jobs/{job_id}/", include_in_schema=False)
async def get_job(job_id: str, request: Request):
    return await jury_response(request, job_id)
