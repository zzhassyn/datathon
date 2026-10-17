import { BookOpen, FileVideo, ShieldCheck } from "lucide-react";
import { ru } from "./shared/i18n/ru";
import { Link } from "./shared/ui/Link";
export function App() {
  return (
    <div className="app-shell">
      <aside className="rail">
        <Link className="brand" href="/" aria-label={ru.brand}>
          f<span>·</span>
        </Link>
        <nav aria-label={ru.nav}>
          <Link href="/" className="active" aria-label={ru.newJob}>
            <FileVideo size={21} />
          </Link>
          <Link href="/?view=types" aria-label={ru.types}>
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
        <section className="page">
          <h1 tabIndex={-1}>{ru.uploadTitle}</h1>
          <p className="muted">{ru.emptyHint}</p>
          <div className="dropzone">
            <FileVideo size={32} />
            <h2>{ru.drop}</h2>
            <p>{ru.formats}</p>
          </div>
        </section>
        <footer>{ru.disclaimer}</footer>
      </main>
    </div>
  );
}
