import { FileVideo, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { RecentJobs } from "../features/recent/RecentJobs";
import { saveRecent } from "../features/recent/recentStore";
import { setPreview } from "../features/upload/previewStore";
import { type UploadProgress, uploadVideo } from "../features/upload/uploadVideo";
import { validateFiles } from "../features/upload/validateFile";
import { HttpError } from "../shared/api/errors";
import { ru } from "../shared/i18n/ru";
import { megabytes, number } from "../shared/lib/format";
import { navigate } from "../shared/lib/urlState";
import { KeyGate } from "../shared/ui/KeyGate";
export function UploadPage() {
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const selected = useRef<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [drag, setDrag] = useState(false);
  const [now, setNow] = useState(Date.now());
  const retryAt = error instanceof HttpError ? error.retryAt : 0;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!busy) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [busy]);
  useEffect(() => {
    if (retryAt <= Date.now()) return;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [retryAt]);
  async function start(files: File[]) {
    if (controller.current || Date.now() < retryAt) return;
    const invalid = validateFiles(files);
    if (invalid) {
      selected.current = null;
      setError(new Error(invalid));
      return;
    }
    const file = files[0];
    selected.current = file;
    const operation = new AbortController();
    controller.current = operation;
    setBusy(true);
    setProgress(null);
    setError(null);
    try {
      const id = await uploadVideo(file, setProgress, operation.signal);
      if (!mounted.current) return;
      setPreview(id, file);
      saveRecent({
        id,
        fileName: file.name,
        createdAt: new Date().toISOString(),
        lastStatus: "queued",
        verdict: null,
        nViolations: 0,
      });
      controller.current = null;
      navigate(`/?job=${id}`);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e : new Error(ru.network));
    } finally {
      controller.current = null;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <section className="page">
      <h1 tabIndex={-1}>{ru.uploadTitle}</h1>
      <p className="muted">{ru.emptyHint}</p>
      <section
        aria-label={ru.upload}
        className={`dropzone ${drag ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          void start(Array.from(e.dataTransfer.files));
        }}
      >
        {busy ? (
          <>
            <FileVideo size={28} />
            <h2>{progress?.percent === 100 ? ru.saving : ru.uploading}</h2>
            <span className="file-name">{selected.current?.name}</span>
            <progress aria-label={ru.progress} max={100} value={progress?.percent ?? 0} />
            <div className="upload-stats">
              <span className="mono">{progress?.percent ?? 0}%</span>
              {progress && (
                <span>
                  {megabytes(progress.loaded)} {ru.of} {megabytes(progress.total)} {ru.mb}
                </span>
              )}
            </div>
            {progress && progress.percent < 100 && (
              <p>
                {number(progress.bytesPerSecond / 1_000_000)} {ru.speed}
                {progress.etaSec !== null &&
                  ` · ${ru.remaining} ${number(progress.etaSec)} ${ru.seconds}`}
              </p>
            )}
            <p>{ru.keepOpen}</p>
            <button type="button" onClick={() => controller.current?.abort()}>
              {ru.cancel}
            </button>
          </>
        ) : (
          <>
            <Upload size={30} />
            <h2>{ru.drop}</h2>
            <p>{ru.formats}</p>
            <button
              className="primary"
              type="button"
              disabled={now < retryAt}
              onClick={() => input.current?.click()}
            >
              <Upload size={16} />
              {ru.choose}
            </button>
          </>
        )}
        <input
          ref={input}
          className="sr-only"
          type="file"
          aria-label={ru.choose}
          accept=".mov,.mp4,video/quicktime,video/mp4"
          disabled={busy || now < retryAt}
          onChange={(e) => {
            const files = Array.from(e.currentTarget.files || []);
            e.currentTarget.value = "";
            if (files.length) void start(files);
          }}
        />
      </section>
      {error && (
        <div className="error" role="alert">
          <p>{error.message}</p>
          {now < retryAt && (
            <p>
              {ru.remaining} {Math.ceil((retryAt - now) / 1000)} {ru.seconds}
            </p>
          )}
          {selected.current && !(error instanceof HttpError && error.status === 401) && (
            <button
              type="button"
              disabled={busy || now < retryAt}
              onClick={() => {
                if (selected.current) void start([selected.current]);
              }}
            >
              {ru.retry}
            </button>
          )}
        </div>
      )}
      {error instanceof HttpError && error.status === 401 && (
        <KeyGate
          onSuccess={() => {
            setError(null);
            if (selected.current) void start([selected.current]);
          }}
        />
      )}
      <RecentJobs />
    </section>
  );
}
