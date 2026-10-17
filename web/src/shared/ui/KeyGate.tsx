import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { openSession } from "../api/client";
import { ru } from "../i18n/ru";
export function KeyGate({ onSuccess }: { onSuccess?: () => void }) {
  const cache = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  return (
    <form
      className="panel access-form"
      onSubmit={async (e) => {
        e.preventDefault();
        const form = e.currentTarget;
        const input = form.elements.namedItem("code") as HTMLInputElement;
        const code = input.value;
        input.value = "";
        setBusy(true);
        setError(false);
        try {
          await openSession(code);
          await cache.invalidateQueries();
          onSuccess?.();
        } catch {
          setError(true);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2>{ru.access}</h2>
      <label>
        {ru.accessLabel}
        <input
          className="search"
          type="password"
          name="code"
          autoComplete="off"
          required
          maxLength={256}
        />
      </label>
      <button className="primary" type="submit" disabled={busy}>
        {ru.login}
      </button>
      {error && (
        <p role="alert" className="error">
          {ru.accessError}
        </p>
      )}
    </form>
  );
}
