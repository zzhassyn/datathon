import { useEffect, useRef, useState } from "react";
import { ru } from "../shared/i18n/ru";
import { number } from "../shared/lib/format";

type Detection = { class_name: string; confidence: number; xyxy: number[] };
type Frame = { name: string; timestamp_sec: number; image_url: string; detections: Detection[] };
type Report = {
  check_id: string;
  model_name: string;
  weights_sha256: string;
  device: string;
  confidence_threshold: number;
  sampled_frames: number;
  elapsed_sec: number;
  counts: Record<string, number>;
  video_url: string;
  frames: Frame[];
};

async function responseReport(response: Response): Promise<Report> {
  const result = await response.json();
  if (!response.ok)
    throw new Error(typeof result.detail === "string" ? result.detail : ru.modelFailed);
  if (!/^[a-f0-9]{32}$/.test(result.check_id) || result.assessment !== "objects_only")
    throw new Error(ru.modelFailed);
  const prefix = `/api/local-model/${result.check_id}/`;
  if (
    result.video_url !== `${prefix}annotated.mp4` ||
    !Array.isArray(result.frames) ||
    !result.frames.every(
      (f: Frame) => /^frame-\d{4}\.jpg$/.test(f.name) && f.image_url === `${prefix}${f.name}`,
    )
  )
    throw new Error(ru.modelFailed);
  return result as Report;
}

export function ModelCheckPage() {
  const input = useRef<HTMLInputElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const active = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fileName, setFileName] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  useEffect(() => {
    const id = new URLSearchParams(location.search).get("check");
    const operation = new AbortController();
    if (id && /^[a-f0-9]{32}$/.test(id)) {
      fetch(`/api/local-model/${id}`, { signal: operation.signal })
        .then(responseReport)
        .then(setReport)
        .catch((e: Error) => {
          if (!operation.signal.aborted) setError(e.message);
        });
    }
    return () => {
      operation.abort();
      active.current?.abort();
    };
  }, []);
  async function start(file: File) {
    if (active.current) return;
    if (!/\.(mov|mp4)$/i.test(file.name) || !file.size || file.size > 99_000_000) {
      setError(ru.formats);
      return;
    }
    const operation = new AbortController();
    active.current = operation;
    setBusy(true);
    setError("");
    setReport(null);
    setFileName(file.name);
    const body = new FormData();
    body.set("file", file);
    try {
      const result = await responseReport(
        await fetch("/api/local-model/check", {
          method: "POST",
          body,
          signal: operation.signal,
          redirect: "error",
        }),
      );
      setReport(result);
      history.replaceState(null, "", `/?view=model&check=${result.check_id}`);
    } catch (e) {
      if (!operation.signal.aborted) setError(e instanceof Error ? e.message : ru.modelFailed);
    } finally {
      active.current = null;
      setBusy(false);
    }
  }
  const frames = report
    ? report.frames
        .filter((_, i) => i % Math.max(1, Math.floor(report.frames.length / 6)) === 0)
        .slice(0, 6)
    : [];
  return (
    <section className="page model-check">
      <h1 tabIndex={-1}>{ru.modelTitle}</h1>
      <p className="muted">{ru.modelHint}</p>
      <div className="model-notice">{ru.modelNotice}</div>
      <label className="model-select">
        <span>{ru.modelChoose}</span>
        <input
          ref={input}
          type="file"
          accept=".mov,.mp4,video/quicktime,video/mp4"
          disabled={busy}
          aria-label={ru.modelChoose}
          onChange={(e) => {
            const file = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (file) void start(file);
          }}
        />
      </label>
      {fileName && (
        <p>
          {ru.modelFile}: {fileName}
        </p>
      )}
      {busy && <p role="status">{ru.modelBusy}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {report && (
        <>
          <h2>{ru.modelDone}</h2>
          <p>
            {ru.modelSamples}: <strong>{report.sampled_frames}</strong> · {ru.modelTime}:{" "}
            <strong>
              {number(report.elapsed_sec)} {ru.seconds}
            </strong>
          </p>
          <p className="muted">{ru.modelSampling}</p>
          <video
            ref={video}
            className="model-video"
            controls
            playsInline
            src={report.video_url}
            aria-label={ru.modelVideo}
          />
          <h2>{ru.modelCounts}</h2>
          <p className="muted">{ru.modelCountsHint}</p>
          <div className="model-counts">
            {Object.entries(report.counts).map(([label, count]) => (
              <div key={label}>
                <span>{ru.modelClasses[label] ?? label}</span>
                <strong>{count}</strong>
              </div>
            ))}
          </div>
          {!Object.keys(report.counts).length && <p>{ru.modelEmpty}</p>}
          <h2>{ru.modelFrames}</h2>
          <div className="model-frames">
            {frames.map((frame) => (
              <button
                type="button"
                key={frame.name}
                onClick={() => {
                  if (video.current) {
                    video.current.currentTime = frame.timestamp_sec;
                    video.current.pause();
                  }
                }}
              >
                <img
                  src={frame.image_url}
                  alt={`${ru.modelFrame} ${number(frame.timestamp_sec)} ${ru.seconds}`}
                />
                <span>
                  {number(frame.timestamp_sec)} {ru.seconds}
                </span>
              </button>
            ))}
          </div>
          <details>
            <summary>{ru.modelDetails}</summary>
            <p>
              {report.model_name} · {report.device}
            </p>
            <p>
              {ru.modelThreshold}: {number(report.confidence_threshold)}
            </p>
            <code>{report.weights_sha256}</code>
          </details>
        </>
      )}
    </section>
  );
}
