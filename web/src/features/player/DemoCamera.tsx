import { Camera } from "lucide-react";
import { ru } from "../../shared/i18n/ru";
import demoPoster from "./demo/cash-desk.svg?url";
import demoVideo from "./demo/cash-desk.webm?url";

export function DemoCamera() {
  return (
    <section className="demo-camera" aria-label={ru.demoCamera}>
      <div className="camera-heading">
        <span>
          <Camera size={16} />
          {ru.demoCamera}
        </span>
        <span className="badge">{ru.demo}</span>
      </div>
      <video
        src={demoVideo}
        poster={demoPoster}
        muted
        loop
        playsInline
        controls
        preload="none"
        aria-label={ru.demoCamera}
      />
      <p>{ru.demoHint}</p>
    </section>
  );
}
