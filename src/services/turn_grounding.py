"""Layer 2 — Turn Evidence Ledger + Subject Gate (TEL-SG).

PURE MODULE. No Qt, no LLM, no engine imports. The only I/O is two thin
SQLite helpers that take a *path* (never an engine) and open a FRESH
read-only ``connection_factory`` connection per probe.

WHAT THIS IS
------------
Nothing here classifies a sentence's truth. A turn is judged by comparing
extracted *atoms* of the response against a *captured corpus* of every byte
that legitimately entered the turn (the prompt as built, the system prompt,
and this turn's tool ``result_json`` payloads), AND-gated by a subject
allowlist so the task/briefing class can never reach a flag even if grounding
fails.

Inherited posture from ``src/data/report_grounding.py`` (verbatim, by explicit
design decision — the code is NOT reusable, the discipline is):
  * separate "attempted" from "passed";
  * nothing to verify => assume good;
  * FAIL OPEN on every error path — any exception degrades to UNKNOWN/abstain;
  * emit a structured ``<tier>:<subject>:<detail>`` audit string;
  * never raise, never block.

DISCLAIMER, carried over from ``content_update/grounding.py`` which says the
same of itself: this is a COARSE vocabulary-overlap heuristic, NOT a
fabrication detector. It cannot see a negation flip, a swapped number, or a
fabrication assembled entirely out of grounded parts. See ``what_it_misses``
in the design doc; the deliberate misses are pinned by
``tests/test_turn_grounding.py``.

ORDERED GATES — first that fires wins. Every gate may only ABSTAIN or FLAG.

  S0  SURFACE GATE      MCP surface + db_path + a non-empty captured prompt,
                        else UNKNOWN. The legacy in-process text loop writes
                        ZERO ledger rows (message_id='' violates the
                        chat_messages FK under a factory connection), so
                        running there would flag every genuine tool-backed
                        answer on gemini_chats_page / the AI-Reports drilldown.
  S1  TOOL EVIDENCE     CONFIRMED_RAN (with payloads) or CONFIRMED_EMPTY only.
                        On CONFIRMED_RAN the payloads join the corpus — "a tool
                        ran" is never a blanket pass.
  S2  TRUNCATION        Any row at the 4096-byte PHI cap => abstain. A
                        truncated payload cannot prove absence.
  S4  RETRACTION GUARD  Whole-turn abstain, BEFORE any retrieval-verb scan. An
                        honest confession NECESSARILY repeats the fabricated
                        names; flagging it would let the app destroy a truthful
                        retraction.
  S5  TIER A            Structural impossibility, decided against Python-held
                        state (history + ledger + live registry), not prose
                        statistics.
  S3  FAST EXIT         Zero atoms => CLEAN.
  S6  TIER B            Atom grounding, AND-gated by the subject allowlist,
                        scoped to the CLAIM REGION (the first retrieval
                        assertion to the end of the response) and suppressed
                        outright when the corpus had to be truncated or when
                        this turn's [TODAY'S PLAN] block covers the subject.
  S7  TIER C            Fabricated negatives. SHIPPED OFF (own settings key).

KNOWN LIMITS ADDED BY THE ADVERSARIAL PASS (on top of ``what_it_misses``):
  * CONFIRMED_EMPTY means "we saw no rows", not "no tool ran".
    ``registry._persist_tool_execution`` swallows an INSERT failure AFTER
    ``dispatch_tool`` has already returned the real result to the model, and
    the MCP subprocess opens its connection with Python's 5 s default
    ``busy_timeout`` (``chat_mcp_server._get_db_connection``) against
    ``connection_factory``'s 30 s. A writer holding the lock longer than 5 s
    costs the ledger row while the model keeps the data, and the turn then
    reads as a clean empty. Fixing that belongs in the MCP server, not here.
  * A corpus longer than ``CORPUS_CAP`` abstains, so the check goes silent on
    very long sessions. That is deliberate: the alternative is flagging a true
    answer whose grounding fell out of the cap.
  * A concurrent session's ``result_json`` joins this turn's corpus (the
    watermark carries no session filter, on purpose — see
    :func:`resolve_tool_evidence`). Fail-open direction, in-memory only.

GATE ORDER DEVIATION (documented): the design lists S3 (fast exit) ahead of S4
and S5. Shipped order runs S4 then S5 BEFORE S3, because the corpus's
structurally-provable background-job fabrication carries ZERO atoms — a literal
S3-first order would fast-exit it to CLEAN and Tier A-capability could never
fire. S4 still precedes every retrieval-verb scan, which is the ordering the
design calls non-negotiable.

PRIVACY (R2): the corpus can contain PHI. It NEVER reaches a log line, a
telemetry field, or an LLM call. Only counts and short atom hashes are
recorded on the verdict.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Per-process salt for :attr:`Atom.digest`. See that docstring — a truncated
#: unsalted sha1 of a short atom is reversible, so it is NOT a de-identified
#: representation and the salt is what keeps a telemetry stream from being a
#: brute-forceable index of response content.
_DIGEST_SALT = os.urandom(16)

# ---------------------------------------------------------------------------
# Tunables (design-fixed; see risks R3/R6)
# ---------------------------------------------------------------------------

#: PHI cap in src/data/chat_tools/registry.py:19. A row at (or over) this
#: length is truncated and therefore cannot prove absence of anything.
TRUNCATION_CAP = 4096

#: Absolute floor on distinct ungrounded atoms. The reported bug carried ~9;
#: a legitimate turn that phrases one thing novelly carries 1.
MIN_UNGROUNDED = 3
#: Ratio floor, applied on top of MIN_UNGROUNDED.
MIN_UNGROUNDED_RATIO = 0.6
#: Bound the main-thread cost (R3).
MAX_ATOMS = 40
CORPUS_CAP = 256 * 1024
#: MAX_ATOMS bounds what is RETURNED, not what is scanned. Extraction cost is
#: driven by the RESPONSE length, which nothing else caps — the live enablement
#: client ignores ``max_tokens`` entirely — so the input is capped here. A
#: 32 KB prefix is far past any real Renn answer and keeps _extract_atoms
#: comfortably inside the main-thread budget on an identifier-dense dump.
RESPONSE_CAP = 32 * 1024
_WINDOW = 200
_MAX_PROBE = 200

TABLE = "chat_tool_executions"

# Tool-evidence tri-state.
CONFIRMED_RAN = "CONFIRMED_RAN"
CONFIRMED_EMPTY = "CONFIRMED_EMPTY"
UNKNOWN = "UNKNOWN"

# Verdict states.
STATE_CLEAN = "CLEAN"
STATE_UNKNOWN = "UNKNOWN"
STATE_FLAG = "FLAG"

TIER_A = "A"
TIER_B = "B"
TIER_C = "C"

MODE_OFF = "off"
MODE_TELEMETRY = "telemetry"
MODE_BANNER = "banner"
#: There is deliberately NO "block"/"discard" value. Destroying a turn on a
#: semantic inference must not be one line of config away.
MODES = (MODE_OFF, MODE_TELEMETRY, MODE_BANNER)

BANNER_TIER_A_WRITE = (
    "I can't complete that write myself — it needs your Confirm click, "
    "and no confirmation was recorded."
)
BANNER_TIER_A_JOB = (
    "I can't queue background work and deliver it later — nothing was "
    "actually started."
)
BANNER_TIER_B = (
    "Heads up — this answer states specifics that don't appear in anything "
    "I was given this turn, and no tool ran. Ask me to look it up before "
    "relying on it."
)
BANNER_TIER_C = (
    "Heads up — no lookup ran this turn, so I can't stand behind that "
    "\"nothing found\". Ask me to search before relying on it."
)

# ---------------------------------------------------------------------------
# Subject typing — THE precision boundary (risk R1)
# ---------------------------------------------------------------------------

#: CLOSED allowlist. Only these system nouns can open Tier B's candidate set.
#: Every one of them names a store that ONLY a tool can enumerate.
_SUBJECT_ALLOW = (
    "google drive", "drive", "guru", "zendesk", "knowledge base", "kb",
    "card", "cards", "collection", "ticket", "tickets", "help center",
)

#: DELIBERATE EXCLUSIONS. Do NOT "improve" this by moving a term into
#: _SUBJECT_ALLOW — a committed test asserts these stay here.
#:
#: THE INCIDENT: Renn once recanted the operator's REAL morning briefing as
#: "fabricated". That briefing produces ZERO tool rows and is entirely true —
#: its content arrives by prompt injection (startup_greeting.build_greeting_block
#: -> [TODAY'S PLAN], the persistent [NOTE], and [ENABLEMENT SCOPE]), and
#: RENN_SYSTEM_PROMPT explicitly licenses the model to present that block
#: "confidently … equivalent to a list_tasks result". Every term below is
#: exactly what those toolless channels legitimately produce.
_NEVER_SUBJECT = (
    # [TODAY'S PLAN] / list_tasks-equivalent content — the recanted briefing.
    "asana",
    "task",
    "tasks",
    "task board",
    "taskboard",
    "to-do",
    "todo",
    # The calendar tab renders the same task store with no tool call.
    "calendar",
    "schedule",
    # The briefing itself, by every name it goes by in the prompt.
    "briefing",
    "today's plan",
    "the day's plan",
    "my day",
    "my plan",
    "my list",
    # SECOND PERSON. Renn addresses the OPERATOR, so it writes "your day" /
    # "the cards on your plate", never "my day". A first-person-only veto is
    # aimed at the wrong pronoun and let the real briefing through — verified
    # by the adversarial pass, pinned by TestMorningBriefing's closer matrix.
    "your day",
    "your plan",
    "your list",
    "your plate",
    "your board",
    "your schedule",
)

#: NOTE: bare counts are PERMANENTLY disqualified as atoms (see
#: :func:`extract_atoms`) — [ENABLEMENT SCOPE] (page.py:1077) legitimately
#: hands the model document / draft / task / card / topic counts with zero
#: tools, so "confident numbers with no tool call" can never be a flag
#: condition.

#: Authoritative context markers, used ONLY by Tier C (which ships off) to
#: suppress a flag whose subject a toolless channel already covered. This map
#: is drift-sensitive — a reworded context provider silently breaks it, which
#: is precisely why Tier C is not enabled by default.
_AUTHORITATIVE_MARKERS = {
    "[TODAY'S PLAN]": ("task", "tasks", "asana", "card", "cards"),
    "[NOTE]": ("task", "tasks", "asana"),
    "[ENABLEMENT SCOPE]": ("card", "cards", "kb", "knowledge base", "collection"),
    "[DATA SCOPE]": ("ticket", "tickets"),
    "DRILLDOWN DATA": ("ticket", "tickets"),
}

# ---------------------------------------------------------------------------
# Prose patterns
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+|\n+")

#: S6-ii — first-person, past / present-perfect, retrieval assertion.
_RETRIEVAL_RE = re.compile(
    r"\b(?:i|i've|i have)\s+(?:just\s+|already\s+)?"
    r"(?:searched|checked|looked\s+(?:at|in|through|up)|pulled|queried|scanned|"
    r"ran\s+a\s+search|retrieved|found)\b"
    r"|\bthe\s+search\s+returned\b",
    re.IGNORECASE,
)

#: S4 — retraction / negated-retrieval markers. Whole-turn abstain.
_RETRACTION_RES = (
    re.compile(r"\bI (?:never|did ?n[o'’]?t|have ?n[o'’]?t|has not) "
               r"(?:actually )?(?:run|ran|search\w*|check\w*|look\w*|pull\w*|quer\w*)\b",
               re.IGNORECASE),
    re.compile(r"\bI made (?:that|those|them|it)\b.{0,24}?\bup\b", re.IGNORECASE),
    re.compile(r"\bI (?:fabricated|invented)\b", re.IGNORECASE),
    re.compile(r"\bI (?:can['’]?t|cannot|am not able to) "
               r"(?:search|check|access|run)\b", re.IGNORECASE),
    re.compile(r"\bwithout (?:having )?(?:run|running|check\w*|search\w*)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:have ?n[o'’]?t|has ?n[o'’]?t|not) "
               r"(?:searched|checked|looked)\b.{0,30}?\byet\b", re.IGNORECASE),
    re.compile(r"\byou['’]?re right to push back\b", re.IGNORECASE),
)

#: Per-sentence negation, used for S6-v (ungrounded atoms confined to a
#: negated sentence do not count).
_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|none|nothing|n[o'’]t|without|neither|nor)\b",
    re.IGNORECASE,
)

#: S5 Tier A-write — past-tense COMPLETION framing only. A proposal
#: ("I can publish that", "want me to create…") must never match.
_A_WRITE_VERB_RE = re.compile(
    r"\bI(?:'ve|’ve| have)?\s+(?:also\s+)?"
    r"(?:published|created|uploaded|renamed|posted|saved|filed|added)\b",
    re.IGNORECASE,
)
_A_WRITE_TARGET_RE = re.compile(
    r"\b(?:guru|asana|drive|card|collection|folder|task)\b", re.IGNORECASE)
#: Session-independent past-time deixis suppresses A-write: a write the
#: operator confirmed yesterday is not evidenced by today's history.
_PAST_DEIXIS_RE = re.compile(
    r"\b(?:yesterday|earlier|last (?:week|session|time)|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
#: The literal agent_chat._notify_renn emits after the operator's Confirm
#: click. Pinned by a committed test (risk R4) — a rewording here silently
#: turns a legitimate acknowledgement into a false accusation.
CONFIRM_MARKER = "[SYSTEM: operator confirmed"

#: S5 Tier A-capability — an affirmative first-person start …
_A_JOB_START_RE = re.compile(
    r"\bI(?:'ve|’ve| have)?\s+"
    r"(?:kicked off|started|queued|launched|initiated|spun up|set up|begun|began)\b",
    re.IGNORECASE,
)
#: … of a background-research JOB/MANIFEST …
#: NEVER keyed on the bare word "research" — ``research_topic`` IS a real
#: registered tool. Only the job/manifest framing is impossible.
_A_JOB_NOUN_RE = re.compile(
    r"\b(?:background research|research job|research run|a manifest|"
    r"the manifest|jobs sidebar)\b", re.IGNORECASE,
)
#: … with DEFERRED, out-of-turn delivery. This is the impossible part.
_A_JOB_DEFERRED_RE = re.compile(
    r"\b(?:when it lands|in a few minutes|in a couple of minutes|later today|"
    r"once it['’]?s done|when it['’]?s ready|jobs sidebar|"
    r"I['’]?ll (?:post|have|send|share|drop|ping))\b", re.IGNORECASE,
)
_A_NEGATION_RE = re.compile(
    r"\b(?:can['’]?t|cannot|won['’]?t|will not|do ?n[o'’]?t|"
    r"did ?n[o'’]?t|never|is ?n[o'’]?t|not)\b", re.IGNORECASE,
)

#: In-turn progress trackers. These are live registry tools TODAY and they
#: display progress WITHIN the turn — they cannot deliver anything after it,
#: so they deliberately do NOT disable Tier A-capability.
_INTURN_JOB_TOOLS = frozenset({"create_job", "update_job", "list_jobs"})
#: Drift guard: if a tool that CAN defer work past the turn ever registers,
#: Tier A-capability auto-disables rather than producing a false positive.
_DEFERRED_JOB_MARKERS = ("background", "enqueue", "schedul", "defer", "manifest",
                         "research_job")

#: S7 Tier C — asserted absence.
_ABSENCE_RE = re.compile(
    r"\b(?:there['’]?s nothing|there is nothing|nothing (?:on|about|for)|"
    r"no coverage|no (?:results?|matches?|cards?|documents?|files?)|"
    r"could ?n[o'’]?t find|did ?n[o'’]?t find|nothing came back)\b",
    re.IGNORECASE,
)

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "by",
    "with", "from", "is", "are", "was", "were", "it", "its", "this", "that",
    "these", "those", "as", "be", "been", "has", "have", "had",
})

# ---------------------------------------------------------------------------
# Atom extraction
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_QUOTE_RES = (
    re.compile(r"[\"“]([^\"“”\n]{4,120})[\"”]"),
    re.compile(r"\*\*([^*\n]{4,120})\*\*"),
    re.compile(r"\[([^\]\n]{4,120})\]\("),
    re.compile(r"(?<![\w’'])['‘]([^'‘’\n]{4,120})['’](?![\w])"),
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_PROSE_DATE_RE = re.compile(
    r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|"
    r"aug|august|sep|sept|september|oct|october|nov|november|dec|december)\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.IGNORECASE,
)
_DMY_DATE_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|january|feb|february|mar|march|apr|"
    r"april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|"
    r"nov|november|dec|december)\.?(?:,?\s+(\d{4}))?\b", re.IGNORECASE,
)
_IDENT_RES = (
    re.compile(r"\bTRC-\d+\b"),
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
               r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    re.compile(r"(?<![\w.-])\d{4,}(?![\w.-])"),
)
_URL_RES = (
    re.compile(r"https?://[^\s<>()\[\]]+"),
    # The label repetition is BOUNDED for the same reason layer 1's {0,400} is:
    # an unbounded (?:\.[\w-]{2,})* backtracks O(segments^2) over a long dotted
    # run (measured 1.3 s of main-thread freeze at 6000 segments). No real
    # hostname is 9 labels deep.
    re.compile(r"\b[\w-]{2,}(?:\.[\w-]{2,}){0,8}\.(?:com|ai|org|net|io|dev|gov|edu)\b",
               re.IGNORECASE),
    re.compile(r"\b[\w-]{2,}\.(?:pdf|docx|xlsx|pptx|csv|md|txt|pptm)\b",
               re.IGNORECASE),
)

#: LAYOUT, NOT ASSERTION. A line that is entirely a markdown heading or
#: entirely a bold/underlined label is Renn's own section furniture — it says
#: nothing about the world, so it cannot be a claim.
#:
#: THIS IS A BRIEFING-SAFETY CONTROL, not a tidy-up. The real morning briefing
#: is written as "**Overdue Items**" / "**Looking Ahead**" / "**Top Priority**"
#: / "**Suggested First Move**" section headers over TRUE, plan-block-grounded
#: content. Those headers are A1 quote atoms by shape and are never in the
#: corpus, so before this mask a 100%-true briefing scored 4-of-6 (and, on a
#: five-task day, 4-of-4) ungrounded. Verified end-to-end through the real
#: engine during the adversarial pass; pinned by TestMorningBriefing.
_LAYOUT_LINE_RE = re.compile(
    r"(?m)^[ \t]*(?:"
    r"#{1,6}[ \t]+\S.*"                       # markdown heading
    r"|[-*+][ \t]+\*\*[^*\n]{1,80}\*\*[ \t]*:?[ \t]*"   # bulleted bold label
    r"|\*\*[^*\n]{1,80}\*\*[ \t]*:?[ \t]*"    # standalone bold label
    r"|__[^_\n]{1,80}__[ \t]*:?[ \t]*"        # standalone underline label
    r")$"
)
#: A4 — >=2 Capitalized tokens with optional lowercase connectors BETWEEN
#: them. Must both start and end on a capitalized token, so "Zendesk as" and
#: "Chris. Here's" (a sentence boundary) are not titles. No '.' in the token
#: class, for the same reason.
_TITLE_TOKEN = r"[A-Z][A-Za-z0-9&'’-]*"
_TITLE_CONN = r"(?:of|the|and|for|to|in|on|a|an|v\d+|V\d+)\b"
_TITLE_RE = re.compile(
    rf"\b{_TITLE_TOKEN}(?:\s+(?:{_TITLE_CONN}|{_TITLE_TOKEN})){{0,5}}"
    rf"\s+{_TITLE_TOKEN}\b"
)

_SMART_MAP = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}
_SMART_RE = re.compile("[" + "".join(_SMART_MAP) + "]")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    """NFKD + smart-quote fold + casefold + whitespace collapse.

    R3: this runs on the Qt main thread over a corpus that can reach 256 KB.
    ``str.translate`` takes a per-character slow path on any non-ASCII (UCS-2)
    string — 12 ms on a 256 KB corpus, most of the budget — so the fold is a
    regex that only fires on the handful of real matches, and the whole
    non-ASCII branch is skipped for ASCII input.
    """
    if not text:
        return ""
    if not text.isascii():
        text = _SMART_RE.sub(lambda m: _SMART_MAP[m.group()],
                             unicodedata.normalize("NFKD", text))
    # str.split() collapses every whitespace run and drops empties — same
    # result as an r"\s+" sub plus strip, at a fraction of the cost.
    return " ".join(text.casefold().split())


def _norm_atom(text: str) -> str:
    return _normalize(text).strip(" .,;:!?-\"'()[]")


def _canon_date(year: str | None, month: int, day: int) -> str:
    """Canonicalize to ISO on BOTH sides so a corpus ISO date grounds prose.
    Year-less prose canonicalizes to ``MM-DD``, which is a substring of the
    corpus's ``YYYY-MM-DD``."""
    md = f"{month:02d}-{day:02d}"
    return f"{year}-{md}" if year else md


@dataclass(frozen=True)
class Atom:
    """A high-specificity span that cannot plausibly be invented-and-still-true."""

    text: str
    norm: str
    kind: str
    start: int
    end: int

    @property
    def digest(self) -> str:
        """Short, PER-PROCESS-SALTED hash of the atom.

        NOT a de-identification primitive, and it must never be treated as
        one. An UNSALTED truncated sha1 of a short span is trivially
        reversible — the adversarial pass recovered a 7-digit member id from
        one by brute force in seconds, and a name-shaped atom falls to a
        dictionary. The salt makes digests comparable only WITHIN one process
        (which is all shadow-mode calibration needs) instead of globally
        invertible, and :meth:`GroundingVerdict.as_telemetry` emits them only
        on a FLAG. Do not route them anywhere the response body itself does
        not already go.
        """
        return hashlib.sha1(
            _DIGEST_SALT + self.norm.encode("utf-8", "replace")
        ).hexdigest()[:10]


def _overlaps(mask: bytearray, start: int, end: int) -> bool:
    """O(span) occupancy probe.

    Was a linear scan of every previously-claimed span, which made extraction
    O(candidates^2) — 4000 identifier candidates cost 7,998,000 comparisons
    (330 ms) and a 56 KB identifier dump froze the Qt main thread for 1.3 s.
    MAX_ATOMS caps only the RETURNED list, so it never bounded that work.
    """
    return b"\x01" in mask[start:end]


def extract_atoms(text: str) -> list[Atom]:
    """Extract the high-specificity atoms of a response.

    EXPLICITLY NOT ATOMS: bare counts ("47 tickets", "5 tasks"), single
    capitalized words, tokens <=3 chars, anything inside a fenced code block.
    Counts are permanently disqualified because [ENABLEMENT SCOPE]
    legitimately hands the model counts with zero tools.

    Never raises; returns [] on any failure.
    """
    try:
        return _extract_atoms(text or "")
    except Exception as exc:  # noqa: BLE001 — fail open, always
        logger.debug("turn_grounding: atom extraction failed: %s", exc)
        return []


def _extract_atoms(text: str) -> list[Atom]:
    if not text.strip():
        return []
    if len(text) > RESPONSE_CAP:
        text = text[:RESPONSE_CAP]
    # Blank out fenced code and pure-layout lines with same-length filler so
    # offsets stay valid (callers map atoms back onto sentence spans).
    masked = _FENCE_RE.sub(lambda m: " " * (m.end() - m.start()), text)
    masked = _LAYOUT_LINE_RE.sub(lambda m: " " * (m.end() - m.start()), masked)

    claimed = bytearray(len(masked))
    out: list[Atom] = []
    seen: set[str] = set()

    def _push(raw: str, norm: str, kind: str, start: int, end: int) -> None:
        if not norm or norm in seen:
            return
        seen.add(norm)
        claimed[start:end] = b"\x01" * (end - start)
        out.append(Atom(text=raw, norm=norm, kind=kind, start=start, end=end))

    # A1 quoted spans — >=2 words, 4..120 chars.
    for rx in _QUOTE_RES:
        for m in rx.finditer(masked):
            inner = m.group(1)
            if len(inner.split()) < 2:
                continue
            norm = _norm_atom(inner)
            if len(norm) < 4:
                continue
            _push(inner, norm, "quote", m.start(1), m.end(1))

    # A2 dates — all canonicalized to ISO.
    for m in _ISO_DATE_RE.finditer(masked):
        if _overlaps(claimed, m.start(), m.end()):
            continue
        _push(m.group(0), m.group(0), "date", m.start(), m.end())
    for m in _PROSE_DATE_RE.finditer(masked):
        if _overlaps(claimed, m.start(), m.end()):
            continue
        mon = _MONTHS.get(m.group(1).lower().rstrip("."))
        day = int(m.group(2))
        if not mon or not 1 <= day <= 31:
            continue
        _push(m.group(0), _canon_date(m.group(3), mon, day), "date",
              m.start(), m.end())
    for m in _DMY_DATE_RE.finditer(masked):
        if _overlaps(claimed, m.start(), m.end()):
            continue
        mon = _MONTHS.get(m.group(2).lower().rstrip("."))
        day = int(m.group(1))
        if not mon or not 1 <= day <= 31:
            continue
        _push(m.group(0), _canon_date(m.group(3), mon, day), "date",
              m.start(), m.end())

    # A5 URLs / domains / file extensions (before A3 so 4-digit runs inside a
    # URL are not harvested as bare identifiers).
    for rx in _URL_RES:
        for m in rx.finditer(masked):
            if _overlaps(claimed, m.start(), m.end()):
                continue
            _push(m.group(0), _norm_atom(m.group(0)), "url", m.start(), m.end())

    # A3 identifiers — >=4-digit integers, TRC codes, uuids.
    for rx in _IDENT_RES:
        for m in rx.finditer(masked):
            if _overlaps(claimed, m.start(), m.end()):
                continue
            _push(m.group(0), _norm_atom(m.group(0)), "ident", m.start(), m.end())

    # A4 title-shaped spans.
    for m in _TITLE_RE.finditer(masked):
        if _overlaps(claimed, m.start(), m.end()):
            continue
        raw = m.group(0).strip()
        if len(raw) < 8 or len(raw.split()) < 2:
            continue
        norm = _norm_atom(raw)
        if len(norm) < 8:
            continue
        _push(raw, norm, "title", m.start(), m.start() + len(raw))

    out.sort(key=lambda a: a.start)
    return out[:MAX_ATOMS]


# ---------------------------------------------------------------------------
# Corpus + grounding
# ---------------------------------------------------------------------------


class CorpusIndex:
    """Normalized captured-bytes corpus with bounded windowed containment.

    Deliberately does NOT build a global token->positions dict: on a 250 KB
    corpus that costs more than the whole 15 ms budget (risk R3). Both
    matchers are C-level ``str.find`` scans instead.
    """

    __slots__ = ("norm", "size", "truncated")

    def __init__(self, text: str, cap: int = CORPUS_CAP) -> None:
        raw = text or ""
        self.truncated = len(raw) > cap
        if self.truncated:
            # HEAD *AND* TAIL. A pure tail cap discards the HEAD — and the head
            # is exactly where chat_engine.send() prepends the injected context
            # (`prompt = ctx + "\n\n" + prompt`). Evicting it made a true,
            # zero-tool, [ENABLEMENT SCOPE]-grounded answer flip CLEAN -> FLAG
            # purely because the session got long. Callers must ALSO abstain
            # while `truncated` is set: whatever fell out of the middle cannot
            # ground an atom, and "absent from a corpus we mangled" is not
            # evidence of anything.
            half = cap // 2
            raw = raw[:half] + "\n" + raw[-(cap - half - 1):]
        self.norm = _normalize(raw)
        self.size = len(self.norm)

    def __bool__(self) -> bool:
        return bool(self.norm)

    def contains(self, needle: str) -> bool:
        return bool(needle) and needle in self.norm

    def window_contains_all(self, tokens: list[str]) -> bool | None:
        """Every token occurs inside a single ``_WINDOW``-char corpus window.

        Returns True / False, or **None when the probe budget ran out** —
        which means UNDECIDED, not absent. The anchor is the atom's longest
        token, i.e. its domain term, and a domain term recurring 200+ times in
        a 256 KB corpus (one per 1.3 KB) is routine in a long on-topic Renn
        session. Reporting that as ungrounded is a silent fail-CLOSED that gets
        WORSE the longer the conversation runs; callers treat None as grounded.
        """
        if not tokens or not self.norm:
            return False
        anchor = max(tokens, key=len)
        rest = [t for t in tokens if t != anchor]
        pos = 0
        probes = 0
        while probes < _MAX_PROBE:
            i = self.norm.find(anchor, pos)
            if i < 0:
                return False
            probes += 1
            lo = max(0, i - _WINDOW)
            hi = min(self.size, i + len(anchor) + _WINDOW)
            window = self.norm[lo:hi]
            if all(t in window for t in rest):
                return True
            pos = i + 1
        return None  # undecided — budget exhausted, NOT "absent"


def _atom_tokens(atom: Atom) -> list[str]:
    return [t for t in _TOKEN_RE.findall(atom.norm)
            if len(t) > 2 and t not in _STOPWORDS]


def is_grounded(atom: Atom, index: CorpusIndex) -> bool:
    """Grounded if EITHER the normalized atom is a corpus substring, OR every
    atom token (len>2, stopwords stripped) occurs inside one 200-char window.

    Never raises.
    """
    try:
        if index is None or not index:
            return False
        if index.contains(atom.norm):
            return True
        hit = index.window_contains_all(_atom_tokens(atom))
        # None == the probe budget ran out, i.e. UNDECIDED. Fail OPEN: an
        # undecided atom counts as grounded, never as evidence of fabrication.
        return hit is not False
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.debug("turn_grounding: grounding probe failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Evidence containers
# ---------------------------------------------------------------------------


@dataclass
class LedgerMark:
    """Send-time watermark. MAX(rowid) ALONE IS INSUFFICIENT — SQLite reissues
    the max rowid after the max row is deleted (TEXT PK, no AUTOINCREMENT), so
    COUNT(*) is carried alongside and any mismatch degrades to UNKNOWN.

    COUNT(*) alone is ALSO insufficient. The design asserted no delete path
    existed; one does — ``chat_session.delete_session`` (chat_session.py:327)
    deletes this table via an f-string over a loop variable, reachable from
    the Renn history panel's Delete button (``chat_bridge.deleteSession``),
    and it is not gated on busy. Deleting exactly as many rows as the turn
    inserts leaves BOTH max and count unchanged, so a genuine
    ``search_google_drive`` turn read as CONFIRMED_EMPTY and was accused of
    fabricating. ``anchor_id`` pins the IDENTITY of the row sitting at the
    watermark: any reuse of that rowid is a different execution_id.
    """

    max_rowid: int = 0
    count: int = 0
    anchor_id: str | None = None


@dataclass
class ToolEvidence:
    """Result of the post-turn ledger probe."""

    state: str = UNKNOWN
    payloads: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    rows: int = 0
    truncated: bool = False
    #: True only when every counted execution contributed its result_json.
    #: A CONFIRMED_RAN we cannot bind to payloads must abstain, not flag.
    payloads_complete: bool = False
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state in (CONFIRMED_RAN, CONFIRMED_EMPTY)


@dataclass
class TurnEvidence:
    """CAPTURED BYTES, not reconstructed channels.

    ``prompt`` is captured as built (context + full replayed history + the
    ``[SYSTEM: operator confirmed …]`` pseudo-turns + this turn's user
    message), which is why the corpus survives the one-shot ``[TODAY'S PLAN]``
    on turn 2+ and covers every injection channel by construction.
    """

    prompt: str = ""
    system: str = ""
    tool_payloads: list[str] = field(default_factory=list)
    mark: LedgerMark | None = None
    evidence_ok: bool = False
    truncated: bool = False
    #: S0 — MCP surface with a db_path. False disables the whole check.
    surface_ok: bool = False
    tool_state: str = UNKNOWN
    payloads_complete: bool = False
    history_restored: bool = False
    #: Names of the tools that ran this turn (from the ledger window).
    tool_names: list[str] = field(default_factory=list)

    def add(self, kind: str, text: str) -> None:
        """Append captured bytes. Unknown kinds are ignored, never raised."""
        try:
            text = text or ""
            if not text:
                return
            if kind == "prompt":
                self.prompt = (self.prompt + "\n" + text) if self.prompt else text
            elif kind == "system":
                self.system = (self.system + "\n" + text) if self.system else text
            elif kind in ("tool_result", "tool_payload"):
                self.tool_payloads.append(text)
        except Exception as exc:  # noqa: BLE001 — capture must never break a turn
            logger.debug("turn_grounding: evidence.add failed: %s", exc)

    def apply_tool_evidence(self, tool_evidence: ToolEvidence | None) -> None:
        """Fold a ``resolve_tool_evidence`` result into the turn evidence."""
        try:
            if tool_evidence is None:
                self.tool_state = UNKNOWN
                self.evidence_ok = False
                return
            self.tool_state = tool_evidence.state
            self.evidence_ok = tool_evidence.ok
            self.truncated = self.truncated or tool_evidence.truncated
            self.payloads_complete = tool_evidence.payloads_complete
            self.tool_names = list(tool_evidence.tool_names)
            for payload in tool_evidence.payloads:
                self.add("tool_result", payload)
        except Exception as exc:  # noqa: BLE001
            logger.debug("turn_grounding: apply_tool_evidence failed: %s", exc)
            self.tool_state = UNKNOWN
            self.evidence_ok = False

    def corpus_text(self, history_text: str = "") -> str:
        parts = [self.prompt, self.system, history_text]
        parts.extend(self.tool_payloads)
        return "\n".join(p for p in parts if p)


@dataclass
class GroundingOptions:
    """Rollout controls. Read from settings via :func:`load_options`."""

    mode: str = MODE_TELEMETRY
    #: Tier C ships OFF behind its own key: it is the ONLY rung that can flag
    #: a turn with ZERO ungrounded atoms, so its sole protection against the
    #: briefing class is a drift-sensitive marker map.
    tier_c_enabled: bool = False
    min_ungrounded: int = MIN_UNGROUNDED
    min_ratio: float = MIN_UNGROUNDED_RATIO


@dataclass
class GroundingVerdict:
    """Warn-only outcome. The turn text is NEVER discarded, on any tier."""

    state: str = STATE_UNKNOWN
    gate: str = "S0"
    tier: str | None = None
    #: Structured ``<tier>:<subject>:<detail>`` audit string (report_grounding
    #: posture). Contains NO corpus text.
    reason: str = ""
    action: str = "none"
    banner: str = ""
    atom_count: int = 0
    ungrounded_count: int = 0
    detail: dict = field(default_factory=dict)

    @property
    def flagged(self) -> bool:
        return self.state == STATE_FLAG

    def as_telemetry(self) -> dict:
        """Dict for ``telemetry["turn_grounding"]``. Carries counts and closed-
        vocabulary audit tags only — never corpus text, prompt text or response
        text.

        ``ungrounded_hashes`` is emitted ONLY on a FLAG. It is a salted digest
        (see :attr:`Atom.digest`), not a de-identified value, and there is no
        calibration reason to ride every ordinary turn with it.
        """
        skip = {"raw"}
        if self.state != STATE_FLAG:
            skip.add("ungrounded_hashes")
        return {
            "state": self.state,
            "gate": self.gate,
            "tier": self.tier,
            "reason": self.reason,
            "action": self.action,
            "atoms": self.atom_count,
            "ungrounded": self.ungrounded_count,
            **{k: v for k, v in self.detail.items() if k not in skip},
        }


def _verdict(state, gate, *, tier=None, reason="", opts=None, banner="",
             atoms=0, ungrounded=0, detail=None) -> GroundingVerdict:
    mode = (opts.mode if opts else MODE_TELEMETRY)
    if state == STATE_FLAG and mode == MODE_BANNER and banner:
        action = "banner"
    elif state == STATE_FLAG and mode != MODE_OFF:
        action = "telemetry"
        banner = ""
    else:
        action = "none"
        banner = ""
    return GroundingVerdict(
        state=state, gate=gate, tier=tier, reason=reason, action=action,
        banner=banner, atom_count=atoms, ungrounded_count=ungrounded,
        detail=detail or {},
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def load_options() -> GroundingOptions:
    """Read ``enablement.turn_grounding`` via settings_manager.

    FAILS TO ``off`` on ANY settings error — a broken settings file must never
    enable a check that can put text in front of the operator.
    """
    try:
        from src.data.settings_manager import get_section

        section = get_section("enablement", {}) or {}
        cfg = section.get("turn_grounding") or {}
        mode = str(cfg.get("mode", MODE_TELEMETRY)).strip().lower()
        if mode not in MODES:
            mode = MODE_OFF
        return GroundingOptions(
            mode=mode,
            tier_c_enabled=bool(cfg.get("tier_c_enabled", False)),
        )
    except Exception as exc:  # noqa: BLE001 — fail closed to "off"
        logger.debug("turn_grounding: settings unreadable, disabling: %s", exc)
        return GroundingOptions(mode=MODE_OFF)


# ---------------------------------------------------------------------------
# Ledger helpers (path-based; fresh readonly connection per probe)
# ---------------------------------------------------------------------------


def snapshot_tool_ledger(db_path) -> LedgerMark | None:
    """Send-time watermark: ``(MAX(rowid), COUNT(*))`` of the tool ledger.

    INVARIANT: a FRESH ``get_connection(db_path, readonly=True)`` opened and
    closed per probe — never cached, never long-lived, never inside
    ``atomic()`` or an explicit ``BEGIN``. A reader pinned inside an explicit
    BEGIN is the ONE construct that returns a stale snapshot.

    Returns None on ANY failure (missing table, unreadable file, bad path).
    """
    if not db_path:
        return None
    conn = None
    try:
        from src.data.connection_factory import get_connection

        conn = get_connection(db_path, readonly=True)
        row = conn.execute(
            f"SELECT COALESCE(MAX(rowid), 0), COUNT(*) FROM {TABLE}"
        ).fetchone()
        if row is None:
            return None
        max_rowid = int(row[0] or 0)
        anchor_id = None
        if max_rowid:
            anchor = conn.execute(
                f"SELECT execution_id FROM {TABLE} WHERE rowid = ?",
                (max_rowid,),
            ).fetchone()
            anchor_id = str(anchor[0]) if anchor and anchor[0] is not None else None
        return LedgerMark(max_rowid=max_rowid, count=int(row[1] or 0),
                          anchor_id=anchor_id)
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.debug("turn_grounding: ledger snapshot failed: %s", exc)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def resolve_tool_evidence(db_path, mark: LedgerMark | None,
                          telemetry: dict | None = None,
                          use_mcp: bool = True) -> ToolEvidence:
    """Post-turn ledger probe → CONFIRMED_RAN / CONFIRMED_EMPTY / UNKNOWN.

    The ledger is PRIMARY and provider-symmetric (both legs wire the identical
    ``-m src.mcp.chat_mcp_server`` subprocess). Telemetry is a POSITIVE-ONLY
    corroborator and is NEVER read as proof that nothing ran:
    ``acp_bridge._MCP_TOOL_NAMES`` gates tool events behind a 16-name frozenset
    against 86 registered tools, and the Claude leg never sets
    ``_last_tool_calls`` at all.

    Watermark is on ROWID ALONE with NO session filter — 48% of live rows carry
    ``session_id='adhoc_probe'`` from the subprocess pointer-file fallback, so a
    session filter would read a real tool-backed turn as empty. A concurrent
    session's row instead reads as CONFIRMED_RAN, a false NEGATIVE, which is
    the correct asymmetry.

    Never raises.
    """
    tel = 0
    try:
        tel = int((telemetry or {}).get("tool_calls") or 0)
    except Exception:  # noqa: BLE001
        tel = 0

    if not use_mcp:
        return ToolEvidence(state=UNKNOWN, detail="surface_off")
    if not db_path:
        return ToolEvidence(state=UNKNOWN, detail="no_db_path")
    if mark is None:
        return ToolEvidence(state=UNKNOWN, detail="no_mark")

    conn = None
    try:
        from src.data.connection_factory import get_connection

        conn = get_connection(db_path, readonly=True)
        rows = conn.execute(
            f"SELECT rowid, tool_name, result_json, "
            f"COALESCE(LENGTH(result_json), 0) FROM {TABLE} "
            f"WHERE rowid > ? ORDER BY rowid",
            (int(mark.max_rowid),),
        ).fetchall()
        agg = conn.execute(
            f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM {TABLE}"
        ).fetchone()
        count_after = int(agg[0] or 0)
        max_after = int(agg[1] or 0)
        anchor_after = None
        if mark.anchor_id is not None and mark.max_rowid:
            arow = conn.execute(
                f"SELECT execution_id FROM {TABLE} WHERE rowid = ?",
                (int(mark.max_rowid),),
            ).fetchone()
            anchor_after = (str(arow[0]) if arow and arow[0] is not None
                            else None)
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.debug("turn_grounding: ledger read failed: %s", exc)
        return ToolEvidence(state=UNKNOWN, detail="read_failed")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # ROWID-REUSE / CONCURRENT-DELETE DEFENSE. MAX(rowid) alone cannot see a
    # delete-then-insert; the carried COUNT(*) makes it visible as an
    # arithmetic mismatch, and a shrunken MAX(rowid) makes a plain delete
    # visible. Either way => UNKNOWN (fail open), never a flag.
    if count_after != mark.count + len(rows) or max_after < mark.max_rowid:
        return ToolEvidence(state=UNKNOWN, detail="ledger_mismatch", rows=len(rows))

    # IDENTITY CHECK. The arithmetic above cannot see a delete-N-insert-N that
    # reuses the watermark rowid — the shape chat_session.delete_session
    # actually produces from the Renn history panel. If the row sitting at the
    # watermark is no longer the row we marked, the window is meaningless.
    if mark.anchor_id is not None and anchor_after != mark.anchor_id:
        return ToolEvidence(state=UNKNOWN, detail="ledger_anchor_mismatch",
                            rows=len(rows))

    truncated = any(int(r[3] or 0) >= TRUNCATION_CAP for r in rows)
    names = [str(r[1] or "") for r in rows]
    payloads = [str(r[2]) for r in rows if r[2] is not None]

    if rows:
        return ToolEvidence(
            state=CONFIRMED_RAN, payloads=payloads, tool_names=names,
            rows=len(rows), truncated=truncated,
            payloads_complete=(len(payloads) == len(rows)),
            detail="rows_above_watermark",
        )
    if tel > 0:
        # Positive telemetry is never contradicted — but with no rows there are
        # no payloads to bind grounding to, so the caller must abstain.
        return ToolEvidence(state=CONFIRMED_RAN, rows=0, payloads_complete=False,
                            detail="telemetry_only")
    return ToolEvidence(state=CONFIRMED_EMPTY, rows=0, payloads_complete=True,
                        detail="clean_empty")


# ---------------------------------------------------------------------------
# Sentence helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Sentence:
    start: int
    end: int
    text: str
    lower: str


def _split_sentences(text: str) -> list[_Sentence]:
    out: list[_Sentence] = []
    pos = 0
    for m in _SENT_SPLIT.finditer(text):
        seg = text[pos:m.start()]
        if seg.strip():
            out.append(_Sentence(pos, m.start(), seg, seg.lower()))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        out.append(_Sentence(pos, len(text), tail, tail.lower()))
    return out


def _term_alternation(terms) -> re.Pattern:
    """One precompiled alternation per list. Was 24 separate ``re.search``
    calls per sentence (~31 us/sentence, 25 ms on an 800-sentence response) on
    the Qt main thread; longest-first keeps the reported term unambiguous."""
    ordered = sorted(set(terms), key=len, reverse=True)
    return re.compile(
        r"(?<![a-z])(" + "|".join(re.escape(t) for t in ordered) + r")(?![a-z])"
    )


_NEVER_SUBJECT_RE = _term_alternation(_NEVER_SUBJECT)
_SUBJECT_ALLOW_RE = _term_alternation(_SUBJECT_ALLOW)


def _has_term(lower: str, terms) -> str | None:
    rx = (_NEVER_SUBJECT_RE if terms is _NEVER_SUBJECT
          else _SUBJECT_ALLOW_RE if terms is _SUBJECT_ALLOW
          else _term_alternation(terms))
    m = rx.search(lower)
    return m.group(1) if m else None


def _subject_of(sentence: _Sentence) -> str | None:
    """S6-i. An allowlisted system noun with NO _NEVER_SUBJECT term present."""
    if _NEVER_SUBJECT_RE.search(sentence.lower):
        return None
    m = _SUBJECT_ALLOW_RE.search(sentence.lower)
    return m.group(1) if m else None


def _is_retraction(text: str) -> bool:
    return any(rx.search(text) for rx in _RETRACTION_RES)


# ---------------------------------------------------------------------------
# Tier A
# ---------------------------------------------------------------------------


def _job_capability_enabled(registry_names) -> bool:
    """Auto-disable Tier A-capability if a tool that can defer work past the
    turn ever registers. ``create_job``/``update_job``/``list_jobs`` are live
    today but are IN-TURN progress trackers — they cannot deliver anything
    after the turn, so they deliberately do not disable the check."""
    try:
        for name in (registry_names or ()):
            low = str(name).lower()
            if low in _INTURN_JOB_TOOLS:
                continue
            if any(marker in low for marker in _DEFERRED_JOB_MARKERS):
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


def _tier_a(sentences, evidence, history_text, registry_names):
    """Structural impossibility, decided against Python-held state."""
    # ANY ledger row in the turn window suppresses A-write.
    #
    # This rung's whole premise is "Renn structurally CANNOT do this — every
    # write rides a native Confirm card". That premise is already false the
    # moment a tool executed, and the previous 5-word name test
    # (publish|create|upload|rename|write) could not see it: add_subtask,
    # toggle_subtask, update_task, update_scratchpad, draft_subtasks and
    # revise_draft are all DIRECT, un-gated registry writes
    # (src/data/chat_tools/registry.py:218-238 — only request_asana_task_update
    # is Confirm-gated). A truthful "I've added the subtask …" after a real
    # add_subtask was therefore accused by BANNER_TIER_A_WRITE, which asserts
    # the write did not happen — a flatly false statement about a committed
    # row. Widening the list would just be a new guess against a registry that
    # keeps growing; the row itself is the evidence.
    names = getattr(evidence, "tool_names", None) or []
    write_row = bool(names)

    confirmed = CONFIRM_MARKER in (history_text or "")
    job_enabled = _job_capability_enabled(registry_names)

    for idx, sent in enumerate(sentences):
        # A-write
        if (not evidence.history_restored and not confirmed and not write_row
                and _A_WRITE_VERB_RE.search(sent.text)
                and _A_WRITE_TARGET_RE.search(sent.text)
                and not _PAST_DEIXIS_RE.search(sent.text)
                and not _A_NEGATION_RE.search(sent.text)):
            return TIER_A, "write", BANNER_TIER_A_WRITE
        # A-capability
        if (job_enabled
                and _A_JOB_START_RE.search(sent.text)
                and _A_JOB_NOUN_RE.search(sent.text)
                and not _A_NEGATION_RE.search(sent.text)):
            window = sent.text
            if idx + 1 < len(sentences):
                window = window + " " + sentences[idx + 1].text
            if _A_JOB_DEFERRED_RE.search(window):
                return TIER_A, "job", BANNER_TIER_A_JOB
    return None, "", ""


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------


def assess(response: str, evidence: TurnEvidence, history_text: str = "",
           registry_names=None, opts: GroundingOptions | None = None
           ) -> GroundingVerdict:
    """Judge a completed turn. NEVER raises; any failure => UNKNOWN.

    Warn-only by construction: the caller must never discard the turn text.
    """
    opts = opts or GroundingOptions()
    try:
        return _assess(response or "", evidence, history_text or "",
                       registry_names, opts)
    except Exception as exc:  # noqa: BLE001 — fail open, always
        logger.debug("turn_grounding: assess failed: %s", exc)
        return _verdict(STATE_UNKNOWN, "error", reason="unknown:error:exception",
                        opts=opts)


def _assess(response, evidence, history_text, registry_names, opts):
    if opts.mode == MODE_OFF:
        return _verdict(STATE_UNKNOWN, "off", reason="unknown:off:disabled",
                        opts=opts)

    # ---- S0 SURFACE GATE ---------------------------------------------------
    if evidence is None or not getattr(evidence, "surface_ok", False):
        return _verdict(STATE_UNKNOWN, "S0", reason="unknown:surface:not_mcp",
                        opts=opts)
    # R7: a detector without its capture is the worst outcome — the corpus
    # would be empty and the briefing WOULD flag.
    if not (evidence.prompt or "").strip():
        return _verdict(STATE_UNKNOWN, "S0", reason="unknown:surface:no_capture",
                        opts=opts)

    # ---- S1 TOOL-EVIDENCE STATE -------------------------------------------
    if evidence.tool_state == UNKNOWN:
        return _verdict(STATE_UNKNOWN, "S1", reason="unknown:tools:unresolved",
                        opts=opts)
    if evidence.tool_state == CONFIRMED_RAN and not evidence.payloads_complete:
        # A tool ran but we cannot bind grounding to its results. Abstaining is
        # the fail-open direction; flagging here would accuse a real answer.
        return _verdict(STATE_UNKNOWN, "S1", reason="unknown:tools:unbound_payloads",
                        opts=opts)

    # ---- S2 TRUNCATION SUPPRESSION ----------------------------------------
    if evidence.truncated:
        return _verdict(STATE_UNKNOWN, "S2", reason="unknown:truncation:phi_cap",
                        opts=opts)

    sentences = _split_sentences(response)

    # ---- S4 RETRACTION GUARD (before ANY retrieval-verb scan) --------------
    if _is_retraction(response):
        return _verdict(STATE_CLEAN, "S4", reason="clean:retraction:confession",
                        opts=opts)

    # ---- S5 TIER A --------------------------------------------------------
    tier, subject, banner = _tier_a(sentences, evidence, history_text,
                                    registry_names)
    if tier:
        detail_tag = ("no_confirmation" if subject == "write"
                      else "no_such_capability")
        return _verdict(STATE_FLAG, "S5", tier=TIER_A,
                        reason=f"A:{subject}:{detail_tag}", opts=opts,
                        banner=banner, detail={"subject": subject})

    # ---- S3 FAST EXIT ------------------------------------------------------
    # Disposes of clarifying questions, capability explanations, proposals,
    # "I haven't searched Drive yet" and summarizing the user's own list.
    # Tier C is the ONE rung that can flag a zero-atom turn, so the exit is
    # skipped while it is enabled.
    atoms = extract_atoms(response)
    tier_c_live = bool(opts.tier_c_enabled
                       and evidence.tool_state == CONFIRMED_EMPTY)
    if not atoms and not tier_c_live:
        return _verdict(STATE_CLEAN, "S3", reason="clean:atoms:none", opts=opts)

    index = CorpusIndex(evidence.corpus_text(history_text))

    # ---- CORPUS TRUNCATION SUPPRESSION -------------------------------------
    # Same reasoning as S2: a corpus we had to cut cannot prove absence. Only
    # Tier B/C consult it, and both are grounding-based, so abstain outright.
    if index.truncated:
        return _verdict(STATE_UNKNOWN, "S6", reason="unknown:corpus:truncated",
                        opts=opts, atoms=len(atoms),
                        detail={"corpus_chars": index.size,
                                "tool_state": evidence.tool_state})

    ungrounded = [a for a in atoms if not is_grounded(a, index)]
    ratio = len(ungrounded) / max(len(atoms), 1)
    base_detail = {
        "ratio": round(ratio, 3),
        "tool_state": evidence.tool_state,
        "corpus_chars": index.size,
        "ungrounded_hashes": [a.digest for a in ungrounded[:10]],
    }

    # ---- S6 TIER B ---------------------------------------------------------
    candidates = []
    for sent in sentences:
        subj = _subject_of(sent)
        if subj and _RETRIEVAL_RE.search(sent.text):
            candidates.append((sent, subj))

    if candidates and atoms:
        subj = candidates[0][1]
        # BARRIER: the briefing turn is identifiable STRUCTURALLY, not
        # statistically. When the one-shot [TODAY'S PLAN] block is in this
        # turn's captured prompt, the operator's real, tool-free task data is
        # in play — and that data IS Guru cards and KB entries, so a
        # card/kb/guru subject on this turn is exactly what the toolless
        # channel legitimately produces. (The design's Barrier 1 claimed the
        # subject allowlist alone protected the briefing; it does not, because
        # _SUBJECT_ALLOW contains card/cards/guru/kb. Verified: a true
        # briefing flagged "B:cards:4of6_ungrounded".) Scoped to the marker's
        # own subjects so a Drive fabrication on the same turn still flags.
        if _plan_block_covers(evidence, subj):
            return _verdict(STATE_CLEAN, "S6",
                            reason=f"clean:briefing:plan_block_covers_{subj}",
                            opts=opts, atoms=len(atoms),
                            ungrounded=len(ungrounded), detail=base_detail)

        # THE CLAIM REGION. The subject+retrieval gate is per-sentence, so the
        # thresholds must be too — counting ungrounded atoms GLOBALLY let one
        # ordinary closing sentence ("I checked the cards you own — …") open
        # the gate while Renn's own section headings elsewhere supplied the
        # ungrounded mass. A retrieval assertion introduces the claims that
        # FOLLOW it, so the region runs from the first candidate sentence to
        # the end of the response. A trailing aside therefore has nothing to
        # answer for, while "I searched Drive and found: 1. … 2. … 3. …" —
        # where the atoms live in later list lines, not the verb's own
        # sentence — is still fully covered.
        region_start = candidates[0][0].start
        in_region = [a for a in atoms if a.start >= region_start]
        # (v) atoms confined to a negated sentence do not count.
        negated = [s for s in sentences if _NEGATION_RE.search(s.text)]
        ungrounded_set = set(ungrounded)
        effective = [
            a for a in in_region
            if a in ungrounded_set
            and not any(s.start <= a.start < s.end for s in negated)
        ]
        local_ratio = len(effective) / max(len(in_region), 1)
        if (len(effective) >= opts.min_ungrounded
                and local_ratio >= opts.min_ratio):
            base_detail["ratio"] = round(local_ratio, 3)
            return _verdict(
                STATE_FLAG, "S6", tier=TIER_B,
                reason=f"B:{subj}:{len(effective)}of{len(in_region)}_ungrounded",
                opts=opts, banner=BANNER_TIER_B, atoms=len(in_region),
                ungrounded=len(effective), detail=base_detail)

    # ---- S7 TIER C (off by default) ---------------------------------------
    if tier_c_live:
        for sent in sentences:
            subj = _subject_of(sent)
            if not subj or not _RETRIEVAL_RE.search(sent.text):
                continue
            if not _ABSENCE_RE.search(sent.text):
                continue
            if _marker_covers(index, subj):
                continue
            return _verdict(
                STATE_FLAG, "S7", tier=TIER_C,
                reason=f"C:{subj}:asserted_absence_no_tool", opts=opts,
                banner=BANNER_TIER_C, atoms=len(atoms),
                ungrounded=len(ungrounded), detail=base_detail)

    if not atoms:
        return _verdict(STATE_CLEAN, "S3", reason="clean:atoms:none", opts=opts)
    return _verdict(STATE_CLEAN, "S6", reason="clean:grounding:ok", opts=opts,
                    atoms=len(atoms), ungrounded=len(ungrounded),
                    detail=base_detail)


#: The one-shot briefing marker, as ``startup_greeting.build_greeting_block``
#: emits it and ``chat_engine.send`` prepends it to ``prompt``.
PLAN_MARKER = "[TODAY'S PLAN]"
#: Subjects the plan block legitimately covers with ZERO tool calls. The
#: operator's real tasks ARE Guru cards / KB entries, so these words appearing
#: next to a retrieval verb on a briefing turn is normal Renn prose, not a
#: fabrication signal. Drive / Zendesk / tickets are NOT here: the plan block
#: says nothing about them, so a Drive fabrication on the greeting turn still
#: flags.
_PLAN_COVERED_SUBJECTS = frozenset({
    "card", "cards", "kb", "knowledge base", "guru", "collection",
})


def _plan_block_covers(evidence, subject: str) -> bool:
    """True when THIS turn carries the [TODAY'S PLAN] block and the flagged
    subject is one that block legitimately supplies."""
    try:
        if subject not in _PLAN_COVERED_SUBJECTS:
            return False
        return PLAN_MARKER in (getattr(evidence, "prompt", "") or "")
    except Exception:  # noqa: BLE001 — fail open (suppress nothing)
        return False


def _marker_covers(index: CorpusIndex, subject: str) -> bool:
    """Tier C suppression: an authoritative toolless channel already covers
    this subject, so its silence is not evidence of a fabricated absence."""
    for marker, subjects in _AUTHORITATIVE_MARKERS.items():
        if index.contains(_normalize(marker)) and subject in subjects:
            return True
    return False
