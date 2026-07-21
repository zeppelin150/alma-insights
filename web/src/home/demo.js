// Sample viewmodel for the Home route (#/home?demo) — dev/preview only.
// Engages ONLY when the flag is present AND no bridge exists, and the header
// carries a DEMO badge so a screenshot can never pass as live state.

export function isDemoMode() {
  return (window.location.hash || "").toLowerCase().includes("demo");
}

export function buildDemoHome() {
  return {
    mode: "product",
    greeting: "Good afternoon",
    subtitle: "Choose a workspace, or pick up where you left off.",
    empty_activity:
      "No recent activity yet — run a search or scan to get started.",
    tiles: [
      {
        key: "product",
        title: "Product",
        desc: "Full analytics suite — conversations, TRC analytics, trending, incidents, and AI reporting.",
        icon: "pie",
        active: true,
      },
      {
        key: "enablement",
        title: "Enablement",
        desc: "Lightweight workbench — calendar, tasks, Guru card drafting, and the Renn assistant. Skips the heavy analytics stack.",
        icon: "pen",
        active: false,
      },
    ],
    stats: [
      { label: "Tickets", caption: "TICKETS", value: 48213, display: "48,213", available: true },
      { label: "Reports", caption: "REPORTS", value: 27, display: "27", available: true },
      { label: "Chats", caption: "CHATS", value: 9, display: "9", available: true },
    ],
    quick_actions: [
      { key: "search", label: "Run a search", icon: "chat" },
      { key: "reports", label: "Open AI Reports", icon: "doc" },
    ],
    activity: [
      {
        kind: "Report",
        title: "TRC Analytics — eligibility denials trending up across three payers",
        ts_display: "2026-07-20  09:12",
        icon: "doc",
        tint_bg: "#F3E8FF",
        tint_fg: "#6D28D9",
      },
      {
        kind: "Chat",
        title: "Why did TRC-140 spike on Tuesday?",
        ts_display: "2026-07-19  16:40",
        icon: "chat",
        tint_bg: "#E8F0FE",
        tint_fg: "#1D4ED8",
      },
      {
        kind: "Task",
        title: "Draft: prior-auth escalation card",
        ts_display: "2026-07-19  11:05",
        icon: "tasks",
        tint_bg: "#E5F3EC",
        tint_fg: "#0D7D72",
      },
    ],
  };
}
