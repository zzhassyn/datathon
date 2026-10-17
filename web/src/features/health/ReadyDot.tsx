import { useReady } from "../../shared/api/queries";
import { ru } from "../../shared/i18n/ru";
export function ReadyDot() {
  const q = useReady();
  return (
    <span className="ready-state">
      <i className={`status-dot ${q.data?.ready ? "ready" : ""}`} />
      {q.isError ? ru.network : q.data?.ready ? ru.ready : ru.notReady}
    </span>
  );
}
