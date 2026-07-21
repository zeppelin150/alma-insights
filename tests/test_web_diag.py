"""web_diag — the OS-aware blank-page fact sheet. Both OS branches are
exercised HERE regardless of the host OS (monkeypatched platform/sysctl/
filesystem), so the module can't rot on the machine that isn't in front of us.
"""

import struct

import pytest

import src.ui.web.web_diag as wd


def _by(checks, name):
    return next(c for c in checks if c["check"] == name)


# ── real-host run (whatever OS CI/dev is on) ─────────────────────────

def test_run_diagnostics_shape_and_summary():
    diag = wd.run_diagnostics()
    assert diag["platform"] in ("win32", "darwin", "linux")
    assert diag["checks"], "no checks produced"
    for c in diag["checks"]:
        assert c["status"] in (wd.OK, wd.WARN, wd.FAIL)
        assert c["check"] and c["detail"] is not None
    total = sum(diag["summary"].values())
    assert total == len(diag["checks"])


def test_bundle_probe_sees_the_committed_dist():
    checks = wd.probe_bundle()
    assert _by(checks, "bundle")["status"] == wd.OK          # dist is committed
    assert _by(checks, "qwebchannel_js")["status"] == wd.OK


def test_qt_probe_finds_webengine_on_dev_box():
    checks = wd.probe_qt()
    assert _by(checks, "pyside6")["status"] == wd.OK
    assert _by(checks, "webengine_import")["status"] == wd.OK


def test_format_report_renders_marks():
    out = wd.format_report()
    assert "web stack diagnostics" in out
    assert " ok / " in out and "FAIL" in out


def test_log_report_never_raises():
    class BrokenLogger:
        def warning(self, *a):
            raise RuntimeError("logging down")

        error = warning

    wd.log_report(BrokenLogger())   # must swallow everything


# ── Mach-O arch reader (pure bytes, OS-independent) ──────────────────

def _thin(cputype):
    return struct.pack("<I", 0xFEEDFACF) + struct.pack("<i", cputype) + b"\0" * 24


def _fat(*cputypes):
    head = struct.pack(">II", 0xCAFEBABE, len(cputypes))
    for ct in cputypes:
        head += struct.pack(">i", ct) + b"\0" * 16
    return head


def test_macho_thin_and_fat(tmp_path):
    p = tmp_path / "thin_arm"
    p.write_bytes(_thin(0x0100000C))
    assert wd.macho_arches(str(p)) == {"arm64"}
    p2 = tmp_path / "thin_x86"
    p2.write_bytes(_thin(0x01000007))
    assert wd.macho_arches(str(p2)) == {"x86_64"}
    p3 = tmp_path / "universal"
    p3.write_bytes(_fat(0x0100000C, 0x01000007))
    assert wd.macho_arches(str(p3)) == {"arm64", "x86_64"}


def test_macho_not_macho(tmp_path):
    p = tmp_path / "notmacho"
    p.write_bytes(b"#!/bin/bash\necho hi\n")
    assert wd.macho_arches(str(p)) == set()
    assert wd.macho_arches(str(tmp_path / "missing")) == set()


# ── macOS branch (simulated on any host) ─────────────────────────────

def test_macos_rosetta_translated_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(wd, "_sysctl",
                        lambda n: "1" if n == "sysctl.proc_translated" else "1")
    monkeypatch.setattr(wd, "_find_webengine_process", lambda: None)
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    checks = wd.probe_macos()
    assert _by(checks, "rosetta")["status"] == wd.FAIL
    assert "QTBUG-98487" in _by(checks, "rosetta")["detail"]


def test_macos_arch_mismatch_detected(monkeypatch, tmp_path):
    # native arm64 python, but an x86_64-only helper → the kill fingerprint
    helper_dir = tmp_path / "Versions" / "A" / "Helpers"
    helper_dir.mkdir(parents=True)
    helper = helper_dir / "QtWebEngineProcess"
    helper.write_bytes(_thin(0x01000007))              # x86_64 only
    monkeypatch.setattr(wd, "_sysctl",
                        lambda n: "0" if n == "sysctl.proc_translated" else "1")
    monkeypatch.setattr(wd, "_find_webengine_process", lambda: str(helper))
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    checks = wd.probe_macos()
    assert _by(checks, "rosetta")["status"] == wd.OK
    assert _by(checks, "helper_arch")["status"] == wd.FAIL


def test_macos_signature_leftovers_warn(monkeypatch, tmp_path):
    helper_dir = tmp_path / "fw" / "Versions" / "A" / "Helpers"
    helper_dir.mkdir(parents=True)
    helper = helper_dir / "QtWebEngineProcess"
    helper.write_bytes(_thin(0x0100000C))
    (tmp_path / "fw" / "Versions" / "A" / "_CodeSignature").mkdir()
    monkeypatch.setattr(wd, "_sysctl", lambda n: "0")
    monkeypatch.setattr(wd, "_find_webengine_process", lambda: str(helper))
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    checks = wd.probe_macos()
    sig = _by(checks, "framework_signature")
    assert sig["status"] == wd.WARN and "force-reinstall" in sig["detail"]


# ── page-size fingerprint (simulated) ────────────────────────────────

def test_page_size_fingerprint_on_apple_silicon(monkeypatch):
    import mmap
    import platform
    monkeypatch.setattr(wd.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mmap, "PAGESIZE", 4096)
    checks = wd.probe_platform()
    ps = _by(checks, "page_size")
    assert ps["status"] == wd.FAIL and "Rosetta" in ps["detail"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
