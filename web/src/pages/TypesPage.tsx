import { useCatalog } from "../shared/api/queries";
import { ru } from "../shared/i18n/ru";
export function TypesPage() {
  const q = useCatalog();
  return (
    <section className="page">
      <h1 tabIndex={-1}>{ru.types}</h1>
      {q.isError ? (
        <p className="error">{q.error.message}</p>
      ) : !q.data ? (
        <div className="skeleton" />
      ) : (
        <p className="muted">{ru.catalogEmpty}</p>
      )}
    </section>
  );
}
