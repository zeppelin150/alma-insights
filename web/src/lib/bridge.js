import { useEffect, useState } from "react";

// ONE QWebChannel per page, shared by every bridge consumer. Multiple client
// channels over a single qt.webChannelTransport interleave message ids and
// break — so the first caller opens the channel and everyone else awaits the
// same promise, then picks their object off channel.objects by name. Each
// host surface registers its objects Python-side ("almaBridge" for chat,
// "calendarBridge" / "workbenchBridge" for the tabs; a page may carry
// several). Absent transport or an unregistered name → null (the surface
// renders its waiting state).
let channelPromise = null;

function connectChannel() {
  if (channelPromise) return channelPromise;
  const transport = window.qt && window.qt.webChannelTransport;
  if (typeof window.QWebChannel === "undefined" || !transport) return null;
  channelPromise = new Promise((resolve) => {
    new window.QWebChannel(transport, (channel) => resolve(channel));
  });
  return channelPromise;
}

export function useBridge(name = "almaBridge") {
  const [bridge, setBridge] = useState(null);
  useEffect(() => {
    const pending = connectChannel();
    if (!pending) return;
    let alive = true;
    pending.then((channel) => {
      const obj = channel.objects[name];
      if (!obj || !alive) return;
      window[name] = obj; // expose under its registered name
      setBridge(obj);
    });
    return () => { alive = false; };
  }, [name]);
  return bridge;
}
