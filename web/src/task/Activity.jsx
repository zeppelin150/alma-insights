import React from "react";
import { Avatar } from "./avatars.jsx";
import { visibleStories, orderStories, collapseStories } from "./shape.js";

// Activity feed: Comments | All activity tabs, Oldest/Newest sort, the
// middle-collapse "N more comments" row, and three row kinds. Comment bodies
// arrive as typed tokens (text / link / mention) parsed and sanitized
// Python-side — no HTML sink here; links relay to the host for validation.

export function TokenText({ tokens, onOpenUrl }) {
  const list = Array.isArray(tokens) ? tokens : [];
  return (
    <span className="tk-tokens">
      {list.map((t, i) => {
        if (t.t === "link") {
          return (
            <a
              key={i}
              className="tk-token-link"
              href={t.href || "#"}
              onClick={(e) => {
                e.preventDefault();
                if (onOpenUrl && t.href) onOpenUrl(t.href);
              }}
            >
              {t.v || t.href}
            </a>
          );
        }
        if (t.t === "mention") {
          return (
            <span key={i} className="tk-token-mention">@{t.v}</span>
          );
        }
        return <span key={i}>{t.v}</span>;
      })}
    </span>
  );
}

function CommentRow({ story, onOpenUrl }) {
  return (
    <div className="tk-story tk-story--comment">
      <Avatar person={story.author} size={28} />
      <div className="tk-story-body">
        <div className="tk-story-head">
          <span className="tk-story-author">
            {story.author ? story.author.name : "someone"}
          </span>
          <span className="tk-story-when">{story.when}</span>
        </div>
        <div className="tk-story-text">
          <TokenText tokens={story.tokens} onOpenUrl={onOpenUrl} />
        </div>
      </div>
    </div>
  );
}

function AutomationRow({ story, onOpenUrl }) {
  return (
    <div className="tk-story tk-story--automation">
      <span className="tk-story-bolt">⚡</span>
      <div className="tk-story-body">
        <span className="tk-story-text">
          <TokenText tokens={story.tokens} onOpenUrl={onOpenUrl} />
        </span>
        <span className="tk-story-when">{story.when}</span>
      </div>
    </div>
  );
}

function SystemRow({ story, onOpenUrl }) {
  return (
    <div className="tk-story tk-story--system">
      <div className="tk-story-body">
        <span className="tk-story-text">
          {story.author && (
            <span className="tk-story-actor">{story.author.name} </span>
          )}
          <TokenText tokens={story.tokens} onOpenUrl={onOpenUrl} />
        </span>
        <span className="tk-story-when">{story.when}</span>
      </div>
    </div>
  );
}

const ROWS = { comment: CommentRow, automation: AutomationRow, system: SystemRow };

function StoryRow({ story, onOpenUrl }) {
  const Row = ROWS[story.kind] || SystemRow;
  return <Row story={story} onOpenUrl={onOpenUrl} />;
}

export default function Activity({
  stories, tab, oldestFirst, expanded, capabilities, busy,
  onTab, onSort, onExpand, onPostComment, onOpenUrl,
}) {
  const shown = orderStories(visibleStories(stories, tab), oldestFirst);
  const { head, hidden, tail } = collapseStories(shown, expanded);
  return (
    <div className="tk-activity">
      <div className="tk-activity-bar">
        <button
          type="button"
          className={`tk-tab${tab === "comments" ? " tk-tab--active" : ""}`}
          onClick={() => onTab && onTab("comments")}
        >
          Comments
        </button>
        <button
          type="button"
          className={`tk-tab${tab === "all" ? " tk-tab--active" : ""}`}
          onClick={() => onTab && onTab("all")}
        >
          All activity
        </button>
        <span className="tk-actions-spacer" />
        <button type="button" className="tk-sort" onClick={() => onSort && onSort()}>
          {oldestFirst ? "Oldest" : "Newest"} ↑↓
        </button>
      </div>
      {head.map((s, i) => (
        <StoryRow key={s.gid || `h${i}`} story={s} onOpenUrl={onOpenUrl} />
      ))}
      {hidden > 0 && (
        <button type="button" className="tk-more" onClick={onExpand}>
          {hidden} more comments
        </button>
      )}
      {tail.map((s, i) => (
        <StoryRow key={s.gid || `t${i}`} story={s} onOpenUrl={onOpenUrl} />
      ))}
      {capabilities.comment && (
        <form
          className="tk-composer"
          onSubmit={(e) => {
            e.preventDefault();
            const box = e.currentTarget.elements.comment;
            const text = (box && box.value ? box.value : "").trim();
            if (text && onPostComment && !busy) {
              onPostComment(text);
              e.currentTarget.reset();
            }
          }}
        >
          <textarea
            name="comment"
            className="tk-composer-input"
            placeholder="Ask a question or post an update…"
            disabled={busy}
            rows={2}
          />
          <div className="tk-composer-bar">
            <button type="submit" className="tk-comment-btn" disabled={busy}>
              Comment
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
