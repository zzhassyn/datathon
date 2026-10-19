# `tests/unit/eval/` — тесты числовой оценки

Владелец: S1 (P1-S1-02). Синтетические label/prediction objects проверяют нормализацию X/Х, дубли, allowlist, пропуски, one-to-one matching, hit/type знаменатели, confusion, signal exclusion, baseline regression, атомарный отчёт и CLI. Настоящие видео, кадры и модели не используются.

Дополнительно проверяются accuracy с учётом отсутствующих ответов, раздельные
метрики сцен (включая полностью пропущенную сцену), пустая выборка и код
завершения CLI при регрессии или неполной проверке схемы. Неуспешный прогон
сохраняет отчёт и не перезаписывает baseline.

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/eval -q
```
