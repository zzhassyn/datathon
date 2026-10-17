import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Camera, ChevronDown, Expand, FileVideo, Grid2X2, Maximize, MonitorPlay, Pause, Play, Upload, X, ZoomIn, BookOpen, AlertTriangle } from 'lucide-react';
import { validateFile, timecode } from './workspace';
import './styles.css';

const SAMPLE = '/demo/case-34.mp4';
const cameras = ['Касса 01', 'Касса 02', 'Касса 03'];
type Media = { url: string; name: string; demo: boolean; size?: number };
type Preview = { title: string; src: string; kind: 'image' | 'video' };
const sample: Media = { url: SAMPLE, name: 'Х-34.mov', demo: true, size: 3593850 };

function CameraTile({ index, paused, onOpen }: { index: number; paused: boolean; onOpen: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { if (paused) video.current?.pause(); else void video.current?.play().catch(() => {}); }, [paused]);
  return <button className="camera-tile" onClick={onOpen} aria-label={`Открыть ${cameras[index]}`}>
    <video ref={video} src={SAMPLE} poster="/demo/frame-5.jpg" autoPlay={!paused} loop muted playsInline onError={() => setFailed(true)} onLoadedMetadata={e => { e.currentTarget.currentTime = index * 4; }}/>
    <div className="feed-top"><span className="camera-name">{cameras[index]}</span><span className="live-badge"><i/>{failed ? 'НЕДОСТУПНО' : paused ? 'ПАУЗА' : 'LIVE'} · ДЕМО</span></div>
    <span className="feed-expand"><Expand size={17}/></span>
  </button>;
}

function Player({ media, seek, onDuration, onFailure }: { media: Media; seek: { time: number; key: number }; onDuration: (n: number) => void; onFailure: () => void }) {
  const ref = useRef<HTMLVideoElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [error, setError] = useState(false);
  const [hint, setHint] = useState('');
  useEffect(() => { if (ref.current && ref.current.readyState >= 1) { ref.current.currentTime = Math.min(seek.time, duration); ref.current.pause(); } }, [seek, duration]);
  function toggle() { const v = ref.current; if (!v) return; if (v.paused) void v.play().catch(() => setHint('Не удалось начать воспроизведение. Попробуйте ещё раз.')); else v.pause(); }
  function jump(n: number) { if (ref.current) ref.current.currentTime = Math.max(0, Math.min(duration, n)); }
  return <div className="player" ref={container}>
    <div className="video-stage">
      <div className="stage-label"><Camera size={14}/>{media.demo ? 'Касса 01' : media.name}</div>
      <video ref={ref} src={media.url} poster={media.demo ? '/demo/frame-5.jpg' : undefined} playsInline preload="metadata" muted onClick={toggle}
        onLoadedMetadata={e => { const d = e.currentTarget.duration; if (!Number.isFinite(d) || d <= 0) { setError(true); onFailure(); return; } setDuration(d); onDuration(d); e.currentTarget.currentTime = Math.min(seek.time, d); }}
        onTimeUpdate={e => setTime(e.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
        onError={() => { setError(true); onFailure(); }}/>
      {!playing && !error && <button className="big-play" onClick={toggle} aria-label="Воспроизвести видео"><Play size={25} fill="currentColor"/></button>}
      <span className="stage-timestamp">{timecode(time, true)}</span>
      {error && <div className="video-error"><AlertTriangle/><strong>Видео не воспроизводится</strong><span>Файл повреждён или его кодек не поддерживается браузером. Попробуйте MOV или MP4 с видео H.264.</span></div>}
    </div>
    <div className="player-controls">
      <button onClick={toggle} disabled={error} aria-label={playing ? 'Пауза' : 'Воспроизвести'}>{playing ? <Pause size={19}/> : <Play size={19}/>}</button>
      <span className="control-time">{timecode(time)} <em>/ {timecode(duration)}</em></span>
      <span className="controls-spacer"/>
      <label className="speed-select"><span className="sr-only">Скорость воспроизведения</span><select value={speed} onChange={e => { const next = Number(e.target.value); setSpeed(next); if (ref.current) ref.current.playbackRate = next; }}><option value="0.5">0.5×</option><option value="1">1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label>
      <button aria-label="На весь экран" onClick={() => { if (document.fullscreenElement) void document.exitFullscreen(); else void container.current?.requestFullscreen().catch(() => setHint('Полноэкранный режим недоступен в этом браузере.')); }}><Maximize size={18}/></button>
    </div>
    <div className="timeline-wrap"><div className="scrub"><input type="range" aria-label="Позиция видео в секундах" min="0" max={duration || 1} step="0.01" value={time} disabled={error} onChange={e => jump(Number(e.target.value))} style={{ '--progress': `${duration ? time / duration * 100 : 0}%` } as React.CSSProperties}/>
      {media.demo && duration > 13 && <button className="event-marker" aria-label="Перейти к событию на 13 секунде" style={{ left: `${13 / duration * 100}%` }} onClick={() => jump(13)}><span>00:13</span></button>}
      </div><div className="timeline-labels"><span>00:00</span><span>{timecode(duration / 2)}</span><span>{timecode(duration)}</span></div></div>
    {hint && <p className="player-hint" role="status">{hint}</p>}
  </div>;
}

function EventCard({ onSeek, onPreview }: { onSeek: () => void; onPreview: (p: Preview) => void }) {
  const [expanded, setExpanded] = useState(true);
  const clip = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  return <article className="event-card">
    <div className="event-card-head">
      <span className="event-dot"/>
      <h3>Пересчёт после ухода клиента</h3>
      <button className="timecode-button" onClick={onSeek}><Play size={13}/>00:13.000</button>
      <button className="icon-button" aria-label={expanded ? 'Свернуть событие' : 'Развернуть событие'} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><ChevronDown size={18} style={{ transform: expanded ? 'rotate(180deg)' : undefined }}/></button>
    </div>
    {expanded && <div className="event-content">
      <button className="context-clip" aria-label={playing ? 'Остановить фрагмент' : 'Смотреть фрагмент с 11 до 15 секунды'} onClick={() => { const v = clip.current; if (v?.paused) void v.play().catch(() => {}); else v?.pause(); }}>
        <video ref={clip} src="/demo/context-34.mp4" poster="/demo/frame-13.jpg" muted playsInline onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}/>
        <span className="clip-play">{playing ? <Pause size={18}/> : <Play size={18} fill="currentColor"/>}</span><span className="clip-range">00:11 — 00:15 · 4 сек</span>
      </button>
      <div className="event-description">
        <p>Клиент уходит. Кассир продолжает работу с наличными у счётной машинки.</p>
        <p className="muted">Возможен пересчёт без присутствия клиента. Требует проверки.</p>
        <div className="evidence-grid">{[11, 13, 15].map(t => <button key={t} className={`evidence ${t === 13 ? 'selected' : ''}`} onClick={() => onPreview({ title: `Х-34 · ${timecode(t, true)}`, src: `/demo/frame-${t}.jpg`, kind: 'image' })} aria-label={`Увеличить кадр на ${t} секунде`}>
          <img src={`/demo/frame-${t}.jpg`} alt={`Кадр на ${t} секунде`}/><span className="evidence-zoom"><ZoomIn size={15}/></span><span className="evidence-time">{timecode(t, true)}</span>
        </button>)}</div>
        <details className="source"><summary><BookOpen size={15}/><span>НБРК № 231 · пункт 20</span><ChevronDown size={14}/></summary><div><p>Наличные деньги принимаются и пересчитываются кассовым работником в присутствии клиента.</p><a href="https://adilet.zan.kz/rus/docs/V1900019680" target="_blank" rel="noreferrer">Правила осуществления кассовых операций · Әділет <ArrowUpRight size={14}/></a></div></details>
      </div>
    </div>}
  </article>;
}

export default function App() {
  const [page, setPage] = useState<'cameras' | 'analysis'>('cameras');
  const [media, setMedia] = useState<Media>(sample);
  const [paused, setPaused] = useState(false);
  const [duration, setDuration] = useState(17.733);
  const [seek, setSeek] = useState({ time: 0, key: 0 });
  const [modal, setModal] = useState<Preview | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const [invalidMedia, setInvalidMedia] = useState(false);
  const [camera, setCamera] = useState('all');
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const currentURL = useRef<string | null>(null);
  useEffect(() => { if (modal) dialog.current?.showModal(); else dialog.current?.close(); }, [modal]);
  useEffect(() => () => { if (currentURL.current) URL.revokeObjectURL(currentURL.current); }, []);
  function navigate(next: 'cameras' | 'analysis') { setPage(next); window.scrollTo({ top: 0 }); }
  function openSample(at = 13) {
    if (currentURL.current) URL.revokeObjectURL(currentURL.current);
    currentURL.current = null;
    setMedia(sample); setInvalidMedia(false); setError(''); setSeek({ time: at, key: Date.now() }); navigate('analysis');
  }
  function seekEvent() { setSeek({ time: 13, key: Date.now() }); document.getElementById('recording')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  function loadFile(file?: File) {
    if (!file) return;
    const problem = validateFile(file); if (problem) { setError(problem); return; }
    if (currentURL.current) URL.revokeObjectURL(currentURL.current);
    const url = URL.createObjectURL(file); currentURL.current = url;
    setMedia({ url, name: file.name, size: file.size, demo: false }); setDuration(0); setSeek({ time: 0, key: Date.now() }); setInvalidMedia(false); setError(''); navigate('analysis');
  }
  return <div className="app-shell">
    <aside className="rail">
      <button className="brand" onClick={() => navigate('cameras')} aria-label="Forte Vision" title="Forte Vision">f<span>·</span></button>
      <nav aria-label="Основная навигация">
        <button className={page === 'cameras' ? 'active' : ''} aria-label="Камеры" title="Камеры" onClick={() => navigate('cameras')}><Camera size={21}/></button>
        <button className={page === 'analysis' ? 'active' : ''} aria-label="Проверка видео" title="Проверка видео" onClick={() => navigate('analysis')}><MonitorPlay size={21}/></button>
      </nav>
      <span className="rail-bottom" title="Forte Vision · локальный frontend">FV</span>
    </aside>
    <main onDragOver={e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDragging(true); } }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length > 1) { setError('Выберите одно видео.'); return; } loadFile(e.dataTransfer.files[0]); }}>
      {dragging && <div className="drop-overlay"><Upload size={32}/><h2>Открыть видео</h2><span>MOV · MP4 · WebM</span></div>}
      <header className="toolbar">
        <div className="toolbar-left">{page === 'cameras' ? <><Grid2X2 size={17}/><select aria-label="Выбрать камеру" value={camera} onChange={e => setCamera(e.target.value)}><option value="all">Все камеры</option>{cameras.map((name, i) => <option key={name} value={String(i)}>{name}</option>)}</select></> : <><button className="icon-button" aria-label="Назад к камерам" onClick={() => navigate('cameras')}><ArrowLeft size={19}/></button><h1>{media.name}</h1><span className="duration">{timecode(duration)}</span></>}</div>
        <div className="toolbar-right"><span className="demo-badge" title="Запись из кейса. Разметка демонстрационная. Модель не подключена.">Демо</span>{page === 'cameras' && <button className="icon-button" aria-label={paused ? 'Продолжить камеры' : 'Приостановить камеры'} title={paused ? 'Продолжить' : 'Пауза'} onClick={() => setPaused(!paused)}>{paused ? <Play size={18}/> : <Pause size={18}/>}</button>}<button className="upload-button" aria-label="Загрузить видео" onClick={() => input.current?.click()}><Upload size={16}/><span>Загрузить видео</span></button></div>
      </header>
      {error && <div className="error-banner" role="alert"><AlertTriangle size={16}/><span>{error}</span><button className="icon-button" aria-label="Закрыть ошибку" onClick={() => setError('')}><X size={16}/></button></div>}
      {page === 'cameras' ? <>
        <div className="event-strip"><button className="event-thumb" onClick={() => openSample()} aria-label="Открыть событие: пересчёт после ухода клиента"><img src="/demo/frame-13.jpg" alt="Событие на 13 секунде"/><span className="thumb-tag">00:13</span><span className="thumb-label"><i/>Пересчёт без клиента</span></button></div>
        <div className={`camera-grid ${camera !== 'all' ? 'single-camera' : ''}`}>{cameras.map((_, i) => (camera === 'all' || camera === String(i)) && <CameraTile key={i} index={i} paused={paused} onOpen={() => setModal({ title: cameras[i] + ' · Демо', src: SAMPLE, kind: 'video' })}/>)}</div>
      </> : <div className="analysis-layout">
        <section id="recording"><Player key={media.url} media={media} seek={seek} onDuration={setDuration} onFailure={() => setInvalidMedia(true)}/></section>
        {media.demo ? <section className="results"><div className="result-heading"><h2>События <span>1</span></h2><span className="result-status"><i/>Признаки возможного нарушения</span></div><EventCard onSeek={seekEvent} onPreview={setModal}/></section> : <div className="empty-result"><FileVideo size={23}/><div><h2>{invalidMedia ? 'Ошибка воспроизведения' : 'Анализ не выполнен'}</h2><p>{invalidMedia ? 'Попробуйте видео с кодеком H.264.' : 'Модель не подключена.'}</p></div><button onClick={() => openSample()}>Открыть пример <ArrowUpRight size={15}/></button></div>}
      </div>}
    </main>
    <input ref={input} className="sr-only" type="file" accept=".mov,.mp4,.m4v,.webm" onChange={e => { loadFile(e.target.files?.[0]); e.target.value = ''; }}/>
    <dialog ref={dialog} className="preview-dialog" onCancel={() => setModal(null)} onClose={() => setModal(null)} onClick={e => { if (e.target === e.currentTarget) setModal(null); }}><div className="dialog-head"><h2>{modal?.title}</h2><button className="icon-button" aria-label="Закрыть просмотр" onClick={() => setModal(null)}><X size={21}/></button></div>{modal?.kind === 'image' ? <img className="full-evidence" src={modal.src} alt={modal.title}/> : modal && <video src={modal.src} controls autoPlay loop muted playsInline/>}</dialog>
  </div>;
}
