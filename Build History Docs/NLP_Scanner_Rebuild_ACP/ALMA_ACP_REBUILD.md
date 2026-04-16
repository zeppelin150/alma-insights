# ALMA NLP Scanner — ACP Rebuild Specification

## Context

The ALMA Insights NLP ticket classification pipeline was previously built by decomposing the Gemini CLI bundle — importing internal classes (`BaseDeclarativeTool`, `BaseToolInvocation`, `GeminiEventType`), registering custom tools into the CLI's internal tool registry, and intercepting the streaming event pipeline via a custom JS bridge (`gemini_bridge.mjs`). This approach violates the Gemini CLI Terms of Service updated March 23, 2026, which explicitly prohibits third-party software from accessing the backing Gemini Code Assist service.

This document specifies the rebuild using the CLI's official **ACP (Agent Client Protocol)** mode (`gemini --acp`), which communicates over **stdio via JSON-RPC 2.0** and is ToS-compliant.

---

## Architecture: Before vs After

### BEFORE (ToS Violation — Decommissioned)
```
Python orchestrator
  → gemini_bridge.mjs (custom JS client built from CLI internals)
    → Injected StoreClassificationTool into CLI tool registry
    → Intercepted GeminiEventType.ToolCallRequest events
    → NDJSON bridge to Python over stdout
  → worker_agent.py consumed tool_call / tool_result events
  → tool_registry.py wrote to SQLite
```

### AFTER (ACP — ToS Compliant)
```
Python orchestrator
  → Spawns N gemini --acp subprocesses (pool size: 32)
  → JSON-RPC 2.0 over stdin/stdout per subprocess
  → Prompt requests NDJSON output (or MCP tool calls)
  → Python validates with pydantic
  → Writes to SQLite
```

---

## Validated Performance Benchmarks (PowerShell, April 6 2026)

### Concurrency Scaling — Trivial Prompts
```
n=1   time=3.09s   errors=0  rate_limits=0
n=2   time=3.16s   errors=0  rate_limits=0
n=4   time=3.63s   errors=0  rate_limits=0
n=8   time=4.80s   errors=0  rate_limits=0
n=12  time=11.36s  errors=0  rate_limits=0
n=16  time=7.86s   errors=0  rate_limits=0
```

### Extended Concurrency — Finding the Ceiling
```
n=16  time=19.94s  errors=0  rate_limits=0
n=24  time=23.79s  errors=0  rate_limits=0
n=32  time=18.38s  errors=0  rate_limits=0
n=48  time=57.47s  errors=0  rate_limits=0  ← SOFT THROTTLE
n=64  time=39.41s  errors=0  rate_limits=0
n=72  time=37.60s  errors=0  rate_limits=0
```

### Conclusions
- **Production pool size: 32** — flat scaling from n=16 to n=32 (~18-24s band)
- **Soft throttle begins at n=48** — 3x time spike, no explicit errors
- **No hard rate limits observed up to n=72** with trivial prompts
- **Real payload testing still needed** — 45 tickets × ~1500 words = ~100k+ tokens per chunk

### Estimated Production Throughput
- Chunks per run: 22–35 (45 tickets/chunk, ~1000-2000 words/ticket)
- Pool size: 32
- Rounds: 1 (≤32 chunks) or 2 (33-35 chunks)
- Estimated wall time: **2-6 minutes** for 1000 tickets (pending real payload validation)
- Previous benchmark: 15-20 minutes via decomposed bridge

---

## ACP Protocol Reference

### Transport
- **Protocol**: JSON-RPC 2.0 over stdio (stdin/stdout)
- **Launch**: `gemini --acp`
- **No HTTP server, no port binding** — pure subprocess IPC

### Handshake Sequence
```json
// 1. Initialize
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
  "clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true},"terminal":true},
  "protocolVersion":1
}}

// 2. New session
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{
  "cwd":"C:\\path\\to\\workdir"
}}

// 3. Send prompt
{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{
  "prompt":"<classification prompt here>"
}}
```

### Known Gotchas
1. **`protocolVersion` must be a number** (not string) — Zod validation rejects `"2024-11-05"`
2. **CLI leaks plain text to stdout** (e.g., "Loaded cached credentials.") — filter non-JSON lines
3. **OAuth login prompt when spawned as subprocess** — use API key auth or pre-auth boot sequence
4. **MCP server integration**: Pass MCP config in `session/new` params for tool-call pattern

---

## Component Rebuild Plan

### 1. ACP Process Pool Manager
**New file**: `src/agents/acp_pool.py`

Replaces: `gemini_bridge.mjs`, `gemini_bridge_wrapper.py`

```python
"""
Manages a pool of gemini --acp subprocesses.
Each subprocess is an independent authenticated session.
"""

class ACPInstance:
    """Single ACP subprocess lifecycle."""
    
    async def start(self):
        """Spawn gemini --acp, send initialize + session/new."""
        self.proc = await asyncio.create_subprocess_exec(
            'gemini', '--acp',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # suppress non-JSON noise
        )
        await self._send_initialize()
        await self._send_session_new()
    
    async def send_prompt(self, prompt: str) -> list[dict]:
        """Send classification prompt, return parsed JSON lines."""
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "session/prompt",
            "params": {"prompt": prompt}
        })
        self.proc.stdin.write((msg + "\n").encode())
        await self.proc.stdin.drain()
        return await self._read_response()
    
    async def _read_response(self) -> list[dict]:
        """Read stdout line-by-line, filter non-JSON, collect results."""
        results = []
        async for line in self.proc.stdout:
            line = line.decode().strip()
            try:
                data = json.loads(line)
                results.append(data)
                if self._is_terminal(data):
                    break
            except json.JSONDecodeError:
                continue  # skip "Loaded cached credentials." etc.
        return results

class ACPPool:
    """Pool of N ACP instances for concurrent chunk processing."""
    
    def __init__(self, pool_size: int = 32):
        self.pool_size = pool_size
        self.instances: list[ACPInstance] = []
    
    async def start_pool(self):
        """Boot all instances, run auth handshake."""
        self.instances = [ACPInstance() for _ in range(self.pool_size)]
        await asyncio.gather(*[inst.start() for inst in self.instances])
    
    async def process_chunks(self, chunks: list[str]) -> list[list[dict]]:
        """Dispatch chunks across pool, return all results."""
        semaphore = asyncio.Semaphore(self.pool_size)
        
        async def _process_one(instance, chunk):
            async with semaphore:
                return await instance.send_prompt(chunk)
        
        tasks = []
        for i, chunk in enumerate(chunks):
            instance = self.instances[i % self.pool_size]
            tasks.append(_process_one(instance, chunk))
        
        return await asyncio.gather(*tasks)
    
    async def shutdown(self):
        """Terminate all subprocesses."""
        for inst in self.instances:
            inst.proc.terminate()
```

### 2. Prompt Template (Modified)
**File**: `config/prompts/nlp_classify.txt`

Change: Remove tool-call instruction. Request NDJSON output directly.

```
You are classifying support tickets for an RCM healthcare platform.
All tickets in this batch belong to the same Ticket Reason Code (TRC):

TRC: {trc_path}

STATISTICAL CONTEXT FOR THIS TRC:
{statistical_context}

{sub_taxonomy_section}

MANIFEST:
- Tickets in this batch: {n_tickets}
- Comments: {n_comments}
- Date range: {date_start} to {date_end}
- Chunk: {chunk_n} of {chunk_total}

TICKETS:
{ticket_jsonl}

OUTPUT FORMAT:
Emit one JSON object per line (NDJSON) for EACH ticket. No markdown fencing.
No preamble. No explanation. No text between JSON lines.

Each JSON object MUST contain:
{
  "ticket_id": "<ticket_id>",
  "sub_cluster": "<behavioral sub-pattern, <8 words>",
  "sub_cluster_confidence": <0.0-1.0>,
  "is_novel": <true|false>,
  "novel_justification": "<why this is genuinely new, or null>",
  "sentiment_intensity": <1-5>,
  "sentiment_polarity": "<positive|negative|mixed|neutral>",
  "friction_type": "<one of: access_blocked, self_serve_failure, ...>",
  "anomaly_flag": "<normal|unusual|critical>",
  "anomaly_reason": "<why flagged, or null>",
  "entities": {"payer": null, "product_area": null, "feature": null},
  "key_phrases": ["<phrase1>", "<phrase2>"],
  "root_cause_hint": "<one sentence hypothesis>",
  "summary": "<1-2 sentence de-identified summary>"
}

You MUST output exactly {n_tickets} JSON lines. One per ticket. No exceptions.
```

### 3. Response Parser + Validator
**New file**: `src/agents/acp_parser.py`

Replaces: `bridge_tools.mjs` (no-op tool), `worker_agent.py` tool_call/tool_result handling

```python
"""
Parses NDJSON classification output from ACP responses.
Validates against schema using pydantic.
"""
from pydantic import BaseModel, Field, validator
from typing import Optional
import json

class TicketClassification(BaseModel):
    ticket_id: str
    sub_cluster: str = Field(max_length=80)
    sub_cluster_confidence: float = Field(ge=0.0, le=1.0)
    is_novel: bool
    novel_justification: Optional[str] = None
    sentiment_intensity: int = Field(ge=1, le=5)
    sentiment_polarity: str  # positive|negative|mixed|neutral
    friction_type: str
    anomaly_flag: str  # normal|unusual|critical
    anomaly_reason: Optional[str] = None
    entities: dict
    key_phrases: list[str]
    root_cause_hint: str
    summary: str

    @validator('sentiment_polarity')
    def validate_polarity(cls, v):
        assert v in ('positive', 'negative', 'mixed', 'neutral')
        return v

    @validator('anomaly_flag')
    def validate_anomaly(cls, v):
        assert v in ('normal', 'unusual', 'critical')
        return v

def parse_acp_response(response_lines: list[dict]) -> list[TicketClassification]:
    """
    Extract classification NDJSON from ACP JSON-RPC response.
    ACP wraps model output in session/update notifications.
    """
    classifications = []
    text_buffer = ""
    
    for msg in response_lines:
        # ACP streams model output as session/update notifications
        if msg.get("method") == "session/update":
            content = msg.get("params", {}).get("content", "")
            text_buffer += content
    
    # Parse accumulated text as NDJSON
    for line in text_buffer.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            classification = TicketClassification(**data)
            classifications.append(classification)
        except (json.JSONDecodeError, Exception) as e:
            # Log malformed line, continue parsing
            logger.warning(f"Failed to parse classification line: {e}")
            continue
    
    return classifications
```

### 4. SQLite Writer
**File**: `src/agents/tool_registry.py`

No changes needed. The `_tool_store_classification` method accepts a dict and writes to SQLite. The pydantic models from `acp_parser.py` serialize to the same dict shape.

```python
# Existing code works as-is:
for classification in parsed_classifications:
    tool_registry._tool_store_classification(classification.dict())
```

### 5. Orchestrator
**Modified file**: `src/agents/scan_orchestrator.py` (or equivalent)

```python
"""
Top-level orchestration: chunk → dispatch → collect → store.
"""

async def run_scan(tickets: list[dict], config: ScanConfig):
    # 1. Chunk tickets
    chunks = chunk_tickets(tickets, chunk_size=45)
    
    # 2. Build prompts
    prompts = [build_prompt(chunk, config) for chunk in chunks]
    
    # 3. Boot ACP pool
    pool = ACPPool(pool_size=min(32, len(prompts)))
    await pool.start_pool()
    
    try:
        # 4. Dispatch all chunks
        results = await pool.process_chunks(prompts)
        
        # 5. Parse + validate
        all_classifications = []
        for i, result in enumerate(results):
            parsed = parse_acp_response(result)
            expected = len(chunks[i])
            if len(parsed) != expected:
                logger.error(
                    f"Chunk {i}: expected {expected} classifications, "
                    f"got {len(parsed)} — queuing for retry"
                )
                # TODO: retry logic
            all_classifications.extend(parsed)
        
        # 6. Write to SQLite
        for c in all_classifications:
            tool_registry._tool_store_classification(c.dict())
        
        logger.info(
            f"Scan complete: {len(all_classifications)}/{len(tickets)} "
            f"tickets classified in {len(chunks)} chunks"
        )
    finally:
        await pool.shutdown()
```

---

## Files to DELETE (Decomposed Bridge — No Longer Needed)

```
src/gemini/bridge_tools.mjs          # Custom tool registration into CLI internals
src/gemini/gemini_bridge.mjs         # Streaming event interceptor + NDJSON bridge
src/gemini/schemas/                  # Tool schemas injected into CLI registry
```

## Files to CREATE

```
src/agents/acp_pool.py               # ACP subprocess pool manager
src/agents/acp_parser.py             # NDJSON response parser + pydantic validation
```

## Files to MODIFY

```
config/prompts/nlp_classify.txt      # Remove tool-call instructions, add NDJSON format
src/agents/scan_orchestrator.py      # Replace bridge dispatch with ACP pool dispatch
src/agents/worker_agent.py           # Strip tool_call/tool_result event handling
```

## Files UNCHANGED

```
src/agents/tool_registry.py          # SQLite writer — same dict interface
src/agents/stream_parser.py          # May still be useful for fallback text parsing
```

---

## Authentication Boot Sequence

Known issue: `gemini --acp` prompts for interactive login when spawned as a subprocess even with cached credentials.

### Option A: Pre-auth on app launch
```python
async def boot_auth():
    """Run interactive gemini once to cache credentials before pool start."""
    proc = await asyncio.create_subprocess_exec(
        'gemini', '--prompt', 'echo authenticated',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    # Credentials now cached in ~/.gemini/google_accounts.json
```

### Option B: API key auth (bypasses OAuth entirely)
```python
# Set env var before spawning ACP instances
import os
os.environ['GEMINI_API_KEY'] = config.api_key

# All ACP instances inherit the env and skip OAuth
```

**NOTE**: If using API key auth, confirm which ToS applies (Gemini API ToS, not Code Assist ToS) and whether the BAA covers this auth path.

---

## Alternative: MCP Tool Pattern (Preserves Tool-Call Schema)

If NDJSON-from-prompt proves unreliable (model sometimes wraps in markdown fences, omits tickets, etc.), the tool-call pattern can be preserved ToS-compliantly:

1. Python runs a **local MCP server** exposing `store_classification` as a tool
2. ACP `session/new` includes MCP server config:
```json
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{
  "cwd":"C:\\workdir",
  "mcpServers":[{
    "name":"alma-tools",
    "type":"stdio",
    "command":"python",
    "args":["src/mcp/alma_mcp_server.py"]
  }]
}}
```
3. Prompt instructs model to call `store_classification` tool (original prompt works)
4. CLI routes tool calls to MCP server → Python handles storage directly

This is architecturally identical to the old bridge but goes through documented protocols. The CLI manages the tool lifecycle, not injected code.

---

## Validation Test Script (PowerShell)

Run these tests before and after rebuild to confirm parity.

### Test 1: Single ACP handshake
```powershell
$init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientCapabilities":{},"protocolVersion":1}}'
$init | gemini --acp
# Expect: JSON response with protocolVersion and agentCapabilities
```

### Test 2: Single chunk end-to-end (timed)
```powershell
$chunk = Get-Content .\test_chunk_45tickets.json -Raw
$escaped = $chunk.Replace('\','\\').Replace('"','\"').Replace("`n",'\n').Replace("`r",'')

$messages = @(
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientCapabilities":{},"protocolVersion":1}}'
  '{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"C:\\temp"}}'
  "{`"jsonrpc`":`"2.0`",`"id`":3,`"method`":`"session/prompt`",`"params`":{`"prompt`":`"$escaped`"}}"
)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$output = $messages -join "`n" | gemini --acp 2>$null
$sw.Stop()
Write-Host "45-ticket chunk: $($sw.Elapsed.TotalSeconds)s"
$toolCalls = ($output | Select-String 'ticket_id').Matches.Count
Write-Host "Classifications returned: $toolCalls / 45"
```

### Test 3: Parallel throughput at production pool size
```powershell
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$jobs = 1..32 | ForEach-Object {
    Start-Job -ScriptBlock {
        param($p)
        $p | gemini --acp 2>$null
    } -ArgumentList $prompt
}
$results = $jobs | Wait-Job | Receive-Job
$sw.Stop()
$errors = ($results | Select-String '"error"').Matches.Count
Write-Host "32 parallel: $($sw.Elapsed.TotalSeconds)s  errors=$errors"
$jobs | Remove-Job
```

### Test 4: Rate limit ceiling discovery
```powershell
foreach ($n in 16,24,32,48,64,72) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $jobs = 1..$n | ForEach-Object {
        Start-Job -ScriptBlock {
            param($p)
            $p | gemini --acp 2>$null
        } -ArgumentList $prompt
    }
    $results = $jobs | Wait-Job | Receive-Job
    $sw.Stop()
    $errors = ($results | Select-String '"error"').Matches.Count
    $rateLimits = ($results | Select-String '429|rate|quota|RESOURCE_EXHAUSTED').Matches.Count
    Write-Host "n=$n  time=$($sw.Elapsed.TotalSeconds)s  errors=$errors  rate_limits=$rateLimits"
    $jobs | Remove-Job
    Start-Sleep -Seconds 10
}
```

---

## Compliance Checklist

- [ ] All `src/gemini/` bridge files deleted
- [ ] No CLI internal imports remain in codebase
- [ ] All Gemini access goes through `gemini --acp` subprocess
- [ ] CLI binary is unmodified — no patching, no monkey-patching
- [ ] OAuth flows through CLI's own auth, not extracted tokens
- [ ] Confirm with compliance: "BAA covers Gemini CLI; `--acp` is a CLI flag, not a separate product"
- [ ] Confirm with compliance: if using API key auth, which ToS + BAA applies
- [ ] No PHI in dev/test data (scrubbed data only until BAA confirmation)
- [ ] ACP pool size capped at 32 (below soft throttle threshold of 48)
- [ ] Real payload test completed with 45-ticket chunks — classification count validated

---

## Open Questions for Google Account Rep

1. Does `gemini --acp` fall under the same BAA as interactive Gemini CLI usage?
2. Is the MCP-server-via-ACP pattern (local tool server) considered "third-party access" or "CLI usage"?
3. What are the official concurrency limits per account for ACP sessions?
4. Will the `--acp` flag remain stable, or is it still considered experimental?

---

## Timeline

| Task | Est. Hours | Depends On |
|------|-----------|------------|
| Real 45-ticket chunk test | 1h | — |
| `acp_pool.py` implementation | 3h | Chunk test results |
| `acp_parser.py` + pydantic models | 2h | — |
| Prompt template migration | 1h | — |
| Orchestrator rewire | 2h | Pool + parser |
| Delete bridge files | 0.5h | — |
| Auth boot sequence | 1h | — |
| Integration test (full 1000-ticket run) | 2h | All above |
| Compliance one-pager for infosec | 1h | — |
| **Total** | **~13.5h** | — |
