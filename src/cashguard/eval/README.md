# `src/cashguard/eval/` — оценка качества

> Владелец: S1. Реализована независимая числовая часть P1-S1-02; scene/rules replay и baseline ожидают S0/S4/S5.

| Файл | Реализовано |
|---|---|
| `labels.py` | Allowlist CSV-столбцов, X/Х, расширения и пробелы; Х-47 → X-4 с сохранением второго события, Х-48 → X-5 |
| `metrics.py` | Video recall/precision/F1, жадное one-to-one сопоставление по времени, медиана/p90, hit@1s/2s, точность типа, confusion, пропуски, clean firings; сигналы отдельно |
| `run_eval.py` | `evaluate_traces(labels, traces, evaluator)` с переданным runner; CLI для готовых числовых предсказаний |
| `report.py` | Атомарные JSON/текстовые отчёты и diff с явно переданной baseline; baseline никогда не обновляется автоматически |

`Prediction` и `LabelVideo` — внутренние типы оценки, не контракты inference/API. Метрики нельзя получать подстановкой эталона в предсказания. Пропавшие результаты учитываются отдельно и как пропуски положительных видео. Неизмеренная schema validity остаётся `null` с числом реально проверенных ответов; числовые трассы не доказывают качество evidence.

CSV-парсер читает только allowlist в результирующий объект: `file,label,timecode_sec,type_code,type_ru,clean_pair,confidence,scene`. Остальные ячейки отбрасываются сразу после разбора CSV quoting, не интерпретируются и не попадают в результат, ошибки или отчёт.

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/eval -q
PYTHONPATH=src .venv/bin/python -m cashguard.eval.run_eval \
  --labels analysis/labels_typed.csv \
  --predictions /path/to/numeric_predictions.json \
  --output /path/to/report
```

Числовой вход: объект по id видео; значение — `{"verdict":"violation","events":[{"code":"CODE","timecode_sec":12.5}]}`. Необязательный `jury_valid` — результат фактически проведённой проверки Jury JSON. Для чистого видео: `{"verdict":"no_violation","events":[]}`. `--baseline` добавляет diff; новый код на чистом видео считается регрессией даже при неизменном числе срабатываний.

Полная P1-S1-02 ещё требует owner fixtures/runner, прогона 40 трасс, двух уровней rules/final, утверждённых baseline tolerances и рецепта `eval-replay` от S0. Синтетические метрики нельзя выдавать за качество моделей.

Подробнее: [docs/eval.md](../../../docs/eval.md).
