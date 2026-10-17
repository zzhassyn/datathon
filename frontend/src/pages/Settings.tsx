import { useState } from "react";
import { Cable, Check, KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import type { ApiConfig } from "../types";
export function Settings({
  config,
  onSave,
  locked,
  modeLocked,
}: {
  config: ApiConfig;
  onSave: (c: ApiConfig) => void;
  locked: boolean;
  modeLocked?: boolean;
}) {
  const [draft, setDraft] = useState(config);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  function save() {
    setError("");
    if (draft.mode === "http") {
      try {
        const url = new URL(draft.baseUrl);
        if (
          !["http:", "https:"].includes(url.protocol) ||
          !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
          url.username ||
          url.password ||
          url.search ||
          url.hash ||
          !["", "/"].includes(url.pathname)
        )
          throw Error();
        if (!draft.apiKey.trim()) {
          setError("Введите X-API-Key для локального сервиса.");
          return;
        }
      } catch {
        setError(
          "Укажите адрес локального сервиса: localhost, 127.0.0.1 или [::1].",
        );
        return;
      }
    }
    onSave({ ...draft, baseUrl: draft.baseUrl.replace(/\/$/, "") });
    setSaved(true);
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">ПОДКЛЮЧЕНИЕ СЕРВИСА</div>
          <h1>Интеграция с API</h1>
          <p>Один интерфейс для демосценария и локального AI-сервиса.</p>
        </div>
        <span className="page-symbol">
          <Cable size={28} />
        </span>
      </div>
      <section className="settings-panel">
        <h2>Источник результатов</h2>
        <p className="muted">
          В репозитории пока нет backend. Деморежим работает без сети.
        </p>
        <div className="mode-options">
          <button
            disabled={locked || modeLocked}
            className={draft.mode === "mock" ? "selected" : ""}
            onClick={() => {
              setDraft({ ...draft, mode: "mock" });
              setSaved(false);
            }}
          >
            <ScanMark />
            <strong>Демонстрация</strong>
            <span>Синтетические события и имитация статусов</span>
          </button>
          <button
            disabled={locked || modeLocked}
            className={draft.mode === "http" ? "selected" : ""}
            onClick={() => {
              setDraft({ ...draft, mode: "http" });
              setSaved(false);
            }}
          >
            <Cable size={22} />
            <strong>Локальный API</strong>
            <span>Настоящие ответы вашего backend</span>
          </button>
        </div>
        <label className="field-label">
          Адрес backend
          <input
            type="url"
            value={draft.baseUrl}
            disabled={locked || modeLocked || draft.mode === "mock"}
            placeholder="http://127.0.0.1:8000"
            onChange={(e) => {
              setDraft({ ...draft, baseUrl: e.target.value });
              setSaved(false);
            }}
          />
        </label>
        <label className="field-label">
          X-API-Key
          <span className="input-icon">
            <KeyRound size={16} />
            <input
              type="password"
              autoComplete="off"
              value={draft.apiKey}
              disabled={locked || draft.mode === "mock"}
              placeholder="Введите ключ локального сервиса"
              onChange={(e) => {
                setDraft({ ...draft, apiKey: e.target.value });
                setSaved(false);
              }}
            />
          </span>
          <small>
            Ключ хранится только в памяти страницы. После перезагрузки введите
            его снова.
          </small>
        </label>
        {locked && (
          <div className="inline-notice">
            Дождитесь завершения активных задач, чтобы изменить подключение.
          </div>
        )}
        {error && (
          <div className="error-notice" role="alert">
            {error}
          </div>
        )}
        <div className="settings-save">
          <button className="button primary" onClick={save} disabled={locked}>
            {saved ? <Check size={16} /> : <Cable size={16} />}{" "}
            {saved ? "Настройки сохранены" : "Применить настройки"}
          </button>
        </div>
      </section>
      <div className="settings-bottom">
        <article>
          <LockKeyhole size={23} />
          <h3>Обработка в локальном контуре</h3>
          <p>
            Разрешены адреса loopback. Внешние перенаправления запросов
            запрещены. Видео и кадры не отправляются облачным AI.
          </p>
        </article>
        <article>
          <ShieldCheck size={23} />
          <h3>Контракт из ТЗ</h3>
          <p>
            POST /api/v1/analyze → job_id
            <br />
            GET /api/v1/jobs/&#123;job_id&#125;
            <br />
            Авторизация через X-API-Key.
          </p>
          <small>
            Нужен CORS для http://127.0.0.1:5173. Контракт и запуск описаны в
            README.
          </small>
        </article>
      </div>
    </>
  );
}
function ScanMark() {
  return <span className="scan-mark">F</span>;
}
