import React, { useEffect, useRef, useState } from "react";
import { useBridge } from "../lib/bridge.js";
import { normalizeData } from "./shape.js";
import { isDemoMode, buildDemoTask } from "./demo.js";
import Header from "./Header.jsx";
import FieldGrid from "./FieldGrid.jsx";
import Description from "./Description.jsx";
import Subtasks from "./Subtasks.jsx";
import AppsRow from "./AppsRow.jsx";
import Activity from "./Activity.jsx";
import "./task.css";

// Route root for #/task — the Asana task-detail mirror. Owns the bridge
// hookup and ALL local UI state (tabs, sort, collapse, hide-fields); every
// child is a pure renderer. Writes only relay ids/text to the bridge; the
// host validates against Python-held state.

export default function TaskApp() {
  const bridge = useBridge("taskBridge");
  const [vm, setVm] = useState(() => normalizeData(null));
  const [tab, setTab] = useState("comments");
  const [oldestFirst, setOldestFirst] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [fieldsHidden, setFieldsHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resolving, setResolving] = useState("");
  const [status, setStatus] = useState("");
  const [editingDesc, setEditingDesc] = useState(false);
  const prevIdRef = useRef("");

  useEffect(() => {
    window.__almaTaskMounted = true; // headless hook
  }, []);

  const demo = isDemoMode() && !bridge;
  useEffect(() => {
    if (!demo) return;
    const next = normalizeData(buildDemoTask().task_data);
    setVm(next);
    window.__almaTaskVm = next; // headless hook
    window.__almaTaskDemo = true;
  }, [demo]);

  useEffect(() => {
    if (!bridge) return;
    bridge.taskData.connect((j) => {
      try {
        const next = normalizeData(JSON.parse(j));
        if (next.task_id !== prevIdRef.current) {
          prevIdRef.current = next.task_id;
          setTab("comments");
          setOldestFirst(true);
          setExpanded(false);
          setFieldsHidden(false);
          // Editor state resets ONLY on task change. The background
          // reconcile silently re-opens the current task (WS1-M7) — a
          // same-task push must never stomp an operator mid-edit; the
          // uncontrolled textarea keeps their draft across the re-render.
          setEditingDesc(false);
        }
        setVm(next);
        setBusy(false);
        setResolving("");
        window.__almaTaskVm = next; // headless hook
      } catch (e) {}
    });
    if (bridge.statusText) {
      bridge.statusText.connect((t) => {
        setStatus(t || "");
        window.__almaTaskStatus = t; // headless hook
      });
    }
    if (bridge.actionResolved) {
      bridge.actionResolved.connect((j) => {
        try {
          const p = JSON.parse(j);
          setBusy(false);
          setResolving("");
          window.__almaTaskAction = p; // headless hook
        } catch (e) {}
      });
    }
    bridge.refresh();
    window.__almaTaskReady = true; // headless hook: all signals connected
  }, [bridge]);

  const call = (name, ...args) => {
    if (bridge && typeof bridge[name] === "function") bridge[name](...args);
  };
  const relay = (name, ...args) => {
    if (!bridge) return;
    setBusy(true);
    call(name, ...args);
  };

  const connected = bridge || demo;
  if (!connected) {
    return (
      <div className="app route-task tk-app">
        <div className="route-empty">
          <p>Waiting for the task bridge…</p>
          <p className="route-note">
            This route renders inside the app's task drilldown. Append{" "}
            <code>?demo</code> for sample data.
          </p>
        </div>
      </div>
    );
  }
  if (!vm.task_id) {
    return (
      <div className="app route-task tk-app">
        <div className="route-empty">
          <p>No task selected.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app route-task tk-app">
      <Header
        header={vm.header}
        capabilities={vm.capabilities}
        busy={busy}
        onToggleComplete={(done) => relay("toggleComplete", vm.task_id, done)}
        onRefresh={() => relay("refreshTask", vm.task_id)}
        onSetDue={(iso) => {
          if (iso && iso !== vm.header.due_iso) relay("setDue", vm.task_id, iso);
        }}
        onOpenUrl={(url) => call("openUrl", url)}
      />
      {status && <div className="tk-status">{status}</div>}
      <FieldGrid
        fields={vm.fields}
        hidden={fieldsHidden}
        onToggleHidden={() => setFieldsHidden(!fieldsHidden)}
      />
      <Description
        srcdoc={vm.description.srcdoc}
        markdown={vm.description.markdown}
        canEdit={vm.capabilities.description}
        editing={editingDesc}
        busy={busy}
        status={status}
        onEdit={() => setEditingDesc(true)}
        onCancel={() => setEditingDesc(false)}
        onSave={(md) => relay("updateDescription", vm.task_id, md)}
      />
      <Subtasks
        subtasks={vm.subtasks}
        capabilities={vm.capabilities}
        busy={busy}
        onAdd={(text) => relay("addSubtask", vm.task_id, text)}
        onToggle={(gid, done) => relay("toggleSubtask", vm.task_id, gid, done)}
        onOpen={(gid) => call("openSubtask", vm.task_id, gid)}
      />
      <AppsRow
        attachments={vm.attachments}
        resolving={resolving}
        onOpenAttachment={(gid) => {
          if (!bridge) return;
          setResolving(gid);
          call("openAttachment", gid);
        }}
      />
      <Activity
        stories={vm.stories}
        tab={tab}
        oldestFirst={oldestFirst}
        expanded={expanded}
        capabilities={vm.capabilities}
        busy={busy}
        onTab={setTab}
        onSort={() => setOldestFirst(!oldestFirst)}
        onExpand={() => setExpanded(true)}
        onPostComment={(text) => relay("postComment", vm.task_id, text)}
        onOpenUrl={(url) => call("openUrl", url)}
      />
    </div>
  );
}
