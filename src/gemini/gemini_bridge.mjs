#!/usr/bin/env node
/**
 * gemini_bridge.mjs — Persistent Gemini CLI Streaming Bridge v4
 *
 * Boots the Gemini CLI's internal modules ONCE, then stays alive accepting
 * JSON prompts on stdin and returning JSON event streams on stdout.
 * Same auth, same API endpoint, same binary as the approved CLI.
 *
 * Two call modes:
 *   stream:true  → direct geminiClient.sendMessageStream() with token-by-token
 *                   streaming, heartbeats, stall detection, abort support
 *   stream:false → legacy runNonInteractive() wrapper (Phase 1 compat)
 *
 * USAGE:
 *   node gemini_bridge.mjs --test       # validate boot + single streaming call
 *   node gemini_bridge.mjs              # persistent stdin/stdout loop
 *
 * STDIN PROTOCOL (one JSON per line):
 *   {"id":"b1", "prompt":"...", "stream":true}   → streaming call
 *   {"id":"b1", "prompt":"...", "stream":false}   → legacy non-streaming call
 *   {"id":"b1", "prompt":"..."}                   → defaults to stream:true
 *   {"command":"abort", "id":"b1"}                → cancel in-flight call
 *   {"command":"ping"}
 *   {"command":"quit"}
 *
 * STDOUT PROTOCOL (one JSON per line):
 *   Streaming intermediate events:
 *     {"id":"b1", "type":"content",   "delta":"..."}
 *     {"id":"b1", "type":"heartbeat", "elapsed_ms":5000, "silence_ms":3200}
 *     {"id":"b1", "type":"tool_call", "name":"...", "call_id":"...", "args":{}}
 *     {"id":"b1", "type":"tool_result","name":"...", "call_id":"...", "status":"success"}
 *   Terminal events (exactly one per call):
 *     {"id":"b1", "type":"done",    "full_text":"...", "elapsed_ms":4200, "turns":1}
 *     {"id":"b1", "type":"error",   "error":"rate_limit", "message":"...", "recoverable":true, "bridge_healthy":true}
 *     {"id":"b1", "type":"stopped", "reason":"...", "message":"..."}
 *   Bridge-level fatal (bridge is dying):
 *     {"type":"bridge_fatal", "code":41, "reason":"auth_failure"}
 *
 * STDERR: [bridge] diagnostic/log messages
 */

import { createRequire } from 'module';
import { createInterface } from 'readline';
import { pathToFileURL } from 'url';
import path from 'path';

// ─── Resolve CLI install paths from global npm prefix ───
const globalRoot = path.join(
  process.env.APPDATA || path.join(process.env.HOME || process.env.USERPROFILE, '.npm-global'),
  'npm', 'node_modules'
);
const globalRequire = createRequire(path.join(globalRoot, '_bridge_virtual.js'));

const CLI_ROOT = path.dirname(
  globalRequire.resolve('@google/gemini-cli/dist/index.js')
);

// gemini-cli-core is nested inside the CLI's own node_modules
const cliRequire = createRequire(path.join(CLI_ROOT, '_bridge_virtual.js'));
const CORE_ROOT = path.dirname(
  cliRequire.resolve('@google/gemini-cli-core')
);

const log = (msg) => process.stderr.write(`[bridge] ${msg}\n`);

// ═══════════════════════════════════════════════════════════════════════════
// SMART EXIT GUARD — installed before any CLI modules load
// ═══════════════════════════════════════════════════════════════════════════
// Exit codes from @google/gemini-cli-core ExitCodes:
//   0   = SUCCESS                  → Block (one-shot completion)
//   41  = FATAL_AUTHENTICATION_ERROR → Allow (bridge must die, Python restarts)
//   42  = FATAL_INPUT_ERROR        → Block (per-call error, bridge stays alive)
//   52  = FATAL_CONFIG_ERROR       → Allow (bridge is broken)
//   130 = FATAL_CANCELLATION_ERROR → Block (user cancelled a call, not bridge-fatal)

const realExit = process.exit;
let exitAllowed = false;
let activeCallId = null; // set during streaming calls

// respond must use the real stdout.write — defined early for exit guard
const origWrite = process.stdout.write.bind(process.stdout);
const respond = (obj) => origWrite(JSON.stringify(obj) + '\n');

process.exit = (code) => {
  // Always allow our own explicit exits (quit command, test mode)
  if (exitAllowed) {
    realExit(code);
    return;
  }

  // Code 0: CLI thinks the one-shot is done. Always block.
  if (code === 0) {
    log(`Blocked exit(0) — normal completion, bridge stays alive`);
    return;
  }

  // Code 42: Input error — per-call, not bridge-fatal
  if (code === 42) {
    log(`Blocked exit(42) — input error, bridge stays alive`);
    if (activeCallId) {
      respond({
        id: activeCallId,
        type: 'error',
        error: 'invalid_request',
        message: `CLI input error (exit code 42)`,
        recoverable: false,
        bridge_healthy: true,
        elapsed_ms: 0,
      });
    }
    return;
  }

  // Code 130: Cancellation — per-call, not bridge-fatal
  if (code === 130) {
    log(`Blocked exit(130) — cancellation, bridge stays alive`);
    if (activeCallId) {
      respond({
        id: activeCallId,
        type: 'error',
        error: 'aborted_by_client',
        message: `CLI cancellation (exit code 130)`,
        recoverable: true,
        bridge_healthy: true,
        elapsed_ms: 0,
      });
    }
    return;
  }

  // Code 41: Auth failure — bridge is unusable, must restart
  if (code === 41) {
    log(`FATAL: exit(41) — auth failure, bridge must die`);
    if (activeCallId) {
      respond({
        id: activeCallId,
        type: 'error',
        error: 'auth_expired',
        message: `CLI auth failure (exit code 41)`,
        recoverable: false,
        bridge_healthy: false,
        elapsed_ms: 0,
      });
    }
    respond({ type: 'bridge_fatal', code: 41, reason: 'auth_failure' });
    exitAllowed = true;
    realExit(41);
    return;
  }

  // Code 52: Config error — bridge is broken
  if (code === 52) {
    log(`FATAL: exit(52) — config error, bridge must die`);
    if (activeCallId) {
      respond({
        id: activeCallId,
        type: 'error',
        error: 'config_error',
        message: `CLI config error (exit code 52)`,
        recoverable: false,
        bridge_healthy: false,
        elapsed_ms: 0,
      });
    }
    respond({ type: 'bridge_fatal', code: 52, reason: 'config_error' });
    exitAllowed = true;
    realExit(52);
    return;
  }

  // Any other non-zero exit: assume bridge-fatal, let it die
  log(`FATAL: exit(${code}) — unclassified fatal error, bridge must die`);
  if (activeCallId) {
    respond({
      id: activeCallId,
      type: 'error',
      error: 'bridge_fatal',
      message: `CLI exited with unclassified code ${code}`,
      recoverable: false,
      bridge_healthy: false,
      elapsed_ms: 0,
    });
  }
  respond({ type: 'bridge_fatal', code, reason: 'unknown' });
  exitAllowed = true;
  realExit(code);
};

// Intercept exit/beforeExit event listeners added by CLI internals
const origOn = process.on.bind(process);
const origOnce = process.once.bind(process);
process.on = (evt, fn) => {
  if (evt === 'exit' || evt === 'beforeExit') {
    log(`Intercepted process.on('${evt}') — skipping`);
    return process;
  }
  return origOn(evt, fn);
};
process.once = (evt, fn) => {
  if (evt === 'exit' || evt === 'beforeExit') {
    log(`Intercepted process.once('${evt}') — skipping`);
    return process;
  }
  return origOnce(evt, fn);
};

// ═══════════════════════════════════════════════════════════════════════════
// STDOUT CAPTURE (for legacy non-streaming calls only)
// ═══════════════════════════════════════════════════════════════════════════
let capturing = false;
let captured = '';

process.stdout.write = (chunk, encoding, cb) => {
  if (capturing) {
    captured += typeof chunk === 'string' ? chunk : chunk.toString();
    if (typeof encoding === 'function') encoding(); else if (typeof cb === 'function') cb();
    return true;
  }
  return origWrite(chunk, encoding, cb);
};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE IMPORTS
// ═══════════════════════════════════════════════════════════════════════════
const imp = (rel) => import(pathToFileURL(path.join(CLI_ROOT, rel)).href);

log('Loading CLI modules...');

const { loadSettings } = await imp('src/config/settings.js');
const { loadCliConfig, parseArguments } = await imp('src/config/config.js');
const { initializeApp } = await imp('src/core/initializer.js');
const { validateNonInteractiveAuth } = await imp('src/validateNonInterActiveAuth.js');
const { runNonInteractive } = await imp('src/nonInteractiveCli.js');

const core = await import(pathToFileURL(path.join(CORE_ROOT, 'index.js')).href);
const {
  sessionId,
  GeminiEventType,
  Scheduler,
  ROOT_SCHEDULER_ID,
  recordToolCallInteractions,
  debugLogger,
  ToolErrorType,
} = core;

log('Modules loaded. Starting boot sequence...');

// ═══════════════════════════════════════════════════════════════════════════
// BOOT SEQUENCE — replicates gemini.js main()
// ═══════════════════════════════════════════════════════════════════════════
const bootStart = Date.now();

// 1. Load settings
const settings = loadSettings();
log(`Settings loaded. Auth type: ${settings.merged?.security?.auth?.selectedType || 'none'}`);

// 2. Parse arguments — fake process.argv for non-interactive mode
//    Read --model from real argv (passed by Python bridge wrapper)
const modelArgIdx = process.argv.indexOf('--model');
const modelOverride = modelArgIdx >= 0 ? process.argv[modelArgIdx + 1] : null;
if (modelOverride) log(`Model override from settings: ${modelOverride}`);

const originalArgv = process.argv;
const fakeArgv = ['node', 'gemini', '--prompt', '__bridge_init__'];
if (modelOverride) fakeArgv.push('--model', modelOverride);
process.argv = fakeArgv;
const argv = await parseArguments(settings.merged);
process.argv = originalArgv;
log('Arguments parsed.');

// 3. Load CLI config
const config = await loadCliConfig(settings.merged, sessionId, argv, {
  projectHooks: settings.workspace?.settings?.hooks,
});
log('Config loaded.');

// 4. Initialize storage
await config.storage.initialize();
log('Storage initialized.');

// 5. Initialize app (extensions, tools, etc.)
await initializeApp(config, settings);
log('App initialized.');

// 6. Authenticate
try {
  const authType = await validateNonInteractiveAuth(
    settings.merged.security.auth.selectedType,
    settings.merged.security.auth.useExternal,
    config,
    settings
  );
  await config.refreshAuth(authType);
  log(`Auth complete. Type: ${authType}`);
} catch (e) {
  log(`Auth warning: ${e.message}`);
}

// 7. Initialize config (loads model, etc.)
try {
  await config.initialize();
  log('Config.initialize() complete.');
} catch (e) {
  log(`Config.initialize() warning: ${e.message}`);
}

const bootMs = Date.now() - bootStart;
log(`Boot complete in ${bootMs}ms.`);

// ═══════════════════════════════════════════════════════════════════════════
// STDIN PROTECTION
// ═══════════════════════════════════════════════════════════════════════════
process.stdin.destroy = () => {
  log('Blocked stdin.destroy()');
  return process.stdin;
};
if (process.stdin.end) {
  process.stdin.end = () => {
    log('Blocked stdin.end()');
    return process.stdin;
  };
}

// Keepalive — prevent Node from exiting if all handles close
const keepalive = setInterval(() => {}, 60_000);

// ═══════════════════════════════════════════════════════════════════════════
// ERROR CLASSIFICATION
// ═══════════════════════════════════════════════════════════════════════════

function classifyError(error) {
  const msg = error?.message || String(error);
  const status = error?.status || error?.httpStatus;

  if (status === 429 || msg.includes('RESOURCE_EXHAUSTED') || msg.includes('quota'))
    return { error: 'rate_limit', recoverable: true, bridge_healthy: true };
  if (status === 503 || status === 502 || msg.includes('UNAVAILABLE'))
    return { error: 'server_error', recoverable: true, bridge_healthy: true };
  if (msg.includes('ETIMEDOUT') || msg.includes('ESOCKETTIMEDOUT'))
    return { error: 'network_timeout', recoverable: true, bridge_healthy: true };
  if (msg.includes('ECONNRESET') || msg.includes('ECONNREFUSED'))
    return { error: 'connection_reset', recoverable: true, bridge_healthy: true };
  if (status === 401 || msg.includes('UNAUTHENTICATED'))
    return { error: 'auth_expired', recoverable: false, bridge_healthy: false };
  if (status === 403 || msg.includes('PERMISSION_DENIED'))
    return { error: 'forbidden', recoverable: false, bridge_healthy: false };
  if (status === 400 || msg.includes('INVALID_ARGUMENT'))
    return { error: 'invalid_request', recoverable: false, bridge_healthy: true };
  if (msg.includes('model') && msg.includes('not found'))
    return { error: 'model_not_found', recoverable: false, bridge_healthy: false };
  if (msg.includes('aborted') || msg.includes('cancelled'))
    return { error: 'aborted_by_client', recoverable: true, bridge_healthy: true };

  return { error: 'unknown', recoverable: false, bridge_healthy: true, raw: msg.slice(0, 500) };
}

// ═══════════════════════════════════════════════════════════════════════════
// ACTIVE CALLS TRACKING (for abort support)
// ═══════════════════════════════════════════════════════════════════════════

const activeCalls = new Map(); // id → { abortController, timers: [] }

// ═══════════════════════════════════════════════════════════════════════════
// STREAMING CALL — direct geminiClient.sendMessageStream()
// ═══════════════════════════════════════════════════════════════════════════

const HEARTBEAT_MS = 5000;
const STALL_MS = 90000;  // 90s — large synthesis prompts need extended think time

async function callGeminiStreaming(requestId, prompt) {
  const geminiClient = config.getGeminiClient();
  const scheduler = new Scheduler({
    config,
    messageBus: config.getMessageBus(),
    getPreferredEditor: () => undefined,
    schedulerId: ROOT_SCHEDULER_ID,
  });

  const abortController = new AbortController();
  const prompt_id = Math.random().toString(16).slice(2);
  const startTime = Date.now();

  // Register for abort support
  const timers = [];
  activeCalls.set(requestId, { abortController, timers });
  activeCallId = requestId;

  // ─── Monitoring: Heartbeat ───
  let lastEventTime = Date.now();
  const heartbeatTimer = setInterval(() => {
    respond({
      id: requestId,
      type: 'heartbeat',
      elapsed_ms: Date.now() - startTime,
      silence_ms: Date.now() - lastEventTime,
    });
  }, HEARTBEAT_MS);
  timers.push(heartbeatTimer);

  // ─── Monitoring: Stall Detection ───
  let lastContentTime = Date.now();
  let inToolExecution = false;
  const stallTimer = setInterval(() => {
    if (inToolExecution) return; // tools can be slow, don't stall-check
    if (Date.now() - lastContentTime > STALL_MS) {
      abortController.abort();
      respond({
        id: requestId,
        type: 'error',
        error: 'stall_timeout',
        message: `No content received for ${STALL_MS}ms`,
        elapsed_ms: Date.now() - startTime,
        recoverable: true,
        bridge_healthy: true,
      });
    }
  }, 5000);
  timers.push(stallTimer);

  // ─── Cleanup ───
  const cleanup = () => {
    for (const t of timers) clearInterval(t);
    activeCalls.delete(requestId);
    activeCallId = null;
  };

  let currentParts = [{ text: prompt }];
  let turnCount = 0;
  let totalText = '';

  try {
    while (true) {
      turnCount++;
      const toolCallRequests = [];

      const responseStream = geminiClient.sendMessageStream(
        currentParts,
        abortController.signal,
        prompt_id,
        undefined,
        false,
        turnCount === 1 ? prompt : undefined
      );

      for await (const event of responseStream) {
        lastEventTime = Date.now();

        if (abortController.signal.aborted) {
          // Already reported via abort handler or stall timer
          return;
        }

        if (event.type === GeminiEventType.Content) {
          const text = event.value;
          totalText += text;
          lastContentTime = Date.now();

          respond({
            id: requestId,
            type: 'content',
            delta: text,
          });
        }
        else if (event.type === GeminiEventType.ToolCallRequest) {
          respond({
            id: requestId,
            type: 'tool_call',
            name: event.value.name,
            call_id: event.value.callId,
            args: event.value.args,
          });
          toolCallRequests.push(event.value);
        }
        else if (event.type === GeminiEventType.Error) {
          const classified = classifyError(event.value.error || event.value);
          respond({
            id: requestId,
            type: 'error',
            ...classified,
            message: event.value.error?.message || String(event.value),
            elapsed_ms: Date.now() - startTime,
          });
          return;
        }
        else if (event.type === GeminiEventType.AgentExecutionStopped) {
          respond({
            id: requestId,
            type: 'stopped',
            reason: event.value.reason,
            message: event.value.systemMessage,
            elapsed_ms: Date.now() - startTime,
          });
          return;
        }
        else if (event.type === GeminiEventType.AgentExecutionBlocked) {
          respond({
            id: requestId,
            type: 'error',
            error: 'agent_blocked',
            message: event.value.reason,
            recoverable: false,
            bridge_healthy: true,
            elapsed_ms: Date.now() - startTime,
          });
          return;
        }
        else if (event.type === GeminiEventType.LoopDetected) {
          respond({
            id: requestId,
            type: 'error',
            error: 'loop_detected',
            message: 'Infinite loop detected, stopping execution',
            recoverable: false,
            bridge_healthy: true,
            elapsed_ms: Date.now() - startTime,
          });
          return;
        }
        else if (event.type === GeminiEventType.MaxSessionTurns) {
          respond({
            id: requestId,
            type: 'error',
            error: 'max_turns',
            message: 'Maximum session turns exceeded',
            recoverable: false,
            bridge_healthy: true,
            elapsed_ms: Date.now() - startTime,
          });
          return;
        }
      }

      // No tool calls = model is done
      if (toolCallRequests.length === 0) {
        // Best-effort token reporting (5.2)
        // Estimate tokens from char count — CLI doesn't expose usageMetadata
        const estInputTokens = Math.round(prompt.length / 4);
        const estOutputTokens = Math.round(totalText.length / 4);

        respond({
          id: requestId,
          type: 'done',
          full_text: totalText,
          elapsed_ms: Date.now() - startTime,
          turns: turnCount,
          input_tokens: estInputTokens,
          output_tokens: estOutputTokens,
        });
        return;
      }

      // Execute tool calls
      inToolExecution = true;
      const completedToolCalls = await scheduler.schedule(
        toolCallRequests,
        abortController.signal
      );
      inToolExecution = false;
      lastContentTime = Date.now(); // reset stall timer after tools

      const toolResponseParts = [];
      for (const completed of completedToolCalls) {
        respond({
          id: requestId,
          type: 'tool_result',
          name: completed.request.name,
          call_id: completed.request.callId,
          status: completed.status === 'error' ? 'error' : 'success',
          output: typeof completed.response.resultDisplay === 'string'
            ? completed.response.resultDisplay?.slice(0, 500) : undefined,
        });

        if (completed.response.responseParts) {
          toolResponseParts.push(...completed.response.responseParts);
        }

        // Check for stop-execution tool
        if (completed.response.errorType === ToolErrorType.STOP_EXECUTION) {
          respond({
            id: requestId,
            type: 'stopped',
            reason: 'tool_stop_execution',
            message: completed.response.error?.message,
            elapsed_ms: Date.now() - startTime,
          });
          return;
        }
      }

      // Record tool interactions (non-blocking, best-effort)
      try {
        const currentModel = geminiClient.getCurrentSequenceModel?.() ?? config.getModel();
        geminiClient.getChat?.().recordCompletedToolCalls?.(currentModel, completedToolCalls);
        await recordToolCallInteractions(config, completedToolCalls);
      } catch (e) {
        log(`Tool recording warning: ${e.message}`);
      }

      currentParts = toolResponseParts;
    }
  } catch (e) {
    // Catch-all for unexpected errors
    if (!abortController.signal.aborted) {
      const classified = classifyError(e);
      respond({
        id: requestId,
        type: 'error',
        ...classified,
        message: e.message,
        elapsed_ms: Date.now() - startTime,
      });
    }
  } finally {
    cleanup();
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// LEGACY NON-STREAMING CALL (Phase 1 backward compat)
// ═══════════════════════════════════════════════════════════════════════════

async function callGeminiLegacy(prompt) {
  const prompt_id = Math.random().toString(16).slice(2);

  captured = '';
  capturing = true;

  // Prevent runNonInteractive from hijacking stdin.
  const realIsTTY = process.stdin.isTTY;
  Object.defineProperty(process.stdin, 'isTTY', { value: false, configurable: true });

  try {
    await runNonInteractive({
      config,
      settings,
      input: prompt,
      prompt_id,
      resumedSessionData: undefined,
    });
  } catch (e) {
    log(`runNonInteractive threw: ${e.message}`);
  } finally {
    capturing = false;
    Object.defineProperty(process.stdin, 'isTTY', { value: realIsTTY, configurable: true });
    process.stdin.resume();
  }

  return captured.trim();
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST MODE
// ═══════════════════════════════════════════════════════════════════════════

if (process.argv.includes('--test')) {
  const testStreaming = !process.argv.includes('--legacy');

  if (testStreaming) {
    log('=== TEST: streaming API call ===');
    const testId = 'test_stream';
    // Collect events to summarize
    const events = [];
    const origRespond = respond;

    // Temporarily intercept respond to collect + forward
    const testStart = Date.now();
    await callGeminiStreaming(testId, 'Respond with exactly one word: ok');

    // callGeminiStreaming already sent events via respond(). Summarize.
    log(`Streaming test complete (${Date.now() - testStart}ms). Check stdout for events.`);
  } else {
    log('=== TEST: legacy (non-streaming) API call ===');
    const start = Date.now();
    try {
      const result = await callGeminiLegacy('Respond with exactly one word: ok');
      log(`Response (${Date.now() - start}ms): "${result.slice(0, 200)}"`);
      respond({ test: 'success', response: result, elapsed_ms: Date.now() - start, boot_ms: bootMs });
    } catch (e) {
      log(`Call failed: ${e.message}`);
      respond({ test: 'failed', error: e.message, boot_ms: bootMs });
    }
  }

  exitAllowed = true;
  realExit(0);
}

// ═══════════════════════════════════════════════════════════════════════════
// PERSISTENT LOOP
// ═══════════════════════════════════════════════════════════════════════════

const startTime = Date.now();
let callCount = 0;
log('Bridge ready. Send JSON on stdin.');

const rl = createInterface({ input: process.stdin });

rl.on('line', async (line) => {
  let req;
  try { req = JSON.parse(line); } catch { respond({ error: 'Invalid JSON' }); return; }

  // ─── Commands ───

  if (req.command === 'quit') {
    log(`Quit after ${callCount} calls, uptime ${Date.now() - startTime}ms`);
    clearInterval(keepalive);
    exitAllowed = true;
    realExit(0);
  }

  if (req.command === 'ping') {
    respond({
      id: req.id || 'ping',
      status: 'alive',
      uptime_ms: Date.now() - startTime,
      calls: callCount,
      boot_ms: bootMs,
      active_calls: activeCalls.size,
    });
    return;
  }

  if (req.command === 'abort') {
    const active = activeCalls.get(req.id);
    if (active) {
      active.abortController.abort();
      // Terminal error event will be sent by the streaming loop or stall handler
      log(`Abort sent for call ${req.id}`);
      respond({
        id: req.id,
        type: 'error',
        error: 'aborted_by_client',
        message: 'Aborted by client request',
        recoverable: true,
        bridge_healthy: true,
        elapsed_ms: 0,
      });
    } else {
      respond({ id: req.id, type: 'error', error: 'no_active_call', message: `No active call with id ${req.id}` });
    }
    return;
  }

  // ─── Prompt calls ───

  if (!req.prompt) {
    respond({ id: req.id, error: 'Missing prompt field' });
    return;
  }

  callCount++;
  const useStreaming = req.stream !== false; // default to streaming

  if (useStreaming) {
    // Streaming call — events are sent directly by callGeminiStreaming
    await callGeminiStreaming(req.id || `call_${callCount}`, req.prompt);
  } else {
    // Legacy non-streaming call
    const callStart = Date.now();
    try {
      activeCallId = req.id || `call_${callCount}`;
      const result = await callGeminiLegacy(req.prompt);
      respond({
        id: req.id || `call_${callCount}`,
        response: result,
        elapsed_ms: Date.now() - callStart,
      });
    } catch (e) {
      respond({
        id: req.id || `call_${callCount}`,
        error: e.message,
        elapsed_ms: Date.now() - callStart,
      });
    } finally {
      activeCallId = null;
    }
  }
});

rl.on('close', () => {
  // Don't exit — stdin may have been "closed" by runNonInteractive internals.
  // The keepalive timer keeps the process alive. Only quit on explicit command.
  log('rl close event (ignored — bridge stays alive)');
});
