// Parity tests against the Python source of truth:
// calendar.Calendar(firstweekday=6).monthdatescalendar — plus the agenda and
// keyboard-focus reducers the CalendarApp leans on.
import { describe, expect, it } from "vitest";
import {
  addDays, addMonths, agendaGroups, dowSunday0, groupByDate, monthGrid,
  monthTitle, moveFocus, shortDate, weekOf,
} from "./grid.js";

describe("monthGrid — Python monthdatescalendar(firstweekday=6) parity", () => {
  it("July 2026 (starts Wednesday): 5 Sunday-start weeks, Jun 28 → Aug 1", () => {
    const weeks = monthGrid(2026, 7);
    expect(weeks.length).toBe(5);
    expect(weeks[0][0]).toBe("2026-06-28");
    expect(weeks[4][6]).toBe("2026-08-01");
    expect(weeks.every((w) => w.length === 7)).toBe(true);
  });

  it("February 2026 (starts Sunday, 28 days): exactly 4 weeks, no spill", () => {
    const weeks = monthGrid(2026, 2);
    expect(weeks.length).toBe(4);
    expect(weeks[0][0]).toBe("2026-02-01");
    expect(weeks[3][6]).toBe("2026-02-28");
  });

  it("August 2026 (starts Saturday, 31 days): 6 weeks", () => {
    const weeks = monthGrid(2026, 8);
    expect(weeks.length).toBe(6);
    expect(weeks[0][0]).toBe("2026-07-26");
    expect(weeks[5][6]).toBe("2026-09-05");
  });

  it("every week starts on a Sunday", () => {
    for (const weeks of [monthGrid(2026, 7), monthGrid(2026, 12), monthGrid(2027, 1)]) {
      for (const w of weeks) expect(dowSunday0(w[0])).toBe(0);
    }
  });
});

describe("date arithmetic", () => {
  it("addDays crosses month and year boundaries", () => {
    expect(addDays("2026-07-31", 1)).toBe("2026-08-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
    expect(addDays("2026-02-28", 1)).toBe("2026-03-01"); // 2026 not a leap year
  });

  it("addMonths wraps years in both directions", () => {
    expect(addMonths(2026, 12, 1)).toEqual({ y: 2027, m: 1 });
    expect(addMonths(2026, 1, -1)).toEqual({ y: 2025, m: 12 });
    expect(addMonths(2026, 7, -19)).toEqual({ y: 2024, m: 12 });
  });

  it("weekOf returns the Sunday-start week containing the date", () => {
    // 2026-07-14 is a Tuesday → week is Jul 12 (Sun) .. Jul 18 (Sat)
    const w = weekOf("2026-07-14");
    expect(w[0]).toBe("2026-07-12");
    expect(w[6]).toBe("2026-07-18");
    expect(w).toContain("2026-07-14");
  });
});

describe("event shaping", () => {
  const events = [
    { id: "a", date: "2026-07-10", status: "open" },
    { id: "b", date: "2026-07-10", status: "done" },
    { id: "c", date: "2026-07-14", status: "open" },
    { id: "d", date: "2026-07-20", status: "open" },
    { id: "e", date: "2026-07-01", status: "open" },
  ];

  it("groupByDate buckets by ISO date", () => {
    const by = groupByDate(events);
    expect(by["2026-07-10"].map((e) => e.id)).toEqual(["a", "b"]);
    expect(by["2026-07-20"].length).toBe(1);
  });

  it("agendaGroups: overdue excludes done, upcoming includes today", () => {
    const { overdue, upcoming } = agendaGroups(events, "2026-07-14");
    expect(overdue.map((e) => e.id)).toEqual(["e", "a"]); // date-ascending, no done
    expect(upcoming[0]).toEqual({ date: "2026-07-14", events: [events[2]] });
    expect(upcoming[1].date).toBe("2026-07-20");
  });

  it("agendaGroups tolerates empty/undated input", () => {
    expect(agendaGroups([], "2026-07-14")).toEqual({ overdue: [], upcoming: [] });
    expect(agendaGroups([{ id: "x" }], "2026-07-14").overdue).toEqual([]);
  });
});

describe("keyboard focus reducer", () => {
  it("arrows move by day and week", () => {
    expect(moveFocus("2026-07-14", "ArrowLeft")).toBe("2026-07-13");
    expect(moveFocus("2026-07-14", "ArrowRight")).toBe("2026-07-15");
    expect(moveFocus("2026-07-14", "ArrowUp")).toBe("2026-07-07");
    expect(moveFocus("2026-07-14", "ArrowDown")).toBe("2026-07-21");
  });

  it("unhandled keys return null", () => {
    expect(moveFocus("2026-07-14", "Enter")).toBeNull();
    expect(moveFocus("2026-07-14", "a")).toBeNull();
  });
});

describe("labels", () => {
  it("month title and short date", () => {
    expect(monthTitle(2026, 7)).toBe("July 2026");
    expect(shortDate("2026-07-04")).toBe("Jul 4");
  });
});
