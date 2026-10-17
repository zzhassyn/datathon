import { LoaderCircle, Check, AlertCircle, Clock3 } from "lucide-react";
import type { JobStatus } from "../types";
import { statusLabels } from "../lib/format";
export function StatusBadge({ status }: { status: JobStatus }) {
  const Icon =
    status === "done"
      ? Check
      : status === "failed"
        ? AlertCircle
        : status === "queued"
          ? Clock3
          : LoaderCircle;
  return (
    <span className={`status-badge status-${status}`}>
      <Icon
        size={12}
        className={
          status === "processing" || status === "uploading" ? "spin" : ""
        }
      />
      {statusLabels[status]}
    </span>
  );
}
