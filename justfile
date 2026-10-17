# CashGuard: общие команды. Запуск всегда так: `uv run just <recipe>`.
# Windows: рецепты идут через PowerShell. [macos] — только на Mac.
# Рецепты-заглушки печатают карточку roadmap, в которой появится реализация.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
set dotenv-load := false

# Список рецептов
default:
    @just --list --unsorted

# ---------------------------------------------------------------- окружение

# Окружение разработки: Python (+ web, когда появится web/package.json)
setup:
    uv sync --locked
    @echo "web: pnpm -C web install --frozen-lockfile (после P0-S2-01)"

# ---------------------------------------------------------------- качество

# Шлюз мержа: линтеры, типы, границы импортов, дрейф схем, быстрые тесты
check:
    ruff check .
    ruff format --check .
    basedpyright
    lint-imports
    python -m cashguard.contracts.export --check
    pytest -q -m "not mac and not slow"
    @echo "web: biome ci + tsc -b подключит S2 (P0-S2-01)"

# Все pytest-тесты (аргументы пробрасываются)
test *args:
    pytest {{args}}

# Автоформат Python
fmt:
    ruff check --fix .
    ruff format .

# JSON Schema контрактов + web/src/shared/api/openapi.json
schemas:
    python -m cashguard.contracts.export

# ---------------------------------------------------------------- запуск

# Супервизор в режиме разработки: api --reload + worker --role all (stub/replay)
dev:
    @echo "появится в P1-S1-04 (supervisor --profile dev)"

# Супервизор: api + perception + vlm
up:
    @echo "появится в P1-S1-04 (cashguard up)"

# Очередь, воркеры, память
status:
    @echo "появится в P1-S1-06"

# Склеенный лог одного job
trace id:
    @echo "появится в P1-S1-06: trace {{id}}"

# Новый API-ключ
gen-key:
    @echo "появится в P0-S1-02 (scripts/gen_key.py)"

# ---------------------------------------------------------------- оценка

# Правила по replay-трассам против eval/baselines/main.json
eval-replay:
    @echo "появится в P1-S1-07"

# Подбор порогов по фолдам L1-L5
loso:
    @echo "появится в P2-S1-02"

# N параллельных прогонов curl из ТЗ через публичный URL
jury-sim url key n="5":
    @echo "появится в P4-S1-02: {{url}} x{{n}}"

# ---------------------------------------------------------------- web

# Playwright против API в stub-режиме
e2e:
    @echo "появится в P1-S2-03"

# Сборка web/dist
build-web:
    @echo "появится в P0-S2-01: pnpm -C web build"

# ---------------------------------------------------------------- перенос и трассы

# Образ deploy/linux
docker-build:
    @echo "появится в P4-S1-05"

# Трассы с Mac на Windows по LAN/Tailscale
sync-signals:
    @echo "появится в P2-S6-03"

# ---------------------------------------------------------------- только Mac

# Трассы распознавания для replay
[macos]
extract-signals:
    @echo "появится в P2-S3-04"

# Полная оценка на Mac
[macos]
eval-full:
    @echo "появится в P3-S1-02"

# Бюджеты времени и памяти (только в режиме serve)
[macos]
perf:
    @echo "появится в P0-S6-01 / P3-S6-04"

# Устойчивость: свои ракурсы, сдвиги, кропы
[macos]
robustness:
    @echo "появится в P4-S3-02"

# Установка LaunchAgents и туннеля
[macos]
deploy:
    @echo "появится в P1-S6-03"

# LaunchAgents: install | uninstall | status
[macos]
services action="status":
    @echo "появится в P1-S6-03: services {{action}}"

# Режим Mac: serve | train | dev
[macos]
mode name:
    @echo "появится в P1-S6-03: mode {{name}}"

# Обучение, этап A (отказывается стартовать, если работает сервис)
[macos]
train-a:
    #!/usr/bin/env bash
    set -euo pipefail
    if pgrep -f 'cashguard.cli up' >/dev/null; then echo "Сервис работает: сначала just mode train"; exit 1; fi
    caffeinate -i bash training/run_stage_a.sh

# Обучение, этап C (отказывается стартовать, если работает сервис)
[macos]
train-c:
    #!/usr/bin/env bash
    set -euo pipefail
    if pgrep -f 'cashguard.cli up' >/dev/null; then echo "Сервис работает: сначала just mode train"; exit 1; fi
    caffeinate -i bash training/run_stage_c.sh
