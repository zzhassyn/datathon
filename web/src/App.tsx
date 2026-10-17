import { BookOpen, FileVideo, ShieldCheck } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { ReadyDot } from "./features/health/ReadyDot";
import { JobPage } from "./pages/JobPage";
import { UploadPage } from "./pages/UploadPage";
import { ru } from "./shared/i18n/ru";
import { parseLocation } from "./shared/lib/urlState";
import { Link } from "./shared/ui/Link";

const TypesPage = lazy(() => import("./pages/TypesPage").then((m) => ({ default: m.TypesPage })));
export function App() {
  const [route, setRoute] = useState(() => parseLocation(location.search));
  useEffect(() => {
    const change = () => setRoute(parseLocation(location.search));
    window.addEventListener("popstate", change);
    return () => window.removeEventListener("popstate", change);
  }, []);
  useEffect(() => {
    document.title =
      route.view === "job" ? ru.titleJob : route.view === "types" ? ru.titleTypes : ru.titleUpload;
    window.scrollTo(0, 0);
    document.querySelector<HTMLElement>("h1")?.focus();
  }, [route]);
  return (
    <div className="app-shell">
      <aside className="rail">
        <Link className="brand" href="/" aria-label={ru.brand}>
          f<span>·</span>
        </Link>
        <nav aria-label={ru.nav}>
          <Link href="/" className={route.view !== "types" ? "active" : ""} aria-label={ru.newJob}>
            <FileVideo size={21} />
          </Link>
          <Link
            href="/?view=types"
            className={route.view === "types" ? "active" : ""}
            aria-label={ru.types}
          >
            <BookOpen size={21} />
          </Link>
        </nav>
        <ShieldCheck className="rail-bottom" size={20} />
      </aside>
      <main>
        <header className="toolbar">
          <div>
            <strong>{ru.brand}</strong>
            <span className="muted">{ru.subtitle}</span>
          </div>
          <span className="local-badge">
            <i />
            {ru.local}
          </span>
        </header>
        <Suspense fallback={<div className="page skeleton" />}>
          {route.view === "job" ? (
            <JobPage key={route.id} id={route.id} />
          ) : route.view === "types" ? (
            <TypesPage />
          ) : (
            <UploadPage />
          )}
        </Suspense>
        <footer>
          <span>{ru.disclaimer}</span>
          <ReadyDot />
        </footer>
      </main>
    </div>
  );
}
