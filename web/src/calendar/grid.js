// Pure calendar math — ISO date strings in, ISO date strings out. Mirrors the
// Qt calendar's Python source of truth: calendar.Calendar(firstweekday=6)
// .monthdatescalendar → full Sunday..Saturday weeks covering the month.
// ISO dates compare lexicographically, so plain string compare orders them.

const pad = (n) => String(n).padStart(2, "0");
export const iso = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;

export function parseIso(s) {
  const [y, m, d] = String(s).split("-").map(Number);
  return { y, m, d };
}

export function daysInMonth(y, m) {
  return new Date(y, m, 0).getDate();
}

// Day of week with Sunday = 0 (JS getDay already is Sunday-0).
export function dowSunday0(isoStr) {
  const { y, m, d } = parseIso(isoStr);
  return new Date(y, m - 1, d).getDay();
}

export function addDays(isoStr, n) {
  const { y, m, d } = parseIso(isoStr);
  const dt = new Date(y, m - 1, d + n);
  return iso(dt.getFullYear(), dt.getMonth() + 1, dt.getDate());
}

export function addMonths(y, m, n) {
  const t = y * 12 + (m - 1) + n;
  return { y: Math.floor(t / 12), m: ((t % 12) + 12) % 12 + 1 };
}

// Full Sunday-start weeks covering (y, m) — parity with Python's
// monthdatescalendar(firstweekday=6): first cell may be in the prior month,
// last in the next; week count is 4..6 depending on the month's shape.
export function monthGrid(y, m) {
  const first = iso(y, m, 1);
  const last = iso(y, m, daysInMonth(y, m));
  let cur = addDays(first, -dowSunday0(first));
  const weeks = [];
  while (weeks.length === 0 || cur <= last) {
    const week = [];
    for (let i = 0; i < 7; i++) {
      week.push(cur);
      cur = addDays(cur, 1);
    }
    weeks.push(week);
  }
  return weeks;
}

// The Sunday-start week containing isoStr.
export function weekOf(isoStr) {
  const start = addDays(isoStr, -dowSunday0(isoStr));
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}

export function groupByDate(events) {
  const by = {};
  for (const e of events || []) {
    if (!e || !e.date) continue;
    (by[e.date] = by[e.date] || []).push(e);
  }
  return by;
}

// Agenda shape: overdue (dated before today, not done) + upcoming groups
// (today onward), both date-ascending.
export function agendaGroups(events, todayIso) {
  const overdue = [];
  const upcomingBy = {};
  for (const e of events || []) {
    if (!e || !e.date) continue;
    if (e.date < todayIso && e.status !== "done") overdue.push(e);
    else if (e.date >= todayIso) (upcomingBy[e.date] = upcomingBy[e.date] || []).push(e);
  }
  overdue.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  const upcoming = Object.keys(upcomingBy).sort()
    .map((date) => ({ date, events: upcomingBy[date] }));
  return { overdue, upcoming };
}

// Keyboard day-focus movement; null for keys we don't handle.
export function moveFocus(isoStr, key) {
  if (key === "ArrowLeft") return addDays(isoStr, -1);
  if (key === "ArrowRight") return addDays(isoStr, 1);
  if (key === "ArrowUp") return addDays(isoStr, -7);
  if (key === "ArrowDown") return addDays(isoStr, 7);
  return null;
}

// "July 2026" / "Jul 18" labels without a locale dependency surprise.
const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

export function monthTitle(y, m) {
  return `${MONTHS[m - 1]} ${y}`;
}

export function shortDate(isoStr) {
  const { m, d } = parseIso(isoStr);
  return `${MONTHS[m - 1].slice(0, 3)} ${d}`;
}
