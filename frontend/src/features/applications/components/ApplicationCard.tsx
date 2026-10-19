import { useState } from "react";

import { MobileIcon } from "../../../shared/ui/mobile";
import { MAX_NOTE_CHARS, STAGE_LABELS, type ApplicationStage, type ApplicationSummary } from "../api";

const STEP_STAGES: ApplicationStage[] = ["applied", "written_test", "interview", "offer"];

export function ApplicationCard({
  application,
  stage,
  deleting,
  onOpenStage,
  onSaveNote,
}: {
  application: ApplicationSummary;
  stage: ApplicationStage;
  deleting: boolean;
  onOpenStage: () => void;
  onSaveNote: (note: string) => void;
}) {
  const [note, setNote] = useState(application.note ?? "");
  const inactive = stage === "rejected" || stage === "withdrawn";
  const stageIndex = STEP_STAGES.indexOf(stage);

  return (
    <article className={`mobile-card mobile-application-card ${deleting ? "deleting" : ""}`}>
      <div className="mobile-job-head">
        <div className="min-w-0 flex-1">
          <div className="mobile-job-title">{application.company}</div>
          <div className="mobile-job-company">{application.title}</div>
        </div>
        <button
          type="button"
          className={`mobile-stage-button mobile-tag ${inactive ? "neutral" : "amber"}`}
          aria-label={`修改${application.company}的投递阶段`}
          onClick={onOpenStage}
        >
          {STAGE_LABELS[stage]}
          <MobileIcon name="chevron" className="size-[13px]" />
        </button>
      </div>
      <div className={`mobile-steps ${inactive ? "off" : ""}`} aria-hidden="true">
        {STEP_STAGES.map((_, index) => (
          <i key={index} className={!inactive && index <= stageIndex ? "done" : ""} />
        ))}
      </div>
      <div className="mobile-updated">更新 {formatDate(application.updated_at)}</div>
      <input
        className="mobile-note mobile-no-swipe"
        value={note}
        maxLength={MAX_NOTE_CHARS}
        placeholder="加一条备注…"
        aria-label={`${application.company}的备注`}
        onChange={(event) => setNote(event.target.value)}
        onBlur={() => {
          if (note.trim() !== (application.note ?? "")) {
            onSaveNote(note);
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
        }}
      />
    </article>
  );
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
