"""OS-aware diagnostics for the embedded web stack (Agent + enablement tabs).

Every failure mode from the macOS blank-render saga (2026-07-08) — and the
Windows offscreen quirks — is a cheap, deterministic probe here, so the next
"the page is blank" report starts from a machine-generated fact sheet instead
of a multi-day investigation. Modular by concern and branched by OS:

- ``probe_platform``  — OS / arch / Python / **page size** (the 4 KB-vs-16 KB
  mach fingerprint: an x86_64/Rosetta python reports 4096 on Apple Silicon)
- ``probe_qt``        — PySide6 + Qt versions, WebEngine importability
- ``probe_bundle``    — dist/index.html presence/size, qwebchannel.js sibling
- ``probe_env``       — QT_QPA_PLATFORM / QTWEBENGINE_* / ALMA_* flags
- ``probe_macos``     — Rosetta translation state, python-vs-QtWebEngineProcess
  Mach-O architecture mismatch (the QTBUG-98487 kill fingerprint), quarantine
  xattrs, leftover ``_CodeSignature`` dirs (the framework-corruption tell)
- ``probe_windows``   — QtWebEngineProcess.exe presence, GPU/offscreen notes

Pure stdlib, Qt-free (safe from the CLI, tests, or a dying render process).
Run it three ways: ``python scripts/web_diag.py``; ``ALMA_WEB_DIAG=1`` at app
start; or automatically when a WebHost's renderer dies.
"""

from __future__ import annotations

import os
import struct
import sys

OK = "ok"
WARN = "warn"
FAIL = "fail"

_HERE = os.path.dirname(__file__)
_DIST = os.path.join(_HERE, "dist", "index.html")
_QWEBCHANNEL = os.path.join(_HERE, "dist", "qwebchannel.js")
_SPIKE = os.path.join(_HERE, "static", "index.html")

# Mach-O constants for the arch reader (macOS only, but pure-python).
_FAT_MAGIC_BE = 0xCAFEBABE
_MH_MAGIC_64_LE = 0xFEEDFACF
_CPU_ARM64 = 0x0100000C
_CPU_X86_64 = 0x01000007
_CPU_NAMES = {_CPU_ARM64: "arm64", _CPU_X86_64: "x86_64"}


def _check(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


# ── generic probes (every OS) ─────────────────────────────────────────

def probe_platform() -> list[dict]:
    import platform as _platform
    checks = []
    checks.append(_check(
        "platform", OK,
        f"{sys.platform} / {_platform.machine()} / python {_platform.python_version()}"))
    try:
        import mmap
        page = mmap.PAGESIZE
        if sys.platform == "darwin" and _platform.machine() == "arm64" and page != 16384:
            checks.append(_check(
                "page_size", FAIL,
                f"{page} — an arm64 Mac python should see 16384; 4096 means this "
                "python (or an ancestor) runs under Rosetta → mixed-arch mach "
                "channels kill the renderer (QTBUG-98487)"))
        else:
            checks.append(_check("page_size", OK, str(page)))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("page_size", WARN, f"unreadable: {exc}"))
    return checks


def probe_qt() -> list[dict]:
    checks = []
    try:
        import PySide6
        qt_ver = getattr(PySide6, "__version__", "?")
        checks.append(_check("pyside6", OK, f"PySide6 {qt_ver}"))
    except Exception as exc:  # noqa: BLE001
        return [_check("pyside6", FAIL, f"PySide6 not importable: {exc}")]
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        checks.append(_check("webengine_import", OK, "QtWebEngineWidgets importable"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(
            "webengine_import", FAIL,
            f"QtWebEngineWidgets missing ({exc}) — Essentials-only install? "
            "The web tabs fall back to native Qt; the Agent shows its placeholder"))
    return checks


def probe_bundle() -> list[dict]:
    checks = []
    if os.path.exists(_DIST):
        size = os.path.getsize(_DIST)
        status = OK if size > 50_000 else WARN
        checks.append(_check(
            "bundle", status,
            f"dist/index.html {size:,} bytes" + ("" if status == OK else " — suspiciously small")))
    elif os.path.exists(_SPIKE):
        checks.append(_check(
            "bundle", WARN,
            "dist/index.html MISSING — falling back to the static spike "
            "(run: npm --prefix web run build)"))
    else:
        checks.append(_check("bundle", FAIL, "no web bundle at all — pages will be blank"))
    if os.path.exists(_QWEBCHANNEL):
        checks.append(_check("qwebchannel_js", OK, "sibling classic script present"))
    else:
        checks.append(_check(
            "qwebchannel_js", WARN,
            "dist/qwebchannel.js missing — bridges cannot connect (copied by the build)"))
    return checks


def probe_env() -> list[dict]:
    interesting = ("QT_QPA_PLATFORM", "QTWEBENGINE_DISABLE_SANDBOX",
                   "QTWEBENGINE_CHROMIUM_FLAGS", "ALMA_AGENT_DEVTOOLS",
                   "ALMA_WEB_DIAG")
    found = {k: os.environ[k] for k in interesting if k in os.environ}
    if not found:
        return [_check("env", OK, "no web-affecting env overrides")]
    detail = "; ".join(f"{k}={v}" for k, v in found.items())
    status = WARN if "QT_QPA_PLATFORM" in found else OK   # offscreen → blank grabs
    return [_check("env", status, detail)]


# ── macOS branch ──────────────────────────────────────────────────────

def _sysctl(name: str) -> str | None:
    import subprocess
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def macho_arches(path: str) -> set[str]:
    """The architecture slices of a Mach-O binary (fat or thin), pure python.
    Empty set when unreadable/not Mach-O."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except Exception:  # noqa: BLE001
        return set()
    if len(head) < 8:
        return set()
    (magic_be,) = struct.unpack(">I", head[:4])
    if magic_be == _FAT_MAGIC_BE:                      # universal binary
        (count,) = struct.unpack(">I", head[4:8])
        arches = set()
        for i in range(min(count, 8)):
            off = 8 + i * 20
            if off + 4 > len(head):
                break
            (cputype,) = struct.unpack(">i", head[off:off + 4])
            arches.add(_CPU_NAMES.get(cputype & 0xFFFFFFFF, f"cpu{cputype}"))
        return arches
    (magic_le,) = struct.unpack("<I", head[:4])
    if magic_le == _MH_MAGIC_64_LE:                    # thin 64-bit
        (cputype,) = struct.unpack("<i", head[4:8])
        return {_CPU_NAMES.get(cputype & 0xFFFFFFFF, f"cpu{cputype}")}
    return set()


def _find_webengine_process() -> str | None:
    try:
        import PySide6
        root = os.path.dirname(PySide6.__file__)
    except Exception:  # noqa: BLE001
        return None
    for dirpath, _dirs, files in os.walk(root):
        if "QtWebEngineProcess" in files:
            return os.path.join(dirpath, "QtWebEngineProcess")
    return None


def probe_macos() -> list[dict]:
    import platform as _platform
    checks = []
    translated = _sysctl("sysctl.proc_translated")
    hw_arm = _sysctl("hw.optional.arm64")
    if translated == "1":
        checks.append(_check(
            "rosetta", FAIL,
            "THIS python runs under Rosetta translation — the x86_64 preference "
            "reaches QtWebEngineProcess (blank render, QTBUG-98487). Launch via "
            "the fixed wrapper (LSRequiresNativeExecution / arch -arm64)"))
    elif hw_arm == "1" and _platform.machine() != "arm64":
        checks.append(_check(
            "rosetta", FAIL,
            f"Apple Silicon hardware but python reports {_platform.machine()} — "
            "an x86_64 python on arm64 produces the mixed-arch mach kill"))
    else:
        checks.append(_check("rosetta", OK, "native execution"))

    helper = _find_webengine_process()
    if helper:
        helper_arches = macho_arches(helper)
        py_arch = _platform.machine()
        if helper_arches and py_arch not in helper_arches and "arm64" not in helper_arches:
            checks.append(_check(
                "helper_arch", FAIL,
                f"QtWebEngineProcess slices {sorted(helper_arches)} exclude the "
                f"python arch {py_arch} — renderer cannot match the browser"))
        else:
            checks.append(_check(
                "helper_arch", OK,
                f"QtWebEngineProcess slices {sorted(helper_arches) or ['unknown']} "
                f"/ python {py_arch}"))
        fw_dir = os.path.dirname(helper)
        leftovers = []
        probe_root = os.path.dirname(os.path.dirname(fw_dir))
        for dirpath, dirs, _files in os.walk(probe_root):
            if "_CodeSignature" in dirs:
                leftovers.append(dirpath)
            if len(leftovers) > 2:
                break
        if leftovers:
            checks.append(_check(
                "framework_signature", WARN,
                "_CodeSignature dirs inside the PySide6 frameworks — a past "
                "manual re-sign may have corrupted them (2026-07-08 saga); the "
                "pristine wheel has NONE. Fix = pip force-reinstall all four "
                "PySide6 packages"))
        else:
            checks.append(_check("framework_signature", OK, "no re-sign leftovers"))
    else:
        checks.append(_check("helper_arch", WARN, "QtWebEngineProcess not found under PySide6"))

    try:
        import PySide6
        root = os.path.dirname(PySide6.__file__)
        import subprocess
        out = subprocess.run(["xattr", root], capture_output=True, text=True,
                             timeout=5)
        if "com.apple.quarantine" in (out.stdout or ""):
            checks.append(_check(
                "quarantine", WARN,
                "PySide6 tree carries com.apple.quarantine — strip it "
                "(xattr -rd com.apple.quarantine …)"))
        else:
            checks.append(_check("quarantine", OK, "no quarantine xattr"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("quarantine", WARN, f"unreadable: {exc}"))
    return checks


# ── Windows branch ────────────────────────────────────────────────────

def probe_windows() -> list[dict]:
    checks = []
    helper = None
    try:
        import PySide6
        candidate = os.path.join(os.path.dirname(PySide6.__file__),
                                 "QtWebEngineProcess.exe")
        helper = candidate if os.path.exists(candidate) else None
    except Exception:  # noqa: BLE001
        pass
    if helper:
        checks.append(_check("helper_exe", OK, "QtWebEngineProcess.exe present"))
    else:
        checks.append(_check(
            "helper_exe", WARN,
            "QtWebEngineProcess.exe not found next to PySide6 — full PySide6 "
            "(not Essentials) is required for the web surfaces"))
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        checks.append(_check(
            "offscreen", WARN,
            "offscreen platform: GLES fallback errors are normal noise and "
            "window grabs are ALWAYS blank — verify via runJavaScript, never "
            "screenshots"))
    return checks


# ── aggregator + report ───────────────────────────────────────────────

def run_diagnostics() -> dict:
    checks = []
    checks += probe_platform()
    checks += probe_qt()
    checks += probe_bundle()
    checks += probe_env()
    if sys.platform == "darwin":
        checks += probe_macos()
    elif sys.platform == "win32":
        checks += probe_windows()
    summary = {s: sum(1 for c in checks if c["status"] == s)
               for s in (OK, WARN, FAIL)}
    return {"platform": sys.platform, "checks": checks, "summary": summary}


def format_report(diag: dict | None = None) -> str:
    # ASCII-only output: a cp1252 Windows console chokes on box-drawing chars.
    diag = diag or run_diagnostics()
    lines = [f"-- web stack diagnostics ({diag['platform']}) - "
             f"{diag['summary'][OK]} ok / {diag['summary'][WARN]} warn / "
             f"{diag['summary'][FAIL]} FAIL --"]
    mark = {OK: " ", WARN: "!", FAIL: "X"}
    for c in diag["checks"]:
        lines.append(f" [{mark.get(c['status'], '?')}] {c['check']}: {c['detail']}")
    return "\n".join(lines)


def log_report(logger) -> None:
    """Dump the full report through ``logger`` — used by WebHost the moment a
    renderer dies, and at startup under ALMA_WEB_DIAG=1. Never raises."""
    try:
        diag = run_diagnostics()
        level = "error" if diag["summary"][FAIL] else "warning"
        getattr(logger, level, logger.warning)("\n%s", format_report(diag))
    except Exception:  # noqa: BLE001 — diagnostics must never take the app down
        try:
            logger.warning("web_diag failed to produce a report")
        except Exception:  # noqa: BLE001
            pass
