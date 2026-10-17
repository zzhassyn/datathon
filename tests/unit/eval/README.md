# `tests/unit/eval/` — тесты числовой оценки

Владелец: S1 (P1-S1-02). Синтетические label/prediction objects проверяют нормализацию X/Х, дубли, allowlist, пропуски, one-to-one matching, hit/type знаменатели, confusion, signal exclusion, baseline regression, атомарный отчёт и CLI. Настоящие видео, кадры и модели не используются.

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/eval -q
```
