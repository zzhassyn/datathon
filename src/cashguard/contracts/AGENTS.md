# contracts/ — правила для агентов

- Владелец: **S0**. Остальные сессии предлагают изменения PR-ом к S0; такой PR маленький и мержится первым.
- Только типы (Pydantic v2, `frozen=True`, `extra="forbid"`) и `typing.Protocol`. Никаких импортов из `cashguard.*`, кроме самого `contracts`, и никаких ML-библиотек (import-linter).
- `schemas/jury_job.schema.json` заморожена: менять только с одобрения человека. `schemas/generated/*` и `web/src/shared/api/openapi.json` не править руками — `uv run just schemas`.
- После любой правки: `uv run just schemas`, затем `uv run pytest tests/contract -q`.
