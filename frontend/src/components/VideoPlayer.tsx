import { useEffect, useRef, useState } from "react";
import { Crosshair, Film, Play, ScanLine } from "lucide-react";
import type { AnalysisJob, Violation } from "../types";
import { timecode } from "../lib/format";
export function VideoPlayer({
  job,
  event,
  seekRequest,
  onReattach,
  onSelect,
}: {
  job: AnalysisJob;
  event?: Violation;
  seekRequest: { seconds: number; serial: number };
  onReattach: () => void;
  onSelect: (event: Violation) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [analysis, setAnalysis] = useState(true);
  const [current, setCurrent] = useState(0);
  const [error, setError] = useState(false);
  const [ratio, setRatio] = useState(16 / 9);
  useEffect(() => {
    setError(false);
    setCurrent(0);
  }, [job.video.url]);
  useEffect(() => {
    if (video.current && job.video.url) {
      video.current.currentTime = seekRequest.seconds;
      setCurrent(seekRequest.seconds);
    }
  }, [seekRequest, job.video.url]);
  const allBoxes =
    event?.evidence_frames.flatMap((frame) =>
      (frame.boxes ?? []).map((box) => ({
        ...box,
        stamp: frame.timestamp_sec,
      })),
    ) ?? [];
  const boxes = allBoxes.filter((box) =>
    box.start_sec !== undefined || box.end_sec !== undefined
      ? current >= (box.start_sec ?? box.stamp) &&
        current <= (box.end_sec ?? box.stamp)
      : Math.abs(current - box.stamp) < 0.5,
  );
  const hasBoxes = allBoxes.length > 0;
  const contentWidth = ratio < 16 / 9 ? (ratio / (16 / 9)) * 100 : 100;
  const contentHeight = ratio > 16 / 9 ? (16 / 9 / ratio) * 100 : 100;
  return (
    <div className="player-panel">
      <div className="player-toolbar">
        <div className="tabs player-tabs">
          <button
            aria-pressed={!analysis}
            className={!analysis ? "active" : ""}
            onClick={() => setAnalysis(false)}
          >
            <Film size={14} />
            Original
          </button>
          <button
            aria-pressed={analysis}
            className={analysis ? "active" : ""}
            onClick={() => setAnalysis(true)}
          >
            <ScanLine size={14} />
            AI Analysis
          </button>
        </div>
        <span className="tiny-label">
          {job.mode === "mock" ? "ДЕМОСЦЕНАРИЙ" : "ЛОКАЛЬНАЯ ЗАПИСЬ"}
        </span>
      </div>
      <div className="video-stage">
        {job.video.available && job.video.url && !error ? (
          <>
            <video
              ref={video}
              key={job.video.url}
              src={job.video.url}
              poster={job.video.previewUrl}
              controls
              muted
              playsInline
              preload="metadata"
              onLoadedMetadata={(e) => {
                setRatio(
                  e.currentTarget.videoWidth / e.currentTarget.videoHeight ||
                    16 / 9,
                );
                e.currentTarget.currentTime = seekRequest.seconds;
              }}
              onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
              onError={() => setError(true)}
            />
            {analysis && boxes.length > 0 && (
              <div
                className="annotation-layer"
                style={{
                  width: `${contentWidth}%`,
                  height: `${contentHeight}%`,
                }}
              >
                {boxes.map((box, i) => (
                  <div
                    key={i}
                    className="annotation-box"
                    style={{
                      left: `${box.x * 100}%`,
                      top: `${box.y * 100}%`,
                      width: `${box.width * 100}%`,
                      height: `${box.height * 100}%`,
                    }}
                  >
                    <span>
                      {box.label}
                      {job.mode === "mock" ? " · DEMO" : ""}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {analysis && (
              <span className="analysis-watermark">
                <Crosshair size={12} />
                {hasBoxes
                  ? boxes.length
                    ? "Область внимания"
                    : "Аннотации в момент события"
                  : "Координаты областей не предоставлены"}
              </span>
            )}
          </>
        ) : (
          <div className="video-unavailable">
            <Film size={40} />
            <h3>
              {error
                ? "Браузер не воспроизводит этот кодек"
                : "Исходное видео не сохранено"}
            </h3>
            <p>
              {error
                ? "Результаты и кадры доступны ниже. Ошибка плеера не меняет результат анализа."
                : "Выберите исходный файл повторно, чтобы открыть таймкоды."}
            </p>
            {!job.video.synthetic && (
              <button className="button secondary" onClick={onReattach}>
                Выбрать исходное видео
              </button>
            )}
          </div>
        )}
      </div>
      <div className="event-timeline">
        <div className="timeline-label">
          <span>СОБЫТИЯ НА ЗАПИСИ</span>
          <span>
            {timecode(current)}{" "}
            <span className="muted">/ {timecode(job.video.duration)}</span>
          </span>
        </div>
        <div className="timeline-track">
          <span
            style={{
              width: `${Math.min(100, (current / Math.max(1, job.video.duration)) * 100)}%`,
            }}
          />
          {job.result?.violations.map((v, index) => (
            <button
              key={v.id}
              title={`${timecode(v.timecode_sec)} — ${v.type}`}
              aria-label={`Перейти к событию ${index + 1} в ${timecode(v.timecode_sec)}`}
              style={{
                left: `${Math.min(98, (v.timecode_sec / Math.max(1, job.video.duration)) * 100)}%`,
              }}
              onClick={() => onSelect(v)}
            >
              {index + 1}
            </button>
          ))}
        </div>
        <div className="timeline-ends">
          <span>00:00</span>
          <span>{timecode(job.video.duration)}</span>
        </div>
      </div>
      <div className="player-help">
        <Play size={13} />
        <span>
          Выберите событие, чтобы перейти к нужному моменту.
          {job.mode === "mock"
            ? " Области и события в демо заданы вручную."
            : ""}
        </span>
      </div>
    </div>
  );
}
