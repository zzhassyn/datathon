import { useEffect, useState } from "react";
import { LegalReference } from "../features/violations/LegalReference";
import { HttpError } from "../shared/api/errors";
import { useCatalog } from "../shared/api/queries";
import { ru } from "../shared/i18n/ru";
import { KeyGate } from "../shared/ui/KeyGate";
export function TypesPage() {
  const q = useCatalog();
  const [search, setSearch] = useState("");
  useEffect(() => {
    document.querySelector<HTMLElement>("h1")?.focus();
    if (q.data && location.hash) {
      const id = decodeURIComponent(location.hash.slice(1));
      document.getElementById(id)?.scrollIntoView({ block: "center" });
    }
  }, [q.data]);
  const items =
    q.data?.types.filter((t) =>
      [t.title_ru, t.code, ...t.legal.flatMap((l) => [l.act_title_ru, l.clause || ""])]
        .join(" ")
        .toLocaleLowerCase("ru-RU")
        .includes(search.toLocaleLowerCase("ru-RU")),
    ) || [];
  return (
    <section className="page">
      <div className="section-heading">
        <h1 tabIndex={-1}>{ru.types}</h1>
      </div>
      {q.error instanceof HttpError && q.error.status === 401 ? (
        <KeyGate />
      ) : q.isError ? (
        <div className="error">
          <p>{q.error.message}</p>
          <button type="button" onClick={() => void q.refetch()}>
            {ru.retry}
          </button>
        </div>
      ) : !q.data ? (
        <div className="skeleton" role="status" aria-label={ru.loading} />
      ) : !q.data.types.length ? (
        <div className="panel empty-state">
          <p>{ru.catalogEmpty}</p>
        </div>
      ) : (
        <>
          <input
            className="search"
            type="search"
            aria-label={ru.search}
            placeholder={ru.search}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="catalog">
            {items.length ? (
              items.map((t) => (
                <article id={t.code} className="panel" key={t.code}>
                  <h2>{t.title_ru}</h2>
                  <span className="badge">{t.verdict_impact ? ru.affects : ru.noEffect}</span>
                  <LegalReference items={t.legal} />
                </article>
              ))
            ) : (
              <p className="muted">{ru.searchEmpty}</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
