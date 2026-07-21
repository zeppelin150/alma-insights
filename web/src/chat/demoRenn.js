// Demo-mode stand-in for the almaBridge chat object (dev/preview only, used
// solely when ?demo is active AND no real bridge exists). Mimics the signal
// surface ChatDrawer consumes and answers every send with a canned reply, so
// the drawer can be seen without the app. Never ships into a real tab — in
// the app the real almaBridge always resolves first.
export function makeDemoRenn() {
  const handlers = {};
  const sig = (name) => ({ connect: (fn) => { handlers[name] = fn; } });
  return {
    responseReady: sig("response"),
    errorOccurred: sig("error"),
    busyChanged: sig("busy"),
    statusUpdate: sig("status"),
    tokenStreamed: sig("token"),
    toolCall: sig("tool"),
    chatNotice: sig("notice"),
    send(text) {
      if (handlers.busy) handlers.busy(true);
      setTimeout(() => {
        if (handlers.tool) {
          handlers.tool(JSON.stringify(
            { id: 1, name: "find_cards_to_update", rows: 3, ms: 41, ok: true }));
        }
      }, 350);
      setTimeout(() => {
        if (handlers.busy) handlers.busy(false);
        if (handlers.response) {
          handlers.response(
            "**(sample reply)** I'd tighten the intro and refresh the rollout " +
            "date — in the app I run tools against your drafts and cards. " +
            `You asked: _${text.slice(0, 80)}_`);
        }
      }, 900);
    },
  };
}
