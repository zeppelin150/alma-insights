import React from "react";

// Apps + attachments region. Attachment URLs are never stored — a click
// relays the gid to the host, which resolves a fresh view_url off-thread and
// opens it natively (the TaskDetailPanel lane). Pure renderer, hook-free.

// Integration hosts get labeled "Apps" rows; anything else (asana uploads,
// unknown hosts) renders as a plain attachment chip.
const HOST_LABELS = {
  slack: "Slack",
  zendesk: "Zendesk",
  gdrive: "Google Drive",
  google_drive: "Google Drive",
  drive: "Google Drive",
  dropbox: "Dropbox",
  onedrive: "OneDrive",
  box: "Box",
};

const HOST_GLYPHS = {
  Slack: "#",
  Zendesk: "❯",
  "Google Drive": "▲",
  Dropbox: "◆",
  OneDrive: "☁",
  Box: "▣",
};

export function hostLabel(host) {
  const key = String(host || "").toLowerCase();
  return HOST_LABELS[key] || "";
}

export default function AppsRow({ attachments, resolving, onOpenAttachment }) {
  const list = Array.isArray(attachments) ? attachments : [];
  if (!list.length) return null;
  const apps = list.filter((a) => hostLabel(a.host));
  const files = list.filter((a) => !hostLabel(a.host));
  return (
    <div className="tk-section tk-apps">
      {apps.length > 0 && (
        <div className="tk-section-label">Apps</div>
      )}
      {apps.map((a) => {
        const label = hostLabel(a.host);
        return (
          <button
            key={a.gid || a.name}
            type="button"
            className="tk-app-row"
            disabled={resolving === a.gid}
            onClick={() => onOpenAttachment && onOpenAttachment(a.gid)}
          >
            <span className="tk-app-glyph">{HOST_GLYPHS[label] || "◦"}</span>
            <span className="tk-app-host">{label}</span>
            <span className="tk-app-name">
              {resolving === a.gid ? `${a.name} — resolving…` : a.name}
            </span>
          </button>
        );
      })}
      {files.length > 0 && (
        <div className="tk-section-label">Attachments</div>
      )}
      {files.map((a) => (
        <button
          key={a.gid || a.name}
          type="button"
          className="tk-att-chip"
          disabled={resolving === a.gid}
          onClick={() => onOpenAttachment && onOpenAttachment(a.gid)}
        >
          {resolving === a.gid ? `${a.name} — resolving…` : `${a.name} ›`}
        </button>
      ))}
    </div>
  );
}
