import { useEffect, useState } from "react";
import ChatApp from "./chat/ChatApp.jsx";
import CalendarApp from "./calendar/CalendarApp.jsx";
import WorkbenchApp from "./workbench/WorkbenchApp.jsx";
import HomeApp from "./home/HomeApp.jsx";
import ZendeskApp from "./zendesk/ZendeskApp.jsx";
import TaskApp from "./task/TaskApp.jsx";

// Hash router for the single-file SPA. Each WebHost loads the same
// dist/index.html with its own fragment: no hash (the Agent page, pre-router
// bundles included) → chat; "#/calendar" / "#/workbench" → the enablement tabs;
// "#/home" → the app-level Home page (ui.web_home).
function routeFromHash() {
  const h = (window.location.hash || "").replace(/^#\/?/, "").toLowerCase();
  if (h.startsWith("calendar")) return "calendar";
  if (h.startsWith("workbench")) return "workbench";
  if (h.startsWith("home")) return "home";
  if (h.startsWith("zendesk")) return "zendesk";
  if (h.startsWith("task")) return "task";
  return "chat";
}

export default function App() {
  const [route, setRoute] = useState(routeFromHash);
  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    window.__almaRoute = route; // headless hook
  }, [route]);
  if (route === "calendar") return <CalendarApp />;
  if (route === "workbench") return <WorkbenchApp />;
  if (route === "home") return <HomeApp />;
  if (route === "zendesk") return <ZendeskApp />;
  if (route === "task") return <TaskApp />;
  return <ChatApp />;
}
