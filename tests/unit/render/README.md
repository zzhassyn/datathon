# `tests/unit/render/` — тесты рендера

Владелец: S1. `test_jury.py` проверяет замороженную схему, статусы, таймкоды/PTS, safe fallback, no-false-clean, ограничения списков, JPEG framing и действительное декодирование synthetic JPEG через PyAV. UI проверяется на отсутствие base64 и безопасные URL.

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/render -q
```

Fixtures — только публичный синтетический JPEG из `tests/fixtures/jury_example.json`; видео/кадры датасета не используются.
