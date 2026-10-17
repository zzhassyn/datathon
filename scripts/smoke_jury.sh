#!/usr/bin/env bash
# Usage fixed by roadmap: <URL> <key> <synthetic.mov>.
set -euo pipefail
if [ "$#" -ne 3 ]; then
  echo 'Usage: bash scripts/smoke_jury.sh <URL> <key> <synthetic.mov>' >&2
  exit 2
fi
base_url="${1%/}"
api_key="$2"
video="$3"
script_root="$(cd "$(dirname "$0")/.." && pwd)"
if [ -n "${CG_PYTHON:-}" ]; then
  python_bin="$CG_PYTHON"
elif [ -x "$script_root/.venv/bin/python" ]; then
  python_bin="$script_root/.venv/bin/python"
else
  python_bin=python3
fi
export PYTHONPATH="$script_root/src${PYTHONPATH:+:$PYTHONPATH}"
out_dir="${CG_DATA_DIR:-var}/smoke"
umask 077
mkdir -p "$out_dir"
post_file="$(mktemp "$out_dir/post_XXXXXX.json")"
started="$SECONDS"
code="$(curl -sS --max-time 90 -o "$post_file" -w '%{http_code}' \
  -X POST "$base_url/api/v1/analyze" -H "X-API-Key: $api_key" \
  -F "file=@$video;type=video/quicktime")"
if [ "$code" != 200 ]; then
  echo "Upload failed: HTTP $code; response saved locally." >&2
  exit 1
fi
job_id="$("$python_bin" - "$post_file" <<'PY'
import json, re, sys
job = json.load(open(sys.argv[1]))["job_id"]
if not re.fullmatch(r"[a-f0-9]{32}", job):
    raise SystemExit("Invalid job_id")
print(job)
PY
)"
result_file="$out_dir/result_${job_id}.json"
while [ "$((SECONDS - started))" -lt 300 ]; do
  code="$(curl -sS --max-time 10 -o "$result_file" -w '%{http_code}' \
    "$base_url/api/v1/jobs/$job_id" -H "X-API-Key: $api_key")"
  if [ "$code" != 200 ]; then
    echo "Polling failed: HTTP $code; response saved locally." >&2
    exit 1
  fi
  state="$("$python_bin" - "$result_file" <<'PY'
import json, sys
from cashguard.pipeline.render.jury import validate_jury
result = json.load(open(sys.argv[1]))
validate_jury(result)
print(result["status"])
PY
)"
  if [ "$state" = done ]; then
    "$python_bin" - "$result_file" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
print(f"job_id={result['job_id']} status=done verdict={result['verdict']} violations={len(result['violations'])}")
print(f"Validated result saved: {sys.argv[1]}")
PY
    exit 0
  fi
  if [ "$state" = failed ]; then
    echo "job_id=$job_id status=failed; response saved locally." >&2
    exit 1
  fi
  sleep 0.25
done
echo "job_id=$job_id exceeded 300 seconds" >&2
exit 1
