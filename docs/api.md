# API CashGuard

> **Назначение:** полный контракт HTTP-интерфейса CashGuard. Jury API по ТЗ §10, UI API, health, admin, статика, кэш, тесты. По этому документу агенты реализуют `src/cashguard/api/**`, а web генерирует типы.
> **Владелец:** S1 (платформа и оценка). Замороженная схема [`jury_job.schema.json`](../src/cashguard/contracts/schemas/jury_job.schema.json) и `contracts/**` — S0. **Статус:** черновик v1, 17.10.2026.

Главное правило: ответ jury API проверяется автоматически. Одно лишнее поле или неверная форма стоит всего видео. Поэтому jury JSON рендерится один раз, валидируется по замороженной схеме и отдаётся файлом.

---

## 1. Карта маршрутов

Все маршруты обслуживает один процесс uvicorn на `127.0.0.1:8000`. Снаружи доступ только через туннель (Cloudflare named tunnel, резерв — Tailscale Funnel). Фронтенд и API на одном origin, CORS не нужен.

| Метод | Путь | Доступ | Кто вызывает | Код |
|---|---|---|---|---|
| POST | `/api/v1/analyze` (+ `/api/v1/analyze/`) | `X-API-Key` | жюри, `smoke_jury.sh`, `jury-sim` | `api/routes/jury.py`, `api/upload.py`, `api/ingest_service.py` |
| GET | `/api/v1/jobs/{job_id}` (+ `/api/v1/jobs/{job_id}/`) | `X-API-Key` | жюри | `api/routes/jury.py` |
| GET | `/api/v1/admin/status` | только team-ключ | команда, `just status` | `api/routes/admin.py` |
| POST | `/api/ui/jobs` | без ключа, лимиты по IP | веб-интерфейс | `api/routes/ui.py` |
| GET | `/api/ui/jobs/{job_id}` | без ключа | веб-интерфейс (опрос 1 с) | `api/routes/ui.py`, `api/views.py` |
| GET | `/api/ui/jobs/{job_id}/video.mp4` | без ключа | `<video>` | `api/routes/ui.py`, `api/preview.py` |
| GET | `/api/ui/jobs/{job_id}/frames/{name}` | без ключа | `<img>`, лайтбокс | `api/routes/ui.py` |
| GET | `/api/ui/catalog` | без ключа | страница типов, карточки | `api/routes/ui.py` |
| POST | `/api/ui/session` | только если задан `CG_UI_ACCESS_CODE` | веб-интерфейс | `api/routes/ui.py` |
| GET | `/api/ui/jobs/{job_id}/events` | зарезервирован, **не в v1** | — | — |
| GET | `/health` | без ключа | супервизор, uptime-проверки | `api/routes/health.py` |
| GET | `/ready` | без ключа | точка в футере UI, чек-лист жюри | `api/routes/health.py` |
| GET | `/`, `/assets/*`, `/robots.txt`, `/favicon.svg` | без ключа | браузер | `api/main.py` |
| GET | `/docs`, `/openapi.json` | без ключа | люди, генерация типов | FastAPI |

---

## 2. Общие правила

- **Ошибки — всегда JSON** вида `{"detail": "..."}`, никогда HTML. Глобальные обработчики исключений стоят в `api/main.py`. Исключения вне нашего кода перечислены в разделе 3.5.
- **Неизвестный маршрут** — `404 {"detail": "Not Found"}`. Неверный метод — `405 {"detail": "Method Not Allowed"}`. SPA-fallback нет: виды UI выбираются через query (`/?job=<id>`).
- **Нет 307.** Для двух jury-маршрутов вариант со слешем на конце зарегистрирован явно (раздел 3.6).
- **Время** — секунды от начала видео, `float`. Таймкоды берутся из PTS, а не из `frame_index / fps`.
- **job_id** — `uuid4().hex`: 32 символа `[0-9a-f]`.
- **GZip**: `GZipMiddleware(minimum_size=1024, exclude_content_types=["video/mp4", "image/jpeg"])`. Сжатие включается, только если клиент прислал `Accept-Encoding: gzip`. curl делает это только с `--compressed`.
- **Логи** пишут имя принципала (`jury`, `team`, `ui`), но никогда ключ, base64 или кадры.
- `TrustedHostMiddleware` не используется: ошибка в списке хостов вернула бы жюри 400.

---

## 3. Jury API (`/api/v1`)

Контракт ТЗ §10: два шага, `POST /api/v1/analyze` и `GET /api/v1/jobs/{job_id}`. Ключ в заголовке `X-API-Key`.

### 3.1. Аутентификация

- Роутер `/api/v1` подключён с зависимостью `require_api_key` (`api/deps.py`): `APIKeyHeader(name="X-API-Key", auto_error=False)`.
- Ключи задаются в `CG_API_KEYS` в формате `jury:<tok>,team:<tok>` (`SecretStr`, файл `~/.config/cashguard/.env`, `chmod 600`).
- Сравнение: sha256 присланного ключа сверяется через `hmac.compare_digest` с sha256 **каждого** настроенного ключа. Цикл не выходит досрочно, поэтому время ответа не зависит от того, какой ключ совпал.
- Нет ключа, пустой или неверный ключ — `401`. Ответ уходит **до чтения первого байта тела**. Обработчик загрузки принимает только `request: Request`: FastAPI парсит форму до зависимостей, поэтому параметр `UploadFile` принял бы 29 МБ без проверки ключа. Это закрепляет `tests/api/test_auth_before_body.py`.
- Оба ключа (`jury` и `team`) дают доступ к `analyze` и `jobs`. Задачи из `/api/v1` получают `source=jury` и приоритет 0. Разделения задач по ключам нет: любой верный ключ читает любой `job_id`.
- Jury-ключ **никогда** не ограничивается по частоте и не упирается в лимит очереди.
- Выпуск ключей: `uv run just gen-key` (`scripts/gen_key.py`, `secrets.token_urlsafe(32)`). Жюри передаётся адрес и jury-ключ по закрытому каналу (README, ТЗ §8). В git ключи не попадают. После окна жюри jury-ключ перевыпускается, `var/` очищается ([jury_mode.md](jury_mode.md)).

Эскиз зависимости:

```python
# src/cashguard/api/deps.py (эскиз)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(request: Request, key: str | None = Security(api_key_header)) -> str:
    if not key:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    principal: str | None = None
    for name, ref in request.app.state.key_digests:  # [(name, sha256)] считаются в lifespan
        if hmac.compare_digest(digest, ref):          # без break: проверяем все ключи
            principal = name
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    return principal
```

### 3.2. `POST /api/v1/analyze`

**Запрос**

- `Content-Type: multipart/form-data`, поле `file` с видео.
- Принимается любой `Content-Type` части (curl шлёт `application/octet-stream`). Имя файла клиента игнорируется. В режиме `stub` исход (`violation` или `clean`) выбирается по метатегу контейнера `comment=cg-stub:<verdict>`, который пишет `scripts/make_synthetic_mov.py --verdict`; читает его `pipeline/ingest/probe.py` (карточка P0-S1-04 в [roadmap.md](roadmap.md)).
- Формат проверяется по содержимому, а не по расширению: подходит любой QuickTime/ISO-BMFF (`.mov`, `.mp4`). ТЗ требует `.mov` около 29 МБ (FAQ 25).

**Порядок обработки** (порядок важен, его проверяют тесты):

| Шаг | Что делаем | При ошибке |
|---|---|---|
| a | `require_api_key` | `401`, тело не читается |
| b | фиксируем `t0 = time.time()`: это начало всех дедлайнов (раздел 3.7) | — |
| c | `Content-Length` больше лимита | `413` до чтения тела |
| d | свободно на диске меньше `CG_MIN_FREE_DISK_GB` (2 ГБ) | `507` |
| e | `request.stream()` → `StreamingFormDataParser`; поле `file` пишется в `var/jobs/{id}/input.part` с бегущим sha256 и счётчиком байт | превышение лимита в потоке — `413`, `.part` удаляется |
| f | поля `file` нет | `422` |
| g | байты `[4:8]` сверяются со списком знакомых атомов QuickTime/ISO-BMFF: `ftyp`, `moov`, `mdat`, `wide`, `free`, `skip`, `pnot`. Проверка рекомендательная: незнакомый атом (например `uuid`) не отклоняется, а только пишется в лог; решение принимает шаг i | — |
| h | `fsync` в потоке, затем `os.replace` в `input.mov` | — |
| i | лёгкий probe через PyAV в потоке (≤ 50 мс, оценка): длительность, w/h, кодек, поворот | PyAV не открывает контейнер — `415` (это единственная причина 415 по формату) |
| j | дедупликация (ниже), иначе `INSERT jobs`: `status=queued`, `stage=perception`, `priority=0`, `source=jury`, `received_at=t0`, все рубежи дедлайна; событие `accepted` в `job_events` | — |
| k | `COMMIT`, ответ `200` | — |

После шага k задача долговечна: видео на диске после `fsync`, строка в SQLite закоммичена. Любая ошибка до этого момента возвращается клиенту, и он может повторить запрос.

**Ответ**

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store

{"job_id": "9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b"}
```

Код 200, а не 202: неизвестно, не сравнивает ли автоматическая проверка код с 200. Ответ обычно уходит меньше чем через 0,5 с после последнего байта (оценка, подтверждает `just perf`).

**Дедупликация** по `sha256` файла:

| Случай | Что происходит | Что видит клиент |
|---|---|---|
| Готовый результат в `result_cache` по `(sha256, pipeline_version, config_hash)`, только для уровня `full` | вставляется строка сразу в `done`; `jury.json`, `ui_result.json` и `evidence/` копируются с подстановкой нового `job_id` | новый `job_id`, первый же GET — `done` |
| Тот же `sha256` сейчас в обработке | вставляется строка с `linked_to` на исходную задачу | новый `job_id`, `processing`, затем `done` вместе с исходной |
| Иначе | обычная задача | новый `job_id` |

Каждый POST получает **свой** `job_id`. В ответе GET поле `job_id` всегда равно `job_id` из пути. Деградированные результаты (`rules`, `partial`) в кэш не попадают.

### 3.3. `GET /api/v1/jobs/{job_id}`

Порядок: ключ (`401`), затем формат `^[0-9a-f]{32}$` (иначе `404`), затем один индексный `SELECT` с переходом по `linked_to` (не найдено — `404`), затем ленивая финализация (раздел 3.7), затем ответ.

Все известные состояния отвечают **`200`**, `Content-Type: application/json`, `Cache-Control: no-store`.

**Соответствие статусов**

| Внутреннее состояние (`jobs.status`, `stage`) | `status` в ответе | Тело |
|---|---|---|
| `queued` или `running`, любой `stage` (`perception`, `vlm`) | `processing` | фиксированная форма, `verdict: null`, `violations: []` |
| `queued`/`running` с готовым предварительным результатом | `processing` | то же; предварительный результат жюри не показывается |
| `done` (любой `result_level`: `full`, `partial`, `rules`) | `done` | файл `var/jobs/{id}/jury.json` через `FileResponse` |
| `failed` | `failed` | фиксированная форма, `verdict: null`, `violations: []` |

**processing**

```json
{"job_id": "9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b", "status": "processing", "verdict": null, "violations": []}
```

**done, нарушений нет**

```json
{"job_id": "9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b", "status": "done", "verdict": "no_violation", "violations": []}
```

**done, есть нарушение** (иллюстрация: тексты `type` и `description` берутся из `configs/catalog/<CODE>.yaml`; base64 сокращён)

```json
{
  "job_id": "9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b",
  "status": "done",
  "verdict": "violation",
  "violations": [
    {
      "type": "Ключ оставлен в замке сейфа",
      "timecode_sec": 14.0,
      "description": "В 14,0 с кассир покидает кассу; в замке сейфа остаётся ключ. Может свидетельствовать о несоблюдении п. 80 Правил осуществления кассовых операций…, утв. постановлением Правления НБРК от 29.11.2019 № 231.",
      "evidence_frames": [
        {"timestamp_sec": 14.0, "image_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD..."},
        {"timestamp_sec": 14.0, "image_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD..."},
        {"timestamp_sec": 12.5, "image_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD..."}
      ]
    }
  ]
}
```

Норма в примере — косвенная, поэтому формулировка «может свидетельствовать» ([case_analysis.md §7](../analysis/case_analysis.md)).

**failed**

```json
{"job_id": "9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b", "status": "failed", "verdict": null, "violations": []}
```

Причина (`error_code`, сообщение на русском) в jury JSON не попадает: в схеме ТЗ для неё нет поля. Её видно в UI (`error`) и в `admin/status`.

**Правила полей done-ответа**

| Поле | Правило |
|---|---|
| `type` | стабильная русская строка `title_ru` из `configs/catalog/<CODE>.yaml`. Один код — всегда одна строка. Коды (`SAFE_KEY_LEFT`) наружу не идут |
| `timecode_sec` | Python `float`, `timecode_sec = round(pts_event, 2)`; `0 ≤ t ≤ duration`. `pts_event` — PTS кадра, на котором нарушение **видно**, по `timecode_policy` каталога (`event`, `exit`, `state_start`, `omission_due`) |
| `description` | шаблон `template_ru` из каталога, заполненный через `str.format_map` фактами правила, плюс правовая фраза одного из трёх видов: «Основание: п. X <акт>» (прямая норма); «Может свидетельствовать о несоблюдении п. X <акт>» (косвенная); «Прямой нормы нет; ближайшая — …». Свободный текст VLM сюда не попадает |
| `evidence_frames` | 1–3 кадра: (1) кадр события в `timecode_sec` с рамками cv2; (2) увеличенный фрагмент ×3–4 того же момента; (3) кадр-контекст за 1,5 с до события. Все `timestamp_sec = round(pts, 2)` той же функцией, где `pts` — реальный PTS сэмплированного кадра из frame store |
| `evidence_frames[0].timestamp_sec` | равен `timecode_sec` точно: оба — `round(pts_event, 2)` одной функцией |
| `description` (непустота) | `minLength: 1` в замороженной схеме (решено 17.10.2026, раздел 12, вопрос 10); шаблон всегда даёт непустую строку, дополнительно проверяет `tests/unit/render` |
| `image_base64` | сырой JPEG (q85) в стандартном base64, без `data:`-префикса и без переводов строк, начинается с `/9j/` |
| `violations` | не больше 5, по возрастанию `timecode_sec`; нарушения одного кода в пределах 10 с сливаются |
| сигналы | **никогда** не попадают в jury JSON (только UI) |
| лишние ключи | запрещены на всех уровнях |

Размер done-ответа 0,3–1,5 МБ (оценка). Это заранее отрендеренный файл: GET не сериализует ничего на лету, время ответа на origin ≤ 5 мс (оценка).

**Что проверяет схема, а что — код**

| Инвариант | Где проверяется |
|---|---|
| ровно ключи ТЗ, без лишних, на всех уровнях | схема (`additionalProperties: false`) |
| `status` ∈ `processing`, `done`, `failed` | схема |
| `done` ⇒ `verdict` не `null` | схема (`if`/`then`) |
| `processing`/`failed` ⇒ `verdict: null`, `violations: []` | схема (`else`) |
| `verdict = violation` ⇔ `violations` не пуст | схема (два `if`/`then`) |
| 1–3 кадра; `timecode_sec ≥ 0`; `timestamp_sec ≥ 0`; `type` не пустой | схема |
| `image_base64` начинается с `/9j/` | схема (`pattern`) |
| base64 строгий, JPEG целый (`FF D8 … FF D9`) | `tests/unit/render`, `tests/contract` |
| `evidence_frames[0].timestamp_sec == timecode_sec` | `tests/unit/render` |
| `timecode_sec ≤ duration`, `round(pts, 2)` | `tests/unit/render` |
| `description` не пустая строка | `tests/unit/render` |
| ≤ 5 нарушений, по времени | `tests/unit/render` |
| `type` есть в каталоге | `tests/unit/render`, `tests/contract/test_catalog_complete.py` |
| `job_id` равен `job_id` из пути, 32 hex | `tests/api` |

`render.jury` (`pipeline/render/jury.py`) валидирует каждый файл по замороженной схеме **до** публикации. При ошибке включается безопасный рендер: перекодирует кадры, зажимает таймкоды в `[0, duration]`, обрезает кадры до 3, заново применяет инвариант вердикта и валидирует ещё раз. Файлы пишутся как `tmp → fsync → os.replace`, и только потом CAS меняет статус в SQLite. Поэтому `done` без `jury.json` не бывает.

### 3.4. Коды ответов jury API

Тексты `detail` ниже — рекомендуемые. Контракт — код ответа и наличие ключа `detail`. Тесты проверяют код и JSON-форму, а не текст.

| Код | Маршрут | Когда | `detail` | Что делать клиенту |
|---|---|---|---|---|
| 200 | оба | успех; для GET — любое известное состояние | — | — |
| 401 | все `/api/v1/*` | нет `X-API-Key`, ключ пустой или неверный; тело не читается | `invalid or missing API key` | проверить ключ |
| 403 | `admin/status` | верный ключ, но не `team` | `forbidden` | — |
| 404 | GET `jobs` | `job_id` не `^[0-9a-f]{32}$`, неизвестен или удалён очисткой (`CG_RETENTION_HOURS`, по умолчанию 24 ч; в окне жюри очистка выключена) | `job not found` | проверить `job_id` |
| 404 | любой | неизвестный маршрут | `Not Found` | — |
| 405 | любой | неверный метод | `Method Not Allowed` | — |
| 413 | POST | `Content-Length` или фактический размер > 100 МБ (100 × 10^6 байт) | `file too large` | — |
| 415 | POST | PyAV не открывает контейнер (атом в начале файла — только подсказка, шаг g) | `unsupported media type` | прислать `.mov` |
| 422 | POST | нет поля `file` или тело не разбирается как multipart | `multipart field 'file' is required` | проверить `-F "file=@..."` |
| 500 | любой | непредвиденная ошибка; трейс только в `var/logs/api.jsonl` | `internal error` | повторить; не должно случаться |
| 503 | любой | превышен `--limit-concurrency 64` у uvicorn | тело формирует uvicorn (`text/plain`, проверить) | повторить позже |
| 507 | POST | свободно на диске < 2 ГБ | `insufficient storage` | повторить позже |

### 3.5. Ответы не от нашего кода

| Код | Источник | Когда | Тело |
|---|---|---|---|
| 413 | Cloudflare edge | тело больше 100 MB (лимит планов Free/Pro) | HTML Cloudflare, не наш JSON |
| 502 | Cloudflare / cloudflared | туннель жив, origin не отвечает (рестарт api, режим TRAIN) | HTML Cloudflare |
| 530 | Cloudflare | туннель не подключён | HTML Cloudflare (проверить точный код на прогоне) |
| 503 | uvicorn | `--limit-concurrency` | `text/plain` |

Поэтому сервис никогда не переводится в режим TRAIN во время окна жюри ([jury_mode.md](jury_mode.md)).

### 3.6. Слеш на конце

- Каждый jury-обработчик зарегистрирован дважды: `/api/v1/analyze` и `/api/v1/analyze/`, `/api/v1/jobs/{job_id}` и `/api/v1/jobs/{job_id}/`.
- Варианты со слешем имеют `include_in_schema=False` и в OpenAPI не видны.
- Автоматический `redirect_slashes` не нужен: маршрут находится сразу, и 307 не возникает. 307 на POST ломает curl без `-L`.
- Для `admin/status` и `/api/ui/*` алиасы не нужны: их вызывает наш же код.

```python
# src/cashguard/api/routes/jury.py (эскиз)
router = APIRouter(prefix="/api/v1", dependencies=[Security(require_api_key)])

@router.post("/analyze", response_model=AnalyzeAccepted, openapi_extra=MULTIPART_FILE_BODY)
@router.post("/analyze/", response_model=AnalyzeAccepted, include_in_schema=False)
async def analyze(request: Request) -> AnalyzeAccepted:  # только Request: ключ проверяется до тела
    ...

@router.get("/jobs/{job_id}", responses={200: {"model": JuryJobResponse}})
@router.get("/jobs/{job_id}/", include_in_schema=False)
async def get_job(job_id: str) -> Response:
    ...
```

### 3.7. Правило 5 минут: дедлайны от t0 и ленивая финализация

ТЗ §10 и FAQ 14: если сервис не вернул результат за 5 минут, видео считается необработанным.

**t0** — момент начала обработчика загрузки: после проверки ключа, до первого байта тела. Время передачи 29 МБ через туннель входит в наш бюджет. Все рубежи — wall-clock epoch в строке `jobs` (общие для процессов). Внутри процесса бюджет считается по monotonic.

Рубежи из `configs/deadlines.yaml`:

| Рубеж | От t0 | Что происходит |
|---|---|---|
| `vision_target` | +150 с | цель конца перцепции. Уровень `full`/`fast`/`min` выбирается по EWMA из `perf_stats`, длительности видео и очереди, чтобы успеть |
| `vlm_start_cutoff` | +180 с | позже VLM не стартует. Условие запуска: `now + est_vlm + 15 с < vlm_start_cutoff` и свободно ≥ `CG_MIN_FREE_MEM_GB` (3,0 ГБ; раздел 11) |
| `min_level_after` | +200 с | задача, которую воркер перцепции взял позже этого рубежа, сразу идёт на уровне `min` |
| `vlm_stop` | +210 с | VLM прерывается: дедлайн проверяется на каждой группе и каждом чанке генерации |
| `finalize_at` | +225 с | задача с предварительным результатом принудительно становится `done` (`result_level=rules`, деградация `deadline_finalize`) |
| `perception_hard_stop` | +235 с | перцепция останавливается и закрывает задачу по уже обработанным кадрам (ниже) |
| ожидаемый худший случай | ≤ +240 с | цель для пачки из 5 видео по 145 с |
| аварийный предел | ≤ +250 с | задача, дошедшая до `perception_hard_stop`, к этому моменту уже `done` |
| лимит жюри | 300 с | запас 50–60 с на интервал опроса жюри и сеть |

Имя `min_level_after` — предложение S1; значение +200 с и рубеж `perception_hard_stop` закреплены в [ADR-0005](adr/0005-deadline-tiers-provisional-degradation.md).

**Жёсткая остановка перцепции.** Воркер перцепции сверяет бюджет на каждом кадре. На рубеже `perception_hard_stop` он прекращает декодирование и прогоняет scene → rules → verdict → evidence по уже обработанным кадрам. Затем пишет предварительный `jury.json` с деградацией `perception_truncated`. `finalize_at` к этому моменту уже прошёл, поэтому тот же воркер сразу выполняет CAS в `done` (`result_level=rules`). Если воркер не успел и это, задачу закрывают sweeper или ленивый GET (ниже). После `t0 + 250 с` ни одна задача не остаётся в `processing`. Исключение — зависший воркер (например, `SIGSTOP`): он бюджет не проверяет. Его убивает supervisor (нет прогресса > 60 с), вторая попытка идёт с тем же рубежом; этот случай проверяет репетиция №1 с критерием ≤ 300 с от POST ([jury_mode.md](jury_mode.md), раздел 4.2).

**Предварительный результат.** Сразу после перцепции воркер всегда пишет полный, валидный по схеме `jury.json` по правилам (без VLM). VLM его только уточняет. Ответ «нет» от VLM понижает балл кандидата, но не добавляет нарушений.

**Три места финализации** с одним и тем же идемпотентным compare-and-set (`UPDATE jobs … WHERE id=? AND status!='done'`, `rowcount == 1` означает, что выиграл этот вызов):

1. воркер, при успехе;
2. sweeper в lifespan API, каждые 2 с;
3. **лениво в GET**: если `now > finalize_at`, предварительный результат есть, а статус ещё не `done`, обработчик сам выполняет тот же CAS и отдаёт `jury.json`. Ответ приходит, даже если задача sweeper умерла.

**Лестница деградации** (жюри видит ту же форму ответа; причины видны в UI и admin):

| Ситуация | Результат | `degradations` |
|---|---|---|
| нагрузка или мало времени до `vision_target` | перцепция `fast` (1,5 fps) или `min` (1 fps) | `perception_fast`, `perception_min` |
| не успеваем до `vlm_start_cutoff`, мало памяти, длинная очередь VLM, VLM выключен | `done`, `result_level=rules` | `vlm_deadline`, `vlm_memory`, `vlm_backlog`, `vlm_disabled` |
| падение или зависание VLM-воркера | отдаётся предварительный результат | `vlm_crash` |
| дошли до `finalize_at` | отдаётся предварительный результат | `deadline_finalize` |
| дошли до `perception_hard_stop`, перцепция не закончена | правила по обработанным кадрам, `done`, `result_level=rules` | `perception_truncated` |
| задачу взяли из очереди позже +200 с | перцепция сразу на уровне `min`, та же остановка | `perception_min` |
| падение перцепции | повтор (попытка 2) на уровне `fast` | — |
| попытки перцепции исчерпаны, предварительного результата нет | `failed`, `PIPELINE_CRASH` | — |

**Когда `failed`.** Штатно только одна причина: глубокая проверка видео (`UNSUPPORTED_VIDEO`): нет видеопотока, длительность вне 1–900 с, ширина или высота больше 4096, первый кадр не декодируется. Вторая — `PIPELINE_CRASH`, когда попытки перцепции исчерпаны, а предварительного результата нет. Код `INTERNAL` зарезервирован в `ErrorCode`.

**Перцепция не успела к `finalize_at`.** Этот случай закрывает рубеж `perception_hard_stop` (выше): задача становится `done` не позже `t0 + 250 с`. Дополнительно работают выбор уровня по EWMA и убийство зависшего воркера супервизором (нет прогресса > 60 с). Приёмка — карточка P2-S1-01 в [roadmap.md](roadmap.md): перцепция искусственно замедлена, а ответ всё равно `done` ≤ 250 с.

**Ожидаемые времена** от конца загрузки (оценка, `scripts/bench_mac.py` и `just perf` заменят их замерами; гейты меряют от t0):

| Сценарий | Предварительный результат | Финальный результат |
|---|---|---|
| типичный ролик 15–25 с | ≤ 7–10 с | ≤ 25–35 с |
| Х-14 (≈ 142 с; бюджет на 145 с) | ≤ 25–30 с | ≤ 60–65 с |
| 5 типичных роликов разом | — | последний ≈ 70–100 с |
| 5 роликов по 145 с разом | — | последний ≈ 175–190 с (обязательно ≤ 240 с) |

### 3.8. Лимиты

| Параметр | Значение | Где задаётся |
|---|---|---|
| Размер загрузки | 100 МБ = 100 × 10^6 байт (`CG_MAX_UPLOAD_MB=100`), та же десятичная единица, что у лимита Cloudflare | settings |
| Тело запроса на Cloudflare Free/Pro | 100 MB, сверх — 413 от edge | Cloudflare |
| Origin timeout Cloudflare | 100 с; POST отвечает сразу после загрузки, GET — мгновенно | Cloudflare |
| Свободный диск для приёма | ≥ 2 ГБ (`CG_MIN_FREE_DISK_GB`), иначе 507 | settings |
| Длительность видео | 1–900 с, иначе `failed` (`UNSUPPORTED_VIDEO`) | глубокий probe |
| Размер кадра | ширина и высота ≤ 4096 | глубокий probe |
| Видео длиннее 300 с | адаптивный fps, не больше 600 сэмплированных кадров | perception plan |
| Одновременных HTTP-запросов | 64 (`--limit-concurrency`), сверх — 503 от uvicorn | команда uvicorn |
| Keep-alive uvicorn | 100 с: больше, чем 90 с keep-alive у cloudflared, иначе возможны 502 на POST | команда uvicorn |
| Лимиты на jury-ключ | нет: ни rate limit, ни лимита очереди | — |
| Приоритет в очереди | jury 0, ui 1 | `jobs.priority` |
| Нарушений в ответе | ≤ 5 | verdict |
| Кадров на нарушение | 1–3 | render |
| Хранение задачи | 24 ч (`CG_RETENTION_HOURS`), потом каталог и строка удаляются, GET — 404. В режиме жюри очистка выключена: `CG_RETENTION_HOURS=0` или ≥ 168 на всё окно ([jury_mode.md](jury_mode.md), чек-лист заморозки) | sweeper, `jobs/retention.py` |

Про хранение: в архитектуре сказано «удалять каталоги старше 24 ч». Строку задачи нужно удалять вместе с каталогом, иначе GET найдёт `done` без `jury.json`. Это предложение S1, проверить в `jobs/retention.py`.

### 3.9. Примеры клиента

**Точный запрос из ТЗ** (адрес — заглушка из ТЗ, наш адрес — в [README](../README.md)):

```bash
curl -X POST https://team.example.kz/api/v1/analyze \
     -H "X-API-Key: <ключ>" -F "file=@video_017.mov"
```

Ответ: `{"job_id":"<32 hex-символа>"}`. Опрос результата:

```bash
curl https://team.example.kz/api/v1/jobs/<job_id> -H "X-API-Key: <ключ>"
```

**Опрос в bash** (macOS, Linux, Git Bash). Этот сценарий реализует `scripts/smoke_jury.sh`. Интерфейс позиционный и зафиксирован в карточке P0-S1-04 [roadmap.md](roadmap.md): `bash scripts/smoke_jury.sh <URL> <ключ> <файл.mov>`. jq не обязателен.

```bash
#!/usr/bin/env bash
# Загрузка видео и опрос результата, как у жюри (ТЗ, раздел 10).
# Использование: bash scripts/smoke_jury.sh <URL> <ключ> <файл.mov>
set -euo pipefail

BASE="${1:?URL сервиса, например https://team.example.kz}"
KEY="${2:?API-ключ}"
FILE="${3:?путь к видео}"

t0=$(date +%s)
resp=$(curl -sS -X POST "$BASE/api/v1/analyze" -H "X-API-Key: $KEY" -F "file=@$FILE")
job_id=$(printf '%s' "$resp" | sed -n 's/.*"job_id" *: *"\([0-9a-f]*\)".*/\1/p')
[ -n "$job_id" ] || { echo "POST failed: $resp" >&2; exit 1; }
echo "job_id=$job_id upload=$(( $(date +%s) - t0 ))s"

# Полный ответ (с base64-кадрами) — только в var/smoke/: папка в .gitignore и под deny-правилами.
mkdir -p var/smoke
out="var/smoke/result_${job_id}.json"
while :; do
  code=$(curl -sS -o "$out" -w '%{http_code}' "$BASE/api/v1/jobs/$job_id" -H "X-API-Key: $KEY")
  status=$(sed -n 's/.*"status" *: *"\([a-z]*\)".*/\1/p' "$out" | head -n 1)
  echo "$(( $(date +%s) - t0 ))s http=$code status=$status"
  [ "$code" = 200 ] || exit 1
  [ "$status" = processing ] || break
  [ $(( $(date +%s) - t0 )) -lt 300 ] || { echo "TIMEOUT 300 s" >&2; exit 2; }
  sleep 3
done
echo "saved $out"
# В терминал — только сводка, без base64.
if command -v jq >/dev/null; then
  jq '{job_id, status, verdict,
       violations: [.violations[] | {type, timecode_sec, frames: (.evidence_frames | length)}]}' "$out"
fi
```

**Опрос в PowerShell** (Windows PowerShell 5.1 и PowerShell 7):

```powershell
# В Windows PowerShell 5.1 "curl" — это алиас Invoke-WebRequest, поэтому вызываем curl.exe.
$Base = "https://team.example.kz"
$Key  = "<ключ>"
$File = "video_017.mov"

$t0 = Get-Date
$resp = curl.exe -sS -X POST "$Base/api/v1/analyze" -H "X-API-Key: $Key" -F "file=@$File"
$jobId = ($resp | ConvertFrom-Json).job_id
if (-not $jobId) { throw "POST failed: $resp" }
"job_id=$jobId"

$out = Join-Path $env:TEMP "result_$jobId.json"   # не в папку репозитория: в ответе base64-кадры
do {
    $code = curl.exe -sS -o $out -w "%{http_code}" "$Base/api/v1/jobs/$jobId" -H "X-API-Key: $Key"
    $job = Get-Content -Raw -Encoding UTF8 $out | ConvertFrom-Json
    $sec = [int]((Get-Date) - $t0).TotalSeconds
    "$sec s  http=$code  status=$($job.status)"
    if ($code -ne "200") { throw "HTTP $code" }
    if ($job.status -ne "processing") { break }
    if ($sec -ge 300) { throw "TIMEOUT 300 s" }
    Start-Sleep -Seconds 3
} while ($true)

$job.violations | Select-Object type, timecode_sec, @{ n = "frames"; e = { $_.evidence_frames.Count } }
```

Заметки к примерам:

- Полный done-ответ содержит base64-кадры. Его не печатают в терминал и не кладут в корень репозитория или worktree: `smoke_jury.sh` пишет в `var/smoke/`, PowerShell-пример — во временную папку. В `.gitignore` есть `var/` и `result_*.json`. Опрос руками — только со сводкой через `jq '{status,verdict,n:(.violations|length)}'`.
- Проверки на роликах датасета запускают люди, только через `smoke_jury.sh` и только на Mac или на ноутбуке без агентских сессий. Прогоны с Windows-ПК (там работают агенты) идут на синтетике из `scripts/make_synthetic_mov.py` ([jury_mode.md](jury_mode.md), раздел 3).
- Ответ пишется в файл и читается как UTF-8. `Content-Type` ответа — `application/json` без `charset`. `Invoke-RestMethod` в PowerShell 5.1 в этом случае может испортить кириллицу (проверить). Чтение файла через `-Encoding UTF8` от этого не зависит.
- В PowerShell 7 загрузку можно сделать без curl.exe: `Invoke-RestMethod -Method Post -Uri "$Base/api/v1/analyze" -Headers @{ "X-API-Key" = $Key } -Form @{ file = Get-Item $File }`.
- curl для тел больше 1 МБ шлёт `Expect: 100-continue`. uvicorn отвечает `100 Continue`, только когда приложение начинает читать тело. Поэтому при 401 тело до origin не передаётся. Ведёт ли себя так же Cloudflare edge — проверить.
- `--compressed` у curl включает gzip: done-ответ с base64 сжимается примерно на 25% (оценка).
- Нагрузочный прогон: `uv run just jury-sim URL KEY N` — N параллельных загрузок через публичный URL, опрос раз в 3 с ([eval.md](eval.md)).

---

## 4. Admin: `GET /api/v1/admin/status`

- Только team-ключ. Без ключа — 401, с jury-ключом — 403.
- Модель `AdminStatus` (`contracts/health.py`). Источник для `just status` и разбора прогона жюри.
- Алиас со слешем не нужен.

```json
{
  "versions": {"git_sha": "<sha>", "models_hash": "<sha256>", "config_hash": "<sha256>"},
  "models": {"detector": {"version": "v001", "sha256": "<sha256>"}, "pose": {"version": "v001", "sha256": "<sha256>"}},
  "queue": {"perception": 0, "vlm": 1},
  "workers": [
    {"name": "perception", "role": "perception", "pid": 4242, "state": "busy", "alive_at": "...", "progress_at": "...", "job_id": "<id>", "rss_mb": 1800, "mlx_active_mb": null, "quant": null},
    {"name": "vlm", "role": "vlm", "pid": 4243, "state": "busy", "alive_at": "...", "progress_at": "...", "job_id": "<id>", "rss_mb": 900, "mlx_active_mb": 6000, "quant": "8bit"}
  ],
  "recent_jobs": [
    {"id": "<id>", "source": "jury", "status": "done", "perception_level": "full", "result_level": "full", "verdict": "violation", "n": 1, "timings": {"perception": 6.8, "vlm": 9.5}}
  ],
  "memory": {"available_gb": 3.4, "mlx_active_gb": 5.9, "mlx_peak_gb": 6.3},
  "disk_free_gb": 120.5,
  "tunnel_ready": true
}
```

Значения условные. `recent_jobs` — последние 20 задач с таймингами стадий и уровнями. Память — psutil плюс MLX: psutil занижает wired-память MLX, поэтому в логах пишутся оба числа. `tunnel_ready` берётся с метрик cloudflared `127.0.0.1:20241/ready`.

---

## 5. Health: `/health` и `/ready`

Без ключа. Наружу — только грубые булевы флаги. Подробности — только в `admin/status`.

**`GET /health`** — liveness. Всегда `200 {"status": "ok"}` меньше чем за 10 мс, без I/O и без обращения к SQLite. Модель `LiveReport`. Супервизор опрашивает его каждые 5 с и перезапускает API после 3 неудач подряд.

**`GET /ready`** — readiness. Модель `ReadyReport`. `200`, когда выполнены все условия:

| Проверка | Условие |
|---|---|
| `worker_perception` | heartbeat воркера моложе 10 с |
| `worker_vlm` | heartbeat воркера моложе 10 с |
| `models_loaded` | загружены детектор, pose, 3 классификатора, VLM или его запасная квантизация |
| `warmup_done` | прогрев всех моделей завершён |
| `free_mem_ok` | свободно ≥ `CG_MIN_FREE_MEM_GB` (3,0 ГБ) |
| `disk_ok` | свободно на диске ≥ 5 ГБ |
| `queue_ok` | очередь ниже лимита |

```json
{"ready": true, "checks": {"worker_perception": true, "worker_vlm": true, "models_loaded": true, "warmup_done": true, "free_mem_ok": true, "disk_ok": true, "queue_ok": true}, "vlm_quant": "8bit", "version": "<git sha>"}
```

Иначе `503` с тем же телом, `ready: false`, а непройденные проверки равны `false`. `vlm_quant` — `8bit`, `6bit` или `4bit`. Его выбирает авто-квантизация по свободной памяти после прогрева.

Правило для `stub` и `replay` (S1 закрепляет в P0-S1-04, оно нужно для G0): при `CG_PIPELINE_MODE=stub` или `replay` проверки `models_loaded` и `warmup_done` считаются пройденными, а при `CG_VLM_ENABLED=false` — ещё `worker_vlm` и VLM-часть `models_loaded`. Так `/ready` полезен в stub-профиле G0, в dev и в резервном Linux-образе (только правила). Детали реализации проверить с S0 и S6.

`/ready` не блокирует приём задач: `POST /api/v1/analyze` работает и при `ready: false`. Задача встанет в очередь и будет обработана, когда воркеры прогреются.

---

## 6. UI API (`/api/ui`)

Отдельная поверхность для веб-интерфейса. Схему жюри можно не трогать, пока UI развивается. Использует тот же сервис приёма (`ingest_upload`), но с принципалом `ui` и приоритетом 1. Отдаёт URL картинок, а не base64.

### 6.1. Доступ и лимиты

- **Ключ не нужен** (FAQ 27). `<video>`, `<img>` и `EventSource` не умеют слать заголовки, а ключ в бандле был бы публичным.
- **Лимиты по IP** (token bucket в процессе API, `api/ratelimit.py`). Сверх лимита — `429` с заголовком `Retry-After` (секунды). Настройки — `CG_UI_RATE_*`, точные имена в `core/settings.py`. Лимиты проверяются до чтения тела. Два профиля:

  | Профиль | Загрузок на IP за 10 мин | Активных задач на IP | Общая очередь UI | Когда |
  |---|---|---|---|---|
  | обычный (по умолчанию) | 5 | 2 | 10 | разработка, демо |
  | окно жюри | 30 | 5 | 20 | от заморозки до конца окна жюри ([jury_mode.md](jury_mode.md), чек-лист заморозки; [runbook_mac.md](runbook_mac.md), раздел 3.6) |

  Жюри может загружать видео через UI по ссылке команды (FAQ 13), и видео может быть 5 и больше (FAQ 12). Несколько членов жюри могут сидеть за одним NAT площадки. Поэтому в окне жюри действует ослабленный профиль. UI-задачи идут по тем же рубежам дедлайна от t0, что и jury-задачи (раздел 3.7), только с приоритетом 1.
- **IP клиента** берётся из `CF-Connecting-IP`, и только если соединение пришло с `127.0.0.1` (cloudflared). Иначе используется адрес соединения. Через Tailscale Funnel этого заголовка нет, и все UI-клиенты попадут в один бакет: в окне жюри этот бакет ограничен профилем «окно жюри». Передаёт ли Funnel `X-Forwarded-For` — проверить.
- **Код доступа** (по умолчанию выключен). Если задан `CG_UI_ACCESS_CODE`, `POST /api/ui/session` с телом `{"code": "..."}` ставит cookie `HttpOnly; SameSite=Strict`, а остальные `/api/ui/*` без cookie отвечают 401. Без `CG_UI_ACCESS_CODE` маршрут отвечает 404. Детали (имя cookie, код ответа) — предложение S1.
- **Jury-задачи через `/api/ui/*` не видны** — `404`. ТЗ §10 запрещает публиковать тестовые видео, а UI-маршруты открыты без ключа. Это предложение S1, подтвердить у S0.

### 6.2. `POST /api/ui/jobs`

- Multipart, поле `file`. Клиент заранее проверяет `.mov`/`.mp4` и размер ≤ 100 МБ и показывает ошибки по-русски. Сервер проверяет по содержимому, как в jury API.
- Ответ `200 {"job_id": "<32 hex>"}` (та же модель `AnalyzeAccepted`).
- Ошибки: те же случаи, что в разделе 3.4, но тело — `UiHttpError` (`contracts/ui.py`): `{"code", "message_ru", "retry_after_sec"}`. `message_ru` готов к показу пользователю. Так отвечает любой маршрут `/api/ui/*`; API жюри сохраняет свои тела `{"detail"}`.

  | HTTP | `code` | Когда |
  |---|---|---|
  | 413 | `FILE_TOO_LARGE` | `Content-Length` или фактический поток больше `CG_MAX_UPLOAD_MB` |
  | 415 | `UNSUPPORTED_MEDIA` | PyAV не открывает контейнер или в нём нет видеодорожки |
  | 422 | `INVALID_REQUEST` | нет поля `file`, лишние части, тело не multipart |
  | 422 | `UPLOAD_TIMEOUT` | загрузка не уложилась в `CG_UPLOAD_TIMEOUT_SEC` |
  | 429 | `RATE_LIMITED` | лимит профиля; `retry_after_sec` равен заголовку `Retry-After` |
  | 507 | `INSUFFICIENT_STORAGE` | на диске меньше `CG_MIN_FREE_DISK_GB` или закончилось место при записи |
  | 400 | `UPLOAD_ABORTED` | клиент оборвал загрузку (`xhr.abort()`, закрытая вкладка) |
  | 404, 401, 500 | `NOT_FOUND`, `UNAUTHORIZED`, `INTERNAL` | как у остальных маршрутов UI |

- Отмена загрузки: клиент вызывает `xhr.abort()`. Сервер видит обрыв потока, удаляет `input.part` и папку задания, задание не создаётся, попытка не расходует лимит загрузок по IP. Если обрыв пришёлся после `COMMIT`, задание уже принято и будет обработано; клиент о нём не узнает, а сервер удалит его по сроку хранения. Отмены принятого задания в v1 нет (FAQ 27: истории и управления заданиями не нужно).
- После `COMMIT` API в потоке делает remux в `preview.mp4` с `+faststart`, без перекодирования (≤ 0,3 с, оценка). HEVC или повёрнутое видео помечаются `needs_transcode`, и тогда перцепционный воркер перекодирует его через `h264_videotoolbox`.
- Загрузка в браузере идёт через XHR (у `fetch()` нет прогресса отправки).

### 6.3. `GET /api/ui/jobs/{job_id}` → `UiJobView`

- Около 0,5 КБ, пока задача идёт. Фронтенд опрашивает раз в 1 с, пока статус `queued` или `running`.
- `status` здесь внутренний: `queued`, `running`, `done`, `failed`. Сведение к `processing` делается только для жюри.
- Как только записан предварительный результат (`provisional_at`), в ответе появляются нарушения с `provisional: true`. Это бывает и при `status=running`, и при `status=queued` (`stage=vlm`: задача ждёт VLM-воркер). UI показывает их с плашкой «Предварительно». Так результат появляется постепенно без SSE.
- Когда `done`, ответ собирается из закэшированного `ui_result.json` (LRU по mtime в процессе API).
- `Cache-Control: no-store`, пока задача идёт; `private, max-age=60`, когда `done`.
- Неизвестный `job_id` — 404.

Пример во время VLM-этапа (значения условные; формат `created_at` задаёт `contracts/ui.py`):

```json
{
  "job_id": "9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b",
  "status": "running",
  "created_at": "2026-10-17T09:12:03Z",
  "elapsed_sec": 12.4,
  "queue_position": 0,
  "eta_sec": 18,
  "perception_level": "full",
  "result_level": "rules",
  "stages": [
    {"code": "queued", "status": "done", "progress": 1.0, "elapsed_sec": 0.2, "note_ru": null},
    {"code": "probe", "status": "done", "progress": 1.0, "elapsed_sec": 0.1, "note_ru": null},
    {"code": "perception", "status": "done", "progress": 1.0, "elapsed_sec": 6.8, "note_ru": null},
    {"code": "rules", "status": "done", "progress": 1.0, "elapsed_sec": 0.2, "note_ru": null},
    {"code": "evidence", "status": "done", "progress": 1.0, "elapsed_sec": 0.4, "note_ru": null},
    {"code": "vlm", "status": "running", "progress": 0.5, "elapsed_sec": 4.1, "note_ru": null},
    {"code": "done", "status": "pending", "progress": 0.0, "elapsed_sec": null, "note_ru": null}
  ],
  "degradations": [],
  "provisional": true,
  "video": {"url": "/api/ui/jobs/9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b/video.mp4", "duration_sec": 21.3, "width": 464, "height": 848},
  "verdict": "violation",
  "violations": ["<UiViolation, provisional: true>"],
  "signals": [],
  "timings": null,
  "error": null
}
```

Элемент `violations` в готовом результате (числа условные; `title_ru`, `description_ru` и `act_title_ru` — из каталога и `configs/legal/acts.yaml`; `quote_ru` дословно совпадает с `analysis/normative/PKO_NBRK_231_2019_V1900019680.txt`):

```json
{
  "id": "v0",
  "code": "SAFE_KEY_LEFT",
  "title_ru": "Ключ оставлен в замке сейфа",
  "timecode_sec": 14.0,
  "start_sec": 13.2,
  "end_sec": 21.3,
  "confidence": 0.87,
  "vlm_status": "confirmed",
  "provisional": false,
  "description_ru": "В 14,0 с кассир покидает кассу; в замке сейфа остаётся ключ. Может свидетельствовать о несоблюдении п. 80 Правил осуществления кассовых операций…, утв. постановлением Правления НБРК от 29.11.2019 № 231.",
  "legal": [
    {
      "act_code": "PKO",
      "act_title_ru": "Правила осуществления кассовых операций и операций по инкассации банкнот, монет и ценностей в банках второго уровня, филиалах банков-нерезидентов Республики Казахстан, Национальном операторе почты и юридических лицах, исключительной деятельностью которых является инкассация банкнот, монет и ценностей",
      "clause": "п. 80",
      "quote_ru": "Порядок хранения, выдачи и передачи ценностей, печатей, ключей и кодов от электронных и механических замков кладовой и (или) сейфовой комнаты, пломбираторов, клише, именных штампов и ключей от специальных устройств, находящихся на рабочем месте кассового работника, предназначенных для хранения наличных денег (металлический шкаф, сейф, тележка, ящики стола), определяется внутренними документами банка, обеспечивающими исключение их несанкционированного использования.",
      "url": "https://adilet.zan.kz/rus/docs/V1900019680",
      "strength": "indirect"
    }
  ],
  "evidence": [
    {"timestamp_sec": 14.0, "url": "/api/ui/jobs/9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b/frames/0_event.jpg", "kind": "event", "width": 464, "height": 848},
    {"timestamp_sec": 14.0, "url": "/api/ui/jobs/9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b/frames/0_zoom.jpg", "kind": "zoom", "width": 480, "height": 480},
    {"timestamp_sec": 12.5, "url": "/api/ui/jobs/9c4e0d6b2f7a4e1c8b3d5a6f7e8d9c0b/frames/0_context.jpg", "kind": "context", "width": 464, "height": 848}
  ]
}
```

Поля и перечисления — по `contracts/ui.py`:

| Поле | Значения |
|---|---|
| `stages[].code` | `queued`, `probe`, `perception`, `rules`, `evidence`, `vlm`, `done` |
| `stages[].status` | `pending`, `running`, `done`, `skipped`, `failed` |
| `perception_level` | `full`, `fast`, `min` |
| `result_level` | `full`, `partial`, `rules` или `null` |
| `violations[].vlm_status` | `confirmed`, `downweighted`, `unverified`, `skipped`. Кандидат, понижённый VLM, остаётся виден с пометкой «снижено VLM» |
| `legal[].strength` | `direct`, `indirect`, `none` |
| `degradations[]` | `{stage, reason_ru}` |
| `error` | `{code, message_ru}` или `null`; `code` ∈ `UNSUPPORTED_VIDEO`, `PIPELINE_CRASH`, `INTERNAL` |
| `signals[]` | `UiSignal{id, code, title_ru, t_sec, description_ru, evidence}` — 13 кодов `SIG_*`, на вердикт не влияют |

### 6.4. `GET /api/ui/jobs/{job_id}/video.mp4`

- Совместимый с браузером H.264 с `faststart` (`preview.mp4`). Создаётся только для UI-задач.
- `FileResponse` с HTTP Range (`206`) и `ETag`: Safari без Range не играет.
- `Cache-Control: private, max-age=3600`.
- `404`, пока превью не готово. До этого `UiJobView.video` равно `null`, а браузер играет локальный файл через `URL.createObjectURL`.

### 6.5. `GET /api/ui/jobs/{job_id}/frames/{name}`

- Кадр-доказательство JPEG из `var/jobs/{id}/evidence/`.
- `name` должен совпасть с `^\d{1,2}_(event|zoom|context)\.jpg$`, иначе `404`. Итоговый путь дополнительно проверяется через `is_relative_to(job_dir / "evidence")`.
- `Cache-Control: private, max-age=31536000, immutable`. `private` нужен, чтобы Cloudflare не кэшировал кадры с камер.

### 6.6. `GET /api/ui/catalog` → `UiCatalog`

- Все типы нарушений, сигналы, акты и нормы: цитата, пункт, ссылка на adilet. Нужен для карточек и страницы «Типы нарушений и нормы».
- `ETag` = `config_hash`. Запрос с `If-None-Match` получает `304`.
- Предложение (PR к S0 в `contracts/ui.py`): поле `retention_hours` — текущее `CG_RETENTION_HOURS`, чтобы тексты UI о сроке хранения не были зашиты (`0` — очистка выключена, окно жюри; [ui.md](ui.md), раздел 16, вопрос 13).
- `Cache-Control` в архитектуре не задан. Предложение S1: `no-cache` (браузер всегда ревалидирует по `ETag`).

### 6.7. `GET /api/ui/jobs/{job_id}/events` — зарезервирован, не в v1

SSE — опциональное улучшение P3 за флагом `CG_UI_SSE=1` и только за named tunnel: quick tunnel Cloudflare SSE не поддерживает. Протокол: сначала `event: snapshot` с `UiJobView`, затем события `stage`, `provisional`, `done|failed`, пинги раз в 15 с, `retry: 3000`. Клиент откатывается на опрос раз в 1 с, если snapshot не пришёл за 4 с или было 2 `onerror`. В v1 маршрута нет: AGENTS.md запрещает SSE и WebSockets.

---

## 6.9. Эталонные ответы UI (K0)

Общий договор frontend и backend — файлы `tests/fixtures/ui/*.json`. Frontend верстает по ним, backend отдаёт ровно такую форму. Файлы генерирует `uv run python scripts/gen_ui_examples.py` из контракта `UiJobView` и настоящего каталога (названия типов и нормы не придумываются), контрактный тест `tests/contract/test_ui_examples.py` проверяет их.

| Файл | Состояние |
|---|---|
| `01_queued.json` | задание ожидает в очереди (`queue_position`, `eta_sec`) |
| `02_running.json` | задание обрабатывается: этапы, прогресс, ссылка на видео |
| `03_done_clean.json` | завершено без нарушений; сигнал для проверяющего не влияет на вердикт |
| `04_done_violations.json` | завершено, три события с временем, описанием, кадрами и нормами |
| `05_done_partial.json` | обработано частично: `result_level=rules`, деградации, основание не установлено (`legal_note_ru`) |
| `06_failed.json` | ошибка с понятным текстом `error.message_ru` |
| `07_stub_demo.json` | демонстрационный режим `pipeline_mode=stub`: интерфейс обязан показать это явно |

Обязательные поля для интерфейса: `status`, `pipeline_mode`, `stages` (этап, если известен), `video` (метаданные и URL), `verdict`, `violations[]` (`title_ru`, `timecode_sec`, `description_ru`, `evidence[].url`, `legal[]`, `legal_note_ru`), `degradations`, `result_level`, `error`. Ключ jury API во frontend не встраивается.

## 7. Статика и документация API

| Путь | Что отдаётся | `Cache-Control` |
|---|---|---|
| `/` | `web/dist/index.html` (`lang=ru`, `<meta name=robots content=noindex>`) | `no-cache` |
| `/?job=<id>`, `/?view=types` | тот же `index.html`; вид выбирается по query, SPA-fallback не нужен | `no-cache` |
| `/assets/*` | хэшированные ассеты Vite (около 80 КБ gzip JS, оценка) | `public, max-age=31536000, immutable` |
| `/robots.txt` | `Disallow: /` | — |
| `/favicon.svg` | иконка | — |
| `/docs` | Swagger UI с кнопкой Authorize для `X-API-Key` | — |
| `/openapi.json` | схема OpenAPI; источник типов для web | — |

- Маршруты API регистрируются раньше статики. Mount `StaticFiles` только для `web/dist/assets`.
- Если `web/dist` нет (dev на Windows без сборки), `/` отвечает 404 JSON. UI в dev отдаёт Vite на `:5173` с прокси `/api` на порт worktree (`scripts/port.py`, 8100–8199).
- Swagger UI грузит JS с CDN. В закрытом контуре банка ставим `CG_DOCS_ENABLED=false` или раздаём ассеты Swagger локально. `/openapi.json` остаётся доступен. `/redoc` выключен (`redoc_url=None`).

### Сводка заголовков кэша

| Маршрут | `Cache-Control` |
|---|---|
| `/api/v1/*` | `no-store` |
| `/api/ui/jobs/{id}` (идёт) | `no-store` |
| `/api/ui/jobs/{id}` (`done`) | `private, max-age=60` |
| `/api/ui/jobs/{id}/video.mp4` | `private, max-age=3600` + `ETag`, Range |
| `/api/ui/jobs/{id}/frames/{name}` | `private, max-age=31536000, immutable` |
| `/api/ui/catalog` | `ETag` = `config_hash`, `304`; `no-cache` (предложение) |
| `/health`, `/ready` | `no-store` (предложение) |
| `/` | `no-cache` |
| `/assets/*` | `public, max-age=31536000, immutable` |

На стороне Cloudflare кэширование `/api` выключено, Bot Fight Mode и Under Attack выключены ([runbook_mac.md](runbook_mac.md)).

---

## 8. Что никогда не отдаётся

- **Входное видео** (`input.mov`) — ни по одному маршруту. Для jury-задач оно удаляется сразу после перцепции. `preview.mp4` существует только у UI-задач.
- **Файлы `var/`, `data/`, `models/`, `runs/`** — нет маршрутов произвольных файлов. Медиа отдаются только через проверенные маршруты раздела 6.
- **Промежуточные файлы задачи**: `meta.json`, `frames/` (frame store), `trace.json.gz`, `candidates.json`, `analysis.json`. Наружу идут только через модели представления.
- **Jury-задачи через `/api/ui/*`** — 404 (раздел 6.1).
- **В jury JSON** нет сигналов, отдельных полей с нормами, `confidence`, `vlm_status`, `degradations`, `result_level`, `provisional`, `pipeline_version`, `config_hash`, заметок VLM. Только ключи ТЗ §10.
- **base64** — никогда в UI-ответах. Только URL на JPEG.
- **API-ключи** — ни в ответах, ни в логах (только имя принципала), ни в бандле фронтенда.
- **Подробности о системе** без team-ключа — только булевы флаги `/ready`. Память, версии моделей, очередь и список задач — только `admin/status`.
- **Трейсбеки** — никогда. Глобальный обработчик отвечает `500 {"detail": "internal error"}`, трейс уходит в `var/logs/api.jsonl`.
- **CORS-заголовки** не выставляются: всё на одном origin.

Хранение: каталоги задач удаляются через `CG_RETENTION_HOURS` (24 ч; в окне жюри очистка выключена), а после окна жюри очищается весь `var/`. Jury- и UI-видео никогда не попадают в `var/signals`, `data/`, обучение и оценку.

---

## 9. OpenAPI → типы TypeScript

Цепочка, без генерируемого runtime-кода:

```text
contracts/*.py (Pydantic v2, frozen, extra='forbid', Literal-перечисления)
  └─ FastAPI create_app() (separate_input_output_schemas=False)
       └─ python -m cashguard.contracts.export        [S0, `just schemas`]
            ├─ src/cashguard/contracts/schemas/generated/*.schema.json   [GEN]
            └─ web/src/shared/api/openapi.json                           [GEN, в git]
                 └─ pnpm gen:api = openapi-typescript src/shared/api/openapi.json -o src/shared/api/schema.d.ts   [GEN, в git]
                      └─ web/src/shared/api/client.ts (openapi-fetch) + queries.ts (хуки TanStack Query, вручную)
```

Правила:

- Модели `jury.py` (`AnalyzeAccepted`, `JuryEvidenceFrame`, `JuryViolation`, `JuryJobResponse`) экспортируются без `schema_version` и без других служебных полей: у них ровно ключи ТЗ §10. У `UiJobView` поля `schema_version` тоже нет. Оно есть только у сохраняемых артефактов (`PerceptionTrace`, `AnalysisResult`, базовые линии eval).
- `export` импортирует приложение **без ML** и без запуска lifespan, поэтому работает на Windows и в CI. Если импорт `cashguard.api.main` тянет torch, это ловит import-linter.
- `separate_input_output_schemas=False`, иначе Pydantic v2 даёт пары схем `-Input`/`-Output`.
- `Literal` для `status`, `verdict`, `stage`, `kind` и других перечислений. В TS они становятся объединениями строк.
- Обработчики загрузки принимают сырой `Request`, поэтому тело multipart описывается вручную через `openapi_extra`:

```python
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
```

- Done-ответ jury отдаётся `FileResponse`, поэтому модель ответа документируется через `responses={200: {"model": JuryJobResponse}}`. Ошибки — через модель `{detail: str}`. Её пока нет в `contracts`: предложить S0 добавить `ErrorDetail` в `contracts/health.py` или `contracts/jury.py`.
- Алиасы со слешем имеют `include_in_schema=False`.
- Схема безопасности `APIKeyHeader` (`X-API-Key`) даёт кнопку Authorize в `/docs`.
- `jury_job.schema.json` **не генерируется**. Это рукописный оракул (раздел 10). Сгенерированная схема `JuryJobResponse` лежит рядом в `generated/` и сверяется с оракулом тестом на общем наборе документов.
- TypeScript закреплён на **5.9.3**: у openapi-typescript 7.13.0 peer `typescript ^5`. TS 6 и 7 запрещены ([AGENTS.md](../AGENTS.md)).
- Проверка расхождений в `just check` и в хуке pre-commit: `python -m cashguard.contracts.export --check`, затем `pnpm gen:api` и `git diff --exit-code`. Сгенерированные файлы руками не правятся.
- Изменение контракта — отдельный маленький PR от S0 вместе с перегенерированными файлами, мержится первым. S1 и S2 потом делают rebase.

Использование в web:

```ts
// web/src/shared/api/client.ts
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "" }); // тот же origin
export type UiJobView = components["schemas"]["UiJobView"];
export type AnalyzeAccepted = components["schemas"]["AnalyzeAccepted"];

// web/src/shared/api/queries.ts (фрагмент)
const { data, error } = await api.GET("/api/ui/jobs/{job_id}", {
  params: { path: { job_id } },
});
```

Загрузка (`POST /api/ui/jobs`) идёт через XHR с `FormData` (поле `file`). Ответ типизируется как `AnalyzeAccepted`.

---

## 10. Контрактные тесты

### 10.1. Как тест проверяет замороженную схему и пример из ТЗ

[`tests/fixtures/jury_example.json`](../tests/fixtures/jury_example.json) — пример из ТЗ §10 со всеми его значениями (`"job_id": "3f2a9c1e"`, `"type": "Название типа нарушения"`, `44.2`). Обрезанный `"/9j/4AAQ..."` заменён настоящим JPEG 1×1 в base64 (629 байт). Поэтому схема требует от `job_id` только непустую строку: формат 32 hex проверяет `tests/api`, а не схема.

Ядро `tests/contract/test_jury_schema_frozen.py`:

```python
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "src/cashguard/contracts/schemas/jury_job.schema.json"
FIXTURE = ROOT / "tests/fixtures/jury_example.json"
FROZEN_SHA256 = "908120fee6d4d4654a69f1109d052dc94ee7b3d0c6f1ac5797f4d8f86a1ff79f"  # с minLength у description, 17.10.2026


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_schema_is_valid_2020_12():
    Draft202012Validator.check_schema(_load(SCHEMA))


def test_schema_is_frozen():
    canon = json.dumps(_load(SCHEMA), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(canon.encode("utf-8")).hexdigest() == FROZEN_SHA256, (
        "jury_job.schema.json изменён: нужно одобрение человека (S0), затем обновить FROZEN_SHA256"
    )


def test_tz_example_is_valid():
    doc = _load(FIXTURE)
    Draft202012Validator(_load(SCHEMA)).validate(doc)
    raw = base64.b64decode(doc["violations"][0]["evidence_frames"][0]["image_base64"], validate=True)
    assert raw[:3] == b"\xff\xd8\xff" and raw[-2:] == b"\xff\xd9"


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(signals=[]),                                   # лишний ключ
    lambda d: d.update(verdict=None),                                 # done без вердикта
    lambda d: d.update(violations=[]),                                # violation без нарушений
    lambda d: d.update(status="processing", verdict=None),            # processing с нарушениями
    lambda d: d["violations"][0].update(evidence_frames=[]),          # 0 кадров
    lambda d: d["violations"][0]["evidence_frames"][0].update(
        image_base64="data:image/jpeg;base64,/9j/"),                  # data:-префикс
])
def test_mutations_are_rejected(mutate):
    doc = copy.deepcopy(_load(FIXTURE))
    mutate(doc)
    assert list(Draft202012Validator(_load(SCHEMA)).iter_errors(doc))
```

Хэш считается по каноническому JSON, а не по байтам файла. Поэтому он не зависит от форматирования и концов строк (`.gitattributes` ставит LF). Любое смысловое изменение схемы, даже `$comment`, требует сознательно обновить `FROZEN_SHA256`.

**Проверено 17.10.2026** на dev-PC (`python -I`, jsonschema 4.26.0): схема проходит мета-схему draft 2020-12; фикстура валидна; кадр декодируется в JPEG; 5 допустимых документов (`processing`, `failed`, `done` без нарушений, 3 кадра, целый таймкод) принимаются; 24 недопустимых отклоняются. Среди отклонённых: лишний ключ на каждом из трёх уровней, нет `verdict` или `violations`, `status=queued`, `done` с `verdict=null`, `violation` с пустым списком, `no_violation` с нарушениями, `processing` и `failed` с вердиктом или нарушениями, `verdict=insufficient_data`, 0 и 4 кадра, отрицательные таймкоды, таймкод-строка, пустой `type`, `data:`-префикс, PNG вместо JPEG, пустая картинка, пустой и числовой `job_id`. jsonschema 4.26.0 — та же версия, что в тестовом стеке.

### 10.2. Список тестов

Имена файлов — предлагаемые, кроме `test_auth_before_body.py` (зафиксировано в архитектуре). Все тесты API работают без моделей: `CG_PIPELINE_MODE=stub`, httpx `ASGITransport`, временный `var/`.

| Файл | Владелец | Что проверяет |
|---|---|---|
| `tests/contract/test_jury_schema_frozen.py` | S0 | мета-схема 2020-12; хэш заморозки; пример из ТЗ валиден; JPEG декодируется; набор мутаций отклоняется |
| `tests/contract/test_jury_model_matches_frozen.py` | S0 | Pydantic `JuryJobResponse` читает пример; `model_dump_json()` валиден по оракулу; модель и оракул одинаково судят общий набор документов |
| `tests/contract/test_openapi_drift.py` | S0 | `contracts.export --check`: `openapi.json` и `generated/*` актуальны |
| `tests/contract/test_legal_quotes_exist.py`, `test_catalog_complete.py` | S0 | цитаты дословно есть в `analysis/normative`; у каждого кода есть правило, норма и шаблон |
| `tests/unit/render/test_render_jury.py` | S1 | инварианты вне схемы (раздел 3.3); безопасный рендер на сломанном входе (NaN, 4 кадра, `t > duration`, нет кадров) даёт валидный файл |
| `tests/api/test_auth_before_body.py` | S1 | 401 без ключа и с неверным ключом на POST и GET; итератор тела **не тронут**; проверяются все ключи без раннего выхода |
| `tests/api/test_upload_limits.py` | S1 | 413 по `Content-Length` и в потоке без него (chunked), `.part` удалён; 415 (`.txt`, битый контейнер, чужой атом без читаемого контейнера); 200 для синтетического `.mov`, у которого первый атом `uuid`; 422 (нет поля, другое имя поля); 507 (подменённый `disk_usage`); любой `Content-Type` части; имя файла игнорируется |
| `tests/api/test_trailing_slash.py` | S1 | оба алиаса отвечают как основные, без 307; алиасов нет в OpenAPI |
| `tests/api/test_lifecycle.py` | S1 | POST 200 с 32 hex → точная форма `processing` → `done`, валидный по оракулу; `job_id` равен пути; `Cache-Control: no-store`; `Content-Type: application/json` |
| `tests/api/test_job_ids.py` | S1 | кривой и неизвестный `job_id` — 404 JSON; без ключа сначала 401 |
| `tests/api/test_failed_shape.py` | S1 | файл, не прошедший глубокий probe, даёт точную форму `failed` |
| `tests/api/test_lazy_finalize.py` | S1 | sweeper выключен, время за `finalize_at`, предварительный результат есть → GET отдаёт `done`; повторная и параллельная финализация идемпотентны |
| `tests/api/test_dedupe.py` | S1 | тот же файл дважды в работе → `linked_to`, оба `done`, разные `job_id`, подстановка `job_id` в `jury.json`; попадание в кэш после `full` → мгновенный `done`; деградированные не кэшируются |
| `tests/api/test_concurrent.py` | S1 | 5 параллельных POST → 5 разных `job_id`, все `done`, ни одного 5xx |
| `tests/api/test_admin.py` | S1 | без ключа 401, jury-ключ 403, team-ключ 200 |
| `tests/api/test_health.py` | S1 | `/health` 200 без обращения к БД; `/ready` 503 с `false` у непройденных проверок; ключ не нужен |
| `tests/api/test_ui_routes.py` | S1 | 429 и `Retry-After`; jury-ключ не ограничивается; `frames/{name}` с плохим именем или `..` → 404; Range у `video.mp4` → 206; `catalog` с `If-None-Match` → 304; jury-задача через `/api/ui` → 404 |
| `tests/api/test_errors_json.py` | S1 | неизвестный маршрут 404 JSON; 405 JSON; у всех ошибок есть `detail`; нет HTML |
| `tests/api/test_schemathesis.py` | S1 | фаззинг приложения в процессе: нет 5xx; каждый маршрут `/api/v1` без ключа даёт 401 |
| import-linter + `tests/api/test_no_ml_imports.py` | S0/S1 | `api`, `jobs`, `core`, `contracts` не импортируют torch, ultralytics, coremltools, mlx, mlx_vlm, cv2 |
| `tests/jobs/`, `tests/workers/` | S1 | гонка claim, истечение lease, `kill -9` посреди задачи → всё равно `done`, падение VLM → уходит предварительный результат |

### 10.3. Ворота вне pytest

| Ворота | Что делаем | Критерий |
|---|---|---|
| G0 (P0) | curl из ТЗ с телефона через мобильную сеть на публичный URL; запрос без ключа; одна загрузка через UI | ответ валиден по оракулу; без ключа 401; UI-задача дошла до `done` |
| `just jury-sim URL KEY 5` | 5 параллельных загрузок через публичный URL с телефонной точки доступа, опрос раз в 3 с | все `done` ≤ 240 с от своего POST; ни одного 5xx; все ответы валидны по оракулу |
| `just jury-sim URL KEY 10` | 10 параллельных, из них ≥ 3 ролика по 145 с | каждая задача `done` ≤ 300 с от своего POST (цель ≤ 270 с); лишние — `result_level=rules` или `perception_min`; 0 × 5xx, 0 × `failed` |
| загрузки через UI | с одного телефона 6 загрузок за 10 минут, по 3 одновременно, профиль «окно жюри» | ни одного 429; все `done` ≤ 300 с |
| повтор того же файла | тот же файл дважды | второй ответ мгновенный (кэш) |
| релизные ворота | сеть выключена → `just services-restart` → `/ready` 200 (прогрев всех моделей офлайн) → curl из ТЗ на 3 видео на `127.0.0.1` | все `done`: ни старт процессов, ни обработка не ходят в сеть |

---

## 11. Конфигурация, относящаяся к API

| Переменная (`CG_`) | Значение по умолчанию | Смысл |
|---|---|---|
| `CG_API_KEYS` | — | `jury:<tok>,team:<tok>`, `SecretStr` |
| `CG_HOST`, `CG_PORT` | `127.0.0.1`, `8000` (dev: порт worktree 8100–8199) | адрес uvicorn |
| `CG_MAX_UPLOAD_MB` | 100 | лимит тела загрузки, МБ = 10^6 байт |
| `CG_MIN_FREE_DISK_GB` | 2 | ниже — 507 |
| `CG_MIN_FREE_MEM_GB` | 3,0 | ворота VLM и проверка `/ready`. Совпадает с требованием «≥ 3 ГБ свободно на пике»: проверка идёт до VLM-этапа, когда рабочий набор VLM ещё не вырос, поэтому при пройденном гейте выпуска (`just perf` в SERVE, [architecture.md](architecture.md), раздел 8) штатная нагрузка его не задевает. Если перед VLM свободно меньше 3 ГБ, на пике было бы ещё меньше — задача уходит по правилам (`vlm_memory`) |
| `CG_UI_ACCESS_CODE` | не задан | код доступа к UI |
| `CG_UI_RATE_*` | 5 / 10 мин, 2 активных, очередь 10; в окне жюри — 30 / 10 мин, 5 активных, очередь 20 | лимиты UI по IP (раздел 6.1) |
| `CG_DOCS_ENABLED` | — | `/docs` (Swagger) |
| `CG_RETENTION_HOURS` | 24 | очистка задач; `0` — очистка выключена (режим жюри) |
| `CG_PIPELINE_MODE` | `stub`, `replay` или `full` | в dev и тестах — `stub` |
| `CG_VLM_ENABLED` | — | VLM-этап |
| `CG_UI_SSE` | выключен | SSE, P3, не в v1 |

Рубежи дедлайнов — в `configs/deadlines.yaml`, не в переменных окружения.

---

## 12. Открытые вопросы (grill-me)

В скобках — что делаем по умолчанию, пока нет ответа.

1. Формат `type` у жюри: русские названия или коды? (русский `title_ru` из каталога)
2. Правовая фраза внутри `description`? (да)
3. Принимает ли парсер жюри `verdict: null` и `violations: []` при `processing`? В ТЗ показан только `done`. (да, `null` и `[]`)
4. 200 или 202 на POST? (200)
5. Есть ли домен для named tunnel? (купить дешёвый; иначе основной URL — Tailscale Funnel)
6. Нужен ли код доступа к UI? (нет, лимиты по IP)
7. Что отвечать, если к `finalize_at` нет даже предварительного результата? Закрыт: рубеж `perception_hard_stop` (+235 с) и деградация `perception_truncated` (раздел 3.7, [ADR-0005](adr/0005-deadline-tiers-provisional-degradation.md)).
8. Скрывать ли jury-задачи из `/api/ui/*`? (да, 404; предложение S1)
9. По возможности уточнить у организаторов семантику парсера: сравнение `type`, отношение к `null`, к коду 200.
10. ~~Добавить `"minLength": 1` в `$defs.violation.properties.description`?~~ **Решено 17.10.2026: добавлено.** ТЗ §5 требует пояснение, пустая строка теперь не проходит схему (проверено jsonschema 4.26.0). В P0 при фиксации схемы: записать `FROZEN_SHA256` (раздел 10.1) и добавить мутацию `lambda d: d['violations'][0].update(description='')` в `test_mutations_are_rejected`.

---

## 13. Связанные документы

- [README.md](../README.md) — адреса, ключ и короткая инструкция для жюри (ТЗ §8).
- [AGENTS.md](../AGENTS.md) — правила для агентов, закреплённые версии и запреты.
- [architecture.md](architecture.md) — процессы, поток данных, SQLite, супервизор.
- [ui.md](ui.md) — экраны и поведение веб-интерфейса поверх `/api/ui`.
- [jury_mode.md](jury_mode.md), [runbook_mac.md](runbook_mac.md) — подготовка Mac к окну жюри, туннель, ключи.
- [eval.md](eval.md) — `jury-sim`, метрики, ворота.
- [legal_sources.md](legal_sources.md) и `docs/violation_types.md` (генерируется `scripts/render_docs.py`, появится в P3) — нормы и типы, которые попадают в `type` и `description`.
- [limitations.md](limitations.md) — ограничения и известные ошибки.
- [adr/](adr/) — ADR-0004 (загрузка с проверкой ключа до тела), ADR-0005 (дедлайны и деградация), ADR-0006 (замороженный jury-контракт), ADR-0007 (опрос вместо SSE), ADR-0008 (отдельный `/api/ui`), ADR-0013 (публичный вход).
- [`jury_job.schema.json`](../src/cashguard/contracts/schemas/jury_job.schema.json), [`jury_example.json`](../tests/fixtures/jury_example.json) — оракул и пример из ТЗ.
- [case_analysis.md](../analysis/case_analysis.md) — §1 (критерии), §7 (проверенные нормы РК).

## Интеграционная ветка: K1 (17.10.2026)

`codex/fullstack-integration`: обработчики `/api/ui/*` возвращают согласованный
`UiHttpError` для ошибок загрузки, авторизации, отсутствующих файлов, лимитов и
внутренних сбоев. `Retry-After` совпадает с `retry_after_sec`; сообщения на русском,
тексты исключений/пути не передаются. API жюри сохраняет прежнее тело `detail`.
Разрыв отправки — `UPLOAD_ABORTED`, срок отправки — `UPLOAD_TIMEOUT`.
Проверены 12 тестов реальных обработчиков ошибок и сквозная загрузка через браузер.

### K2 — медиа в интеграционной ветке

UI-превью H.264 без поворота создаётся быстрым remux. HEVC и повёрнутые записи
перекодируются в worker: H.264/yuv420p, сторона до 1280 px, нормализация поворота,
таймкоды из PTS. Бюджет перекодирования — до 30 секунд и не позже finalize−5 секунд.
При ошибке превью отсутствует (`video: null`), исходный файл не выдаётся вместо него.
Размеры и длительность в UI относятся к готовому превью. Range 206 поддерживается.
Повреждённые preview/JPEG возвращают 404 с `UiHttpError`.

Для UI-загрузок, которым требуется перекодирование, дедупликация временно отключена:
их превью принадлежит конкретному заданию. Иначе ссылка на уже завершённую jury-задачу
могла бы навсегда оставить интерфейс без видео. Jury и обычный H.264 сохраняют дедуп.
Это изменение поведения не меняет JSON-контракт.
