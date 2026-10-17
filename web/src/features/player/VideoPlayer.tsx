import { MonitorPlay } from "lucide-react";
import { type Ref, useEffect, useImperativeHandle, useRef, useState } from "react";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";
import { EventTimeline } from "./EventTimeline";
import type { Marker } from "./markers";
import { contextWindow, type SeekRequest } from "./seek";
export type PlayerHandle = { seek: (time: number, context?: boolean) => void };
export function VideoPlayer({
  ref,
  serverUrl,
  localUrl,
  duration: knownDuration,
  markers,
  finished,
  onSelect,
}: {
  ref: Ref<PlayerHandle>;
  serverUrl: string | null;
  localUrl: string | null;
  duration: number;
  markers: Marker[];
  finished: boolean;
  onSelect: (m: Marker) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const container = useRef<HTMLElement>(null);
  const pending = useRef<SeekRequest | null>(null);
  const clip = useRef<{ start: number; end: number } | null>(null);
  const restore = useRef(0);
  const server = useRef(serverUrl);
  server.current = serverUrl;
  const [src, setSrc] = useState(serverUrl || localUrl);
  const [failed, setFailed] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(knownDuration);
  const [hint, setHint] = useState("");
  const [context, setContext] = useState(false);
  const updateSource = (url: string) => {
    restore.current = video.current?.currentTime || 0;
    setFailed(false);
    setSrc(url);
  };
  useEffect(() => {
    if (serverUrl && serverUrl !== src && (!video.current || video.current.paused || failed)) {
      restore.current = video.current?.currentTime || 0;
      setFailed(false);
      setSrc(serverUrl);
    }
  }, [serverUrl, src, failed]);
  function apply(request: SeekRequest) {
    const v = video.current;
    if (!v || v.readyState < 1) {
      pending.current = request;
      return;
    }
    const d = Number.isFinite(v.duration) ? v.duration : knownDuration;
    const window = contextWindow(request.time, d);
    v.currentTime = request.context
      ? window.start
      : Math.max(0, Math.min(request.time, d || request.time));
    clip.current = request.context ? window : null;
    setContext(request.context);
    setHint("");
    void v.play().catch(() => setHint(ru.playError));
  }
  useImperativeHandle(ref, () => ({
    seek: (at, contextOnly = false) => {
      const request = { time: at, context: contextOnly };
      if (server.current && src !== server.current) {
        pending.current = request;
        updateSource(server.current);
      } else apply(request);
      container.current?.scrollIntoView({ block: "nearest" });
    },
  }));
  useEffect(() => {
    const v = video.current;
    if (!v || !src || failed) return;
    let frame = 0;
    const tick = () => {
      setTime(v.currentTime);
      if (
        clip.current &&
        (v.currentTime >= clip.current.end || v.currentTime < clip.current.start - 0.2)
      ) {
        v.pause();
        clip.current = null;
        setContext(false);
      }
      if (v.requestVideoFrameCallback) frame = v.requestVideoFrameCallback(tick);
    };
    v.addEventListener("timeupdate", tickFallback);
    function tickFallback() {
      if (!v?.requestVideoFrameCallback) tick();
    }
    if (v.requestVideoFrameCallback) frame = v.requestVideoFrameCallback(tick);
    return () => {
      v.removeEventListener("timeupdate", tickFallback);
      if (frame) v.cancelVideoFrameCallback(frame);
    };
  }, [src, failed]);
  return (
    <section className="player" ref={container} aria-label={ru.videoLabel}>
      <div className="video-stage">
        {src && !failed ? (
          <video
            key={src}
            ref={video}
            src={src}
            controls
            playsInline
            muted
            preload="metadata"
            onLoadedMetadata={(e) => {
              const v = e.currentTarget;
              setDuration(Number.isFinite(v.duration) ? v.duration : knownDuration);
              if (pending.current) {
                const request = pending.current;
                pending.current = null;
                apply(request);
              } else
                v.currentTime = Math.min(
                  restore.current,
                  Number.isFinite(v.duration) ? v.duration : restore.current,
                );
            }}
            onPause={() => {
              if (server.current && server.current !== src) updateSource(server.current);
            }}
            onEnded={() => {
              clip.current = null;
              setContext(false);
            }}
            onError={() => {
              if (server.current && server.current !== src) updateSource(server.current);
              else setFailed(true);
            }}
          />
        ) : (
          <div className="video-empty">
            <MonitorPlay size={30} />
            <p>{failed || finished ? ru.videoUnavailable : ru.previewPending}</p>
          </div>
        )}
        {src === localUrl && src && <span className="video-label">{ru.localPreview}</span>}
        {context && (
          <span className="context-label">
            {ru.contextPlaying} · {timecode(clip.current?.end || 0)}
          </span>
        )}
      </div>
      {hint && (
        <p role="status" className="player-hint">
          {hint}
        </p>
      )}
      {src && !failed && (
        <EventTimeline
          markers={markers}
          duration={knownDuration || duration}
          time={time}
          onSelect={onSelect}
        />
      )}
    </section>
  );
}
