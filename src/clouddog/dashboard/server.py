"""CloudDog dashboard backend.

Stdlib ``http.server`` only (ThreadingHTTPServer). Implements exactly the
endpoints in ``dashboard/API.md``. Binds to 127.0.0.1 only.

Rules enforced here:
- every number is computed live (shutil, /proc, os, real tool output);
  engine modules are imported lazily and honestly report "not available"
  when the parallel build has not landed them yet;
- any ``path`` parameter is realpath-resolved and must sit under one of
  ``ALLOWED_ROOTS`` (``/home/hatch`` or ``/tmp``) — traversal is rejected;
- POST /api/cli only runs allowlisted subcommands, no shell, argv elements
  validated element-by-element;
- no secrets, tokens, or cookie values are ever placed in a response.
"""
from __future__ import annotations

import contextlib
import glob
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import clouddog

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

ALLOWED_ROOTS = ("/home/hatch", "/tmp")
MAX_DU_ENTRIES = 200
LANE_GLOB_MAX = 20
CLI_TIMEOUT_S = 120
CLI_ALLOWLIST = frozenset({"scan", "store", "reap", "daemon", "verify"})
ARG_RE = re.compile(r"^[A-Za-z0-9_./:@=\-]+$")
FLAG_RE = re.compile(r"^-{1,2}[A-Za-z][A-Za-z0-9_-]*$")
OWNER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
KNOWN_BACKENDS = ("github", "terabox", "drive")
LOG_FRAC_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
GH_TIMEOUT_S = 30


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# In-flight sweep jobs: job_id -> {status, started, dry_run, report, error}.
_SWEEP_JOBS: dict = {}


def _send_json(handler: BaseHTTPRequestHandler, obj: object, status: int = 200) -> None:
    body = json.dumps(obj).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_error(handler: BaseHTTPRequestHandler, msg: str, status: int = 400) -> None:
    _send_json(handler, {"ok": False, "error": msg}, status)


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[object, str | None]:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        length = 0
    if length <= 0 or length > 10_000_000:
        return None, "empty or oversized request body"
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8")), None
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        return None, f"invalid JSON body: {exc}"


def _require_allowed(path_str: str) -> str:
    """Resolve ``path_str`` and require it under an allowed root."""
    if not path_str or not isinstance(path_str, str):
        raise ValueError("path is required")
    real = os.path.realpath(os.path.abspath(os.path.expanduser(path_str)))
    for root in ALLOWED_ROOTS:
        if real == root or real.startswith(root + os.sep):
            return real
    raise PermissionError(f"path outside allowed roots {ALLOWED_ROOTS}: {path_str}")


def _engine(module_name: str) -> tuple[object | None, str | None]:
    """Lazily import an engine module built by a parallel lane.

    A module that is missing OR broken (e.g. SyntaxError from a parallel
    lane's in-flight work) is reported as not available, with the
    underlying error attached — never a 500.
    """
    try:
        return importlib.import_module(module_name), None
    except ImportError:
        return None, f"engine module {module_name} not available"
    except Exception as exc:  # noqa: BLE001 - broken module, report honestly
        return (
            None,
            f"engine module {module_name} not available "
            f"({type(exc).__name__}: {exc})",
        )


def _backend_instance(mod: object) -> tuple[object | None, str | None]:
    """Find a StoreBackend subclass instance inside a stores.* module."""
    from clouddog.stores.base import StoreBackend

    candidates = [
        v
        for v in vars(mod).values()
        if isinstance(v, type) and issubclass(v, StoreBackend) and v is not StoreBackend
    ]
    if not candidates:
        return None, f"no StoreBackend subclass found in {mod.__name__}"
    try:
        return candidates[0](), None
    except Exception as exc:  # noqa: BLE001
        return None, f"could not instantiate backend: {exc}"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# live system probes (no engine needed)
# --------------------------------------------------------------------------

def _disk_info() -> dict:
    # The data volume is /home/hatch (100G); "/" is a different mount.
    usage = shutil.disk_usage(os.path.expanduser("/home/hatch"))
    pct = round(usage.used / usage.total * 100, 2) if usage.total else 0.0
    return {
        "path": "/home/hatch",
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "use_pct": pct,
    }


def _dir_size(path: str) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                fp = os.path.join(root, name)
                try:
                    if not os.path.islink(fp):
                        total += os.lstat(fp).st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _du(path: str, depth: int) -> dict:
    if depth != 1:
        raise ValueError("only depth=1 is supported")
    entries = []
    with os.scandir(path) as it:
        for entry in it:
            try:
                st = entry.stat(follow_symlinks=False)
                if entry.is_dir(follow_symlinks=False):
                    size = _dir_size(entry.path)
                else:
                    size = st.st_size
                entries.append(
                    {"path": entry.path, "bytes": size, "mtime": st.st_mtime}
                )
            except OSError:
                continue
    entries.sort(key=lambda e: e["bytes"], reverse=True)
    return {"path": path, "entries": entries[:MAX_DU_ENTRIES]}


def _mem_info() -> dict:
    total = avail = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
    except OSError as exc:
        raise RuntimeError(f"could not read /proc/meminfo: {exc}") from exc
    if total is None or avail is None:
        raise RuntimeError("MemTotal/MemAvailable missing from /proc/meminfo")
    used = total - avail
    return {
        "total_bytes": total,
        "used_bytes": used,
        "pct": round(used / total * 100, 2) if total else 0.0,
    }


def _gpu_info() -> dict | None:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return None
    try:
        proc = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.used,memory.total",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        name, used_s, total_s = parts

        def _mib(s: str) -> int | None:
            m = re.match(r"([\d.]+)\s*(KiB|MiB|GiB|B)", s, re.IGNORECASE)
            if not m:
                return None
            val, unit = float(m.group(1)), m.group(2).lower()
            mult = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3}[unit]
            return int(val * mult)

        used_b, total_b = _mib(used_s), _mib(total_s)
        if used_b is None or total_b is None:
            continue
        return {"name": name, "memory_used_bytes": used_b, "memory_total_bytes": total_b}
    return None


def _sysinfo() -> dict:
    load1, load5, load15 = os.getloadavg()
    return {
        "cpu": {
            "load_avg_1min": load1,
            "load_avg_5min": load5,
            "load_avg_15min": load15,
            "cpu_count": os.cpu_count(),
        },
        "mem": _mem_info(),
        "disk": _disk_info(),
        "gpu": _gpu_info(),
    }


def _backend_health() -> list:
    out = []
    for name in KNOWN_BACKENDS:
        mod, err = _engine(f"clouddog.stores.{name}")
        if err:
            out.append({"name": name, "health": "down", "reason": err})
            continue
        inst, ierr = _backend_instance(mod)
        if ierr:
            out.append({"name": name, "health": "down", "reason": ierr})
            continue
        try:
            status = inst.health()
            out.append(
                {
                    "name": name,
                    "health": status.health.value,
                    "reason": status.reason,
                }
            )
        except Exception as exc:  # noqa: BLE001
            out.append({"name": name, "health": "down", "reason": str(exc)})
    return out


def _ps_args() -> list[str]:
    try:
        proc = subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return proc.stdout.splitlines()


def _lanes() -> list:
    pattern = os.path.join(os.path.expanduser("~/workspace"), "*", "blob_checkpoint.json")
    files = sorted(glob.glob(pattern))[:LANE_GLOB_MAX]
    ps_lines = _ps_args()
    lanes = []
    for manifest_path in files:
        lane_dir = os.path.dirname(manifest_path)
        lane = os.path.basename(lane_dir)
        note_parts: list[str] = []
        uploaded: int | None = None
        total: int | None = None
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            data = None
            note_parts.append(f"manifest unreadable: {exc}")
        if isinstance(data, dict):
            if isinstance(data.get("uploaded"), int):
                uploaded = data["uploaded"]
            blobs = data.get("blobs")
            if isinstance(blobs, list):
                if uploaded is None:
                    uploaded = len(blobs)
            elif uploaded is None:
                note_parts.append("no 'uploaded' count and no 'blobs' list in manifest")
            if isinstance(data.get("total"), int):
                total = data["total"]
        elif data is not None:
            note_parts.append("manifest is not a JSON object")
        # newest *.log in the lane dir; parse N/M progress patterns for total
        log_tail: list[str] = []
        log_path: str | None = None
        try:
            logs = [
                p
                for p in glob.glob(os.path.join(lane_dir, "*.log"))
                if os.path.isfile(p)
            ]
            if logs:
                log_path = max(logs, key=os.path.getmtime)
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
                log_tail = lines[-10:]
                if total is None:
                    for line in reversed(lines[-200:]):
                        m = LOG_FRAC_RE.search(line)
                        if m:
                            try:
                                total = int(m.group(2))
                            except ValueError:
                                total = None
                            break
            else:
                note_parts.append("no .log file found in lane dir")
        except OSError as exc:
            note_parts.append(f"log unreadable: {exc}")
        if total is None:
            note_parts.append("total unknown (manifest lacks 'total', log has no N/M pattern)")
        alive = any(
            lane in line
            and ("python" in line.lower() or ".py" in line or ".sh" in line)
            and "ps -eo" not in line
            for line in ps_lines
        )
        pct = (
            round(uploaded / total * 100, 1)
            if uploaded is not None and total
            else None
        )
        lanes.append(
            {
                "lane": lane,
                "manifest_path": manifest_path,
                "uploaded": uploaded,
                "total": total,
                "pct": pct,
                "alive": alive,
                "log_tail": log_tail,
                "log_path": log_path,
                "note": "; ".join(note_parts) if note_parts else None,
            }
        )
    lanes.extend(_cloud_dog_registry_lane())
    return lanes


def _cloud_dog_registry_lane() -> list:
    """Surface cloud_dog.py's registry (the single-file engine) in the lanes
    view. Read-only; never modifies the registry. Returns [] when absent."""
    reg_path = os.path.join(
        os.path.expanduser("~/workspace"), "agent-ops", "cloud-dog-registry.json"
    )
    try:
        with open(reg_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    if not entries:
        return [{
            "lane": "cloud-dog:registry",
            "manifest_path": reg_path,
            "uploaded": 0,
            "total": 0,
            "pct": None,
            "alive": None,
            "log_tail": [],
            "log_path": None,
            "note": "cloud_dog.py registry present, 0 entries",
        }]
    by_status: dict[str, int] = {}
    total_bytes = 0
    for e in entries.values():
        if not isinstance(e, dict):
            continue
        by_status[e.get("status", "unknown")] = by_status.get(e.get("status", "unknown"), 0) + 1
        try:
            total_bytes += int(e.get("size", 0) or 0)
        except (ValueError, TypeError):
            pass
    verified = by_status.get("verified", 0) + by_status.get("reaped", 0)
    total = len(entries)
    return [{
        "lane": "cloud-dog:registry",
        "manifest_path": reg_path,
        "uploaded": verified,
        "total": total,
        "pct": round(verified / total * 100, 1) if total else None,
        "alive": None,
        "log_tail": [],
        "log_path": None,
        "note": "from cloud_dog.py registry: "
                + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items()))
                + f"; {total_bytes} bytes tracked",
    }]


# --------------------------------------------------------------------------
# gh CLI passthrough (real tool output, no mock)
# --------------------------------------------------------------------------

def _gh_api(args: list[str]) -> tuple[object | None, str | None]:
    gh = shutil.which("gh")
    if not gh:
        return None, "gh CLI not available"
    try:
        proc = subprocess.run(
            [gh, "api", *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"gh api failed: {exc}"
    if proc.returncode != 0:
        return None, f"gh api error: {proc.stderr.strip()[:300]}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"gh api returned non-JSON: {exc}"


def _repos(owner: str) -> list:
    data, err = _gh_api([f"users/{owner}/repos", "--paginate"])
    if err:
        raise RuntimeError(err)
    repos = data if isinstance(data, list) else data.get("repositories", [])
    return [
        {"name": r.get("name"), "description": r.get("description")}
        for r in repos
        if isinstance(r, dict)
    ]


def _repo_contents(owner: str, repo: str, path: str) -> list:
    api_path = f"repos/{owner}/{repo}/contents"
    if path:
        api_path += "/" + path.strip("/")
    data, err = _gh_api([api_path])
    if err:
        raise RuntimeError(err)
    items = data if isinstance(data, list) else [data]
    return [
        {
            "name": i.get("name"),
            "type": i.get("type"),
            "size": i.get("size"),
            "path": i.get("path"),
        }
        for i in items
        if isinstance(i, dict)
    ]


def _report_to_dict(report: object) -> object:
    """Convert an engine report (dataclass or mapping) to plain JSON-able data."""
    if isinstance(report, dict):
        return report
    try:
        import dataclasses

        if dataclasses.is_dataclass(report):
            return dataclasses.asdict(report)
    except Exception:  # noqa: BLE001
        pass
    if hasattr(report, "__dict__"):
        return dict(report.__dict__)
    return {"result": str(report)}


# --------------------------------------------------------------------------
# POST /api/cli — allowlisted, no shell
# --------------------------------------------------------------------------

def _validate_cli_argv(argv: object) -> str | None:
    if not isinstance(argv, list) or not argv:
        return "argv must be a non-empty list"
    if not all(isinstance(a, str) and a for a in argv):
        return "every argv element must be a non-empty string"
    if argv[0] not in CLI_ALLOWLIST:
        return f"subcommand '{argv[0]}' not allowlisted (allowed: {sorted(CLI_ALLOWLIST)})"
    for a in argv[1:]:
        if not ARG_RE.match(a):
            return f"argv element rejected by character allowlist: {a!r}"
        if ".." in a:
            return f"argv element may not contain '..': {a!r}"
        if a.startswith("-") and not FLAG_RE.match(a):
            return f"flag not recognized: {a!r}"
        if a.startswith("/"):
            try:
                _require_allowed(a)
            except (ValueError, PermissionError) as exc:
                return f"path argument rejected: {exc}"
    return None


def _run_cli(argv: list[str]) -> dict:
    """Run clouddog.cli.main(argv) in-process, capturing stdio, with timeout."""
    mod, err = _engine("clouddog.cli")
    if err:
        return {"error": err, "status": 503}
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    result: dict = {}

    def target() -> None:
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(
                stderr_buf
            ):
                rc = mod.main(list(argv))
            result["returncode"] = 0 if rc is None else int(rc)
        except SystemExit as exc:
            code = exc.code
            result["returncode"] = 0 if code is None else (code if isinstance(code, int) else 1)
        except BaseException as exc:  # noqa: BLE001
            result["returncode"] = 1
            stderr_buf.write(f"clouddog.cli.main raised {type(exc).__name__}: {exc}\n")

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(CLI_TIMEOUT_S)
    if thread.is_alive():
        return {"error": f"cli timed out after {CLI_TIMEOUT_S}s", "status": 504}
    result["stdout"] = stdout_buf.getvalue()
    result["stderr"] = stderr_buf.getvalue()
    return result


# --------------------------------------------------------------------------
# POST /api/daemon/install — real generated configs
# --------------------------------------------------------------------------

def _daemon_configs(os_name: str, interval: int) -> list[dict]:
    files: list[dict] = []
    if os_name == "linux":
        service = f"""# CloudDog daemon — systemd service
# Install: copy to ~/.config/systemd/user/clouddog-daemon.service
#   systemctl --user daemon-reload
#   systemctl --user enable --now clouddog-daemon.service
#   systemctl --user enable --now clouddog-daemon.timer
[Unit]
Description=CloudDog daemon (disk hygiene engine)
After=network-online.target

[Service]
Type=simple
# Runs one daemon loop every {interval} minutes; systemd restarts it on failure.
ExecStart=/home/hatch/.local/bin/clouddog daemon --interval {interval}
Restart=always
RestartSec=60

[Install]
WantedBy=default.target
"""
        timer = f"""# CloudDog daemon — systemd timer (ensures the service is started)
# Install: copy to ~/.config/systemd/user/clouddog-daemon.timer
[Unit]
Description=Run CloudDog daemon every {interval} minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec={interval}min
Unit=clouddog-daemon.service

[Install]
WantedBy=timers.target
"""
        files.append({"filename": "clouddog-daemon.service", "content": service})
        files.append({"filename": "clouddog-daemon.timer", "content": timer})
    elif os_name == "macos":
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- CloudDog daemon — LaunchAgent. Install: copy to ~/Library/LaunchAgents/com.clouddog.daemon.plist, then: launchctl load ~/Library/LaunchAgents/com.clouddog.daemon.plist -->
<plist version="1.0">
<dict>
  <key>Label</key><string>com.clouddog.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/clouddog</string>
    <string>daemon</string>
    <string>--interval</string>
    <string>{interval}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>{interval * 60}</integer>
  <key>StandardOutPath</key><string>/tmp/clouddog-daemon.log</string>
  <key>StandardErrorPath</key><string>/tmp/clouddog-daemon.err</string>
</dict>
</plist>
"""
        files.append({"filename": "com.clouddog.daemon.plist", "content": plist})
    else:  # windows
        ps1 = f"""# CloudDog daemon — Windows Task Scheduler installer
# Run in an elevated PowerShell: .\\Install-CloudDogDaemon.ps1
$action = New-ScheduledTaskAction -Execute "clouddog" -Argument "daemon --interval {interval}"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$repetition = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes {interval})
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "CloudDogDaemon" -Action $action -Trigger @($trigger, $repetition) -Settings $settings -RunLevel Highest -Force
Write-Host "CloudDog daemon scheduled: clouddog daemon --interval {interval}"
"""
        files.append({"filename": "Install-CloudDogDaemon.ps1", "content": ps1})
    return files


# --------------------------------------------------------------------------
# POST /api/broadcast/draft — pure formatting, writes nothing
# --------------------------------------------------------------------------

def _broadcast_markdown(title: str, laws: list) -> str:
    lines = [
        f"# Proposal: {title}",
        "",
        f"_Drafted {_utcnow()} — NOT APPLIED. Requires owner approval._",
        "",
        "## Proposed laws",
        "",
    ]
    for i, law in enumerate(laws, 1):
        name = law.get("name", f"law-{i}")
        text = law.get("text", "")
        lines.append(f"### {i}. {name}")
        lines.append("")
        lines.append(str(text))
        lines.append("")
    lines.append("---")
    lines.append(
        "CloudDog broadcast draft — nothing was written to AGENTS.md or MEMORY.md."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# POST /api/relocate — hash-verify, store per file, then clear + shim
# --------------------------------------------------------------------------

_RELOCATE_LIMITS = [
    "Files are stored individually via backend.store(); no tar archive is created.",
    "GitHub tier: release asset names are flattened (path separators become '__'); basename collisions abort the relocate.",
    "The original directory is deleted only after every file verifies byte-exact (size + sha256) on the store.",
    "The retrieval shim re-downloads with the tier's fetch tool; TeraBox retrieval depends on the session-keeper tooling and may need a fresh browser session.",
]


def _relocate_manifest(real: str) -> tuple[list[dict], str | None]:
    """Walk ``real`` (no symlink follow); return entries + collision error.

    Only regular files are relocated; sockets, fifos, and device nodes are
    skipped (they cannot be hash-verified or re-downloaded meaningfully).
    """
    entries: list[dict] = []
    seen_basenames: set[str] = set()
    for root, _dirs, files in os.walk(real, followlinks=False):
        for name in files:
            fp = os.path.join(root, name)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue  # socket / fifo / device / symlink -> skip
            rel = os.path.relpath(fp, real)
            try:
                digest = _sha256_file(fp)
            except OSError as exc:
                return [], f"could not hash {rel}: {exc}"
            flat = rel.replace(os.sep, "__")
            if flat in seen_basenames:
                return [], f"basename collision after flattening: {rel}"
            seen_basenames.add(flat)
            entries.append(
                {"rel": rel, "size": st.st_size, "sha256": digest, "local": fp, "flat": flat}
            )
    entries.sort(key=lambda e: e["rel"])
    return entries, None


def _relocate_shim(tier: str, remote_ref: str, entries: list[dict]) -> str:
    lines = [
        "#!/bin/sh",
        "# CloudDog retrieval shim — re-downloads a relocated directory.",
        f"# Generated {_utcnow()}. Tier: {tier}. Remote ref: {remote_ref}",
        "set -e",
        'DEST="${1:-.}"',
        'mkdir -p "$DEST"',
        "",
    ]
    if tier == "github":
        if "@" in remote_ref:
            repo_part, tag = remote_ref.rsplit("@", 1)
        else:
            repo_part, tag = remote_ref, "latest"
        lines += [
            'STAGE="$(mktemp -d)"',
            'trap \'rm -rf "$STAGE"\' EXIT',
            f'gh release download "{tag}" -R "{repo_part}" -D "$STAGE"',
        ]
        for e in entries:
            lines.append(f'mkdir -p "$DEST/$(dirname "{e["rel"]}")"')
            lines.append(f'mv "$STAGE/{e["flat"]}" "$DEST/{e["rel"]}"')
    elif tier == "terabox":
        lines.append(
            "# TeraBox retrieval requires the session-keeper uploader tooling"
        )
        lines.append(
            "# (see agent-ops/TERABOX-SESSION-KEEPER.md); adapt the fetch command below."
        )
        for e in entries:
            lines.append(f'mkdir -p "$DEST/$(dirname "{e["rel"]}")"')
            lines.append(
                f'# tbupload download "{remote_ref}/{e["rel"]}" "$DEST/{e["rel"]}"'
            )
    else:  # drive
        for e in entries:
            lines.append(f'mkdir -p "$DEST/$(dirname "{e["rel"]}")"')
            lines.append(
                f'rclone copyto "{remote_ref}/{e["rel"]}" "$DEST/{e["rel"]}"'
            )
    lines.append('echo "retrieved into $DEST"')
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# request handler
# --------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CloudDogDashboard/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102
        sys.stderr.write(f"dashboard: {fmt % args}\n")

    # -- routing ------------------------------------------------------
    def do_GET(self) -> None:  # noqa: D102, N802
        split = urllib.parse.urlsplit(self.path)
        path = split.path
        query = urllib.parse.parse_qs(split.query)
        try:
            if path == "/" or path == "/index.html":
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
            elif path == "/api/health":
                _send_json(
                    self,
                    {"ok": True, "version": clouddog.__version__, "time_utc": _utcnow()},
                )
            elif path == "/api/disk":
                _send_json(self, _disk_info())
            elif path == "/api/du":
                self._handle_du(query)
            elif path == "/api/backends":
                _send_json(self, _backend_health())
            elif path == "/api/sysinfo":
                _send_json(self, _sysinfo())
            elif path == "/api/policy/scan":
                self._handle_policy_scan(query)
            elif path == "/api/lanes":
                _send_json(self, _lanes())
            elif path == "/api/sweep/status":
                self._handle_sweep_status()
            elif path == "/api/audit":
                self._handle_audit(query)
            elif path == "/api/repos":
                self._handle_repos(query)
            elif path.startswith("/api/repos/") and path.endswith("/contents"):
                self._handle_repo_contents(path, query)
            elif path == "/api/torrent/status":
                self._handle_torrent_status()
            else:
                _send_error(self, f"unknown endpoint: {path}", 404)
        except (ValueError, PermissionError) as exc:
            _send_error(self, str(exc), 400)
        except RuntimeError as exc:
            _send_error(self, str(exc), 502)
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"internal error: {exc}", 500)

    def do_POST(self) -> None:  # noqa: D102, N802
        split = urllib.parse.urlsplit(self.path)
        path = split.path
        try:
            if path == "/api/store":
                self._handle_store()
            elif path == "/api/verify":
                self._handle_verify()
            elif path == "/api/reap":
                self._handle_reap()
            elif path == "/api/sweep":
                self._handle_sweep()
            elif path == "/api/daemon/install":
                self._handle_daemon_install()
            elif path == "/api/cli":
                self._handle_cli()
            elif path == "/api/broadcast/draft":
                self._handle_broadcast_draft()
            elif path == "/api/relocate":
                self._handle_relocate()
            elif path == "/api/torrent/add":
                self._handle_torrent_add()
            else:
                _send_error(self, f"unknown endpoint: {path}", 404)
        except (ValueError, PermissionError) as exc:
            _send_error(self, str(exc), 400)
        except RuntimeError as exc:
            _send_error(self, str(exc), 502)
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"internal error: {exc}", 500)

    # -- static -------------------------------------------------------
    def _serve_static(self, rel: str) -> None:
        if not rel or rel.startswith("/") or ".." in rel:
            _send_error(self, "not found", 404)
            return
        real = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not (real == STATIC_DIR or real.startswith(STATIC_DIR + os.sep)):
            _send_error(self, "not found", 404)
            return
        if not os.path.isfile(real):
            _send_error(self, "not found", 404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(os.path.splitext(real)[1], "application/octet-stream")
        with open(real, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- GET handlers -------------------------------------------------
    def _handle_du(self, query: dict) -> None:
        raw_path = (query.get("path") or [""])[0]
        depth_s = (query.get("depth") or ["1"])[0]
        try:
            depth = int(depth_s)
        except ValueError:
            raise ValueError("depth must be an integer") from None
        real = _require_allowed(raw_path or os.path.expanduser("~/workspace"))
        _send_json(self, _du(real, depth))

    def _handle_policy_scan(self, query: dict) -> None:
        raw_path = (query.get("path") or [""])[0]
        real = _require_allowed(raw_path or os.path.expanduser("~/workspace"))
        mod, err = _engine("clouddog.policy")
        if err:
            _send_error(self, err, 503)
            return
        try:
            if hasattr(mod, "scan"):
                result = mod.scan(real, max_entries=200)
            else:
                # fallback: policy.evaluate over top-level entries
                result = []
                with os.scandir(real) as it:
                    for entry in it:
                        d = mod.evaluate(entry.path, scan_root=real)
                        action = d.action
                        result.append(
                            {
                                "path": entry.path,
                                "bytes": 0,
                                "action": action.value
                                if hasattr(action, "value")
                                else str(action),
                                "tier": d.tier,
                                "reason": d.reason,
                            }
                        )
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"policy scan failed: {exc}", 500)
            return
        if not isinstance(result, list):
            _send_error(self, "policy scan did not return a list", 500)
            return
        _send_json(self, result)

    def _handle_audit(self, query: dict) -> None:
        limit_s = (query.get("limit") or ["100"])[0]
        try:
            limit = int(limit_s)
        except ValueError:
            raise ValueError("limit must be an integer") from None
        if not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        mod, err = _engine("clouddog.audit")
        if err:
            _send_error(self, err, 503)
            return
        try:
            if hasattr(mod, "read"):
                entries = mod.read(limit)
            elif hasattr(mod, "AuditJournal"):
                journal_path = os.path.expanduser("~/.clouddog/audit.jsonl")
                entries = mod.AuditJournal(journal_path).read(limit)
            else:
                raise RuntimeError("no readable audit API found")
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"audit read failed: {exc}", 500)
            return
        if not isinstance(entries, list):
            _send_error(self, "audit read did not return a list", 500)
            return
        _send_json(self, entries)

    def _handle_repos(self, query: dict) -> None:
        owner = (query.get("owner") or ["mhvnsnt"])[0]
        if not OWNER_RE.match(owner):
            raise ValueError(f"invalid owner: {owner!r}")
        _send_json(self, _repos(owner))

    def _handle_repo_contents(self, path: str, query: dict) -> None:
        # /api/repos/{owner}/{repo}/contents
        rest = path[len("/api/repos/"):-len("/contents")].strip("/")
        parts = rest.split("/")
        if len(parts) != 2 or not all(OWNER_RE.match(p) for p in parts):
            raise ValueError("expected /api/repos/{owner}/{repo}/contents")
        owner, repo = parts
        sub = (query.get("path") or [""])[0]
        if sub and (".." in sub or sub.startswith("/")):
            raise ValueError("repo sub-path may not traverse")
        _send_json(self, _repo_contents(owner, repo, sub))

    def _handle_torrent_status(self) -> None:
        mod, err = _engine("clouddog.torrent")
        if err:
            _send_json(
                self,
                {"available": False, "reason": err, "version": None, "torrents": []},
            )
            return
        try:
            if hasattr(mod, "libtorrent_status"):
                status = dict(mod.libtorrent_status())
            else:
                status = dict(mod.status())
        except Exception as exc:  # noqa: BLE001
            _send_json(
                self,
                {
                    "available": False,
                    "reason": f"torrent status probe failed: {exc}",
                    "version": None,
                    "torrents": [],
                },
            )
            return
        if not isinstance(status, dict):
            _send_json(
                self,
                {
                    "available": False,
                    "reason": "torrent status did not return a dict",
                    "version": None,
                    "torrents": [],
                },
            )
            return
        status.setdefault("torrents", [])
        if status.get("available") and hasattr(mod, "_TorrentEngine"):
            try:
                status["torrents"] = mod._TorrentEngine().status_list()
            except Exception as exc:  # noqa: BLE001
                status["torrents"] = []
                status["reason"] = f"{status.get('reason', '')} (list failed: {exc})"
        _send_json(self, status)

    # -- POST handlers ------------------------------------------------
    def _handle_store(self) -> None:
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        real = _require_allowed(body.get("path", ""))
        tier = body.get("tier", "")
        remote_ref = body.get("remote_ref", "")
        dry_run = bool(body.get("dry_run", False))
        if tier not in KNOWN_BACKENDS:
            raise ValueError(f"tier must be one of {KNOWN_BACKENDS}")
        if not isinstance(remote_ref, str) or not remote_ref:
            raise ValueError("remote_ref is required")
        mod, merr = _engine(f"clouddog.stores.{tier}")
        if merr:
            _send_error(self, merr, 503)
            return
        inst, ierr = _backend_instance(mod)
        if ierr:
            _send_error(self, ierr, 503)
            return
        try:
            stored = inst.store(real, remote_ref, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"store failed: {exc}", 502)
            return
        _send_json(self, {"ok": True, "dry_run": dry_run, "stored": stored.to_dict()})

    def _handle_verify(self) -> None:
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        from clouddog.stores.base import StoredRef

        real = _require_allowed(body.get("path", ""))
        stored_raw = body.get("stored")
        if not isinstance(stored_raw, dict):
            raise ValueError("stored must be a StoredRef object")
        try:
            stored = StoredRef.from_dict(stored_raw)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"invalid StoredRef: {exc}") from None
        if stored.backend not in KNOWN_BACKENDS:
            raise ValueError(f"unknown backend: {stored.backend}")
        mod, merr = _engine(f"clouddog.stores.{stored.backend}")
        if merr:
            _send_error(self, merr, 503)
            return
        inst, ierr = _backend_instance(mod)
        if ierr:
            _send_error(self, ierr, 503)
            return
        try:
            match = bool(inst.verify(real, stored))
            detail = "byte-exact match" if match else "mismatch"
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"verify failed: {exc}", 502)
            return
        _send_json(self, {"ok": True, "match": match, "detail": detail})

    def _handle_reap(self) -> None:
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        manifest_raw = body.get("manifest", "")
        src_dir = _require_allowed(body.get("src_dir", ""))
        dry_run = bool(body.get("dry_run", False))
        mod, merr = _engine("clouddog.reaper")
        if merr:
            _send_error(self, merr, 503)
            return
        # manifest: inline JSON object, or a path to a JSON manifest file
        manifest: dict = {}
        try:
            if isinstance(manifest_raw, dict):
                manifest = dict(manifest_raw)
            elif isinstance(manifest_raw, str) and manifest_raw.strip():
                mpath = _require_allowed(manifest_raw)
                with open(mpath, "r", encoding="utf-8") as fh:
                    manifest = json.load(fh)
                if not isinstance(manifest, dict):
                    raise ValueError("manifest JSON must be an object")
        except (ValueError, PermissionError) as exc:
            _send_error(self, f"bad manifest: {exc}", 400)
            return
        except (OSError, json.JSONDecodeError) as exc:
            _send_error(self, f"could not load manifest: {exc}", 400)
            return
        log_path_raw = body.get("log_path", "")
        if log_path_raw:
            log_path = _require_allowed(log_path_raw)
        else:
            logs = sorted(
                glob.glob(os.path.join(src_dir, "*.log")), key=os.path.getmtime
            )
            log_path = logs[-1] if logs else os.path.join(src_dir, "clouddog-reap.log")
        try:
            if hasattr(mod, "Reaper"):
                reaper = mod.Reaper(
                    src_dir=src_dir, manifest=manifest, log_path=log_path
                )
                report = _report_to_dict(reaper.reap_once(dry_run=dry_run))
            else:
                report = mod.reap(
                    manifest=manifest, src_dir=src_dir, dry_run=dry_run
                )
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"reaper pass failed: {exc}", 500)
            return
        if isinstance(report, dict):
            _send_json(self, {"ok": True, **report})
        else:
            _send_json(self, {"ok": True, "result": report})

    def _handle_sweep(self) -> None:
        # Async: a full-workspace tick takes minutes; run it in a background
        # thread and return a job id immediately. Poll /api/sweep/status.
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        dry_run = bool(body.get("dry_run", False))
        mod, merr = _engine("clouddog.daemon")
        if merr:
            _send_error(self, merr, 503)
            return
        job_id = "sweep-%d" % int(time.time() * 1000)
        _SWEEP_JOBS[job_id] = {"status": "running", "started": _utcnow(),
                               "dry_run": dry_run, "report": None,
                               "error": None}

        def _run():
            try:
                if hasattr(mod, "Daemon"):
                    daemon = mod.Daemon(
                        roots=[os.path.expanduser("~/workspace")],
                        dry_run=dry_run,
                    )
                    report = _report_to_dict(daemon.tick())
                else:
                    report = mod.tick(
                        roots=[os.path.expanduser("~/workspace")],
                        dry_run=dry_run,
                    )
                _SWEEP_JOBS[job_id].update({"status": "done", "report": report})
            except Exception as exc:  # noqa: BLE001
                _SWEEP_JOBS[job_id].update({"status": "error",
                                            "error": str(exc)[:500]})

        threading.Thread(target=_run, daemon=True).start()
        _send_json(self, {"ok": True, "job_id": job_id, "status": "running"},
                   status=202)

    def _handle_sweep_status(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        job_id = urllib.parse.parse_qs(query).get("job", [None])[0]
        job = _SWEEP_JOBS.get(job_id) if job_id else None
        if job is None:
            _send_error(self, "unknown sweep job", 404)
            return
        _send_json(self, {"ok": True, "job_id": job_id, **job})

    def _handle_daemon_install(self) -> None:
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        os_name = body.get("os", "")
        if os_name not in ("linux", "macos", "windows"):
            raise ValueError("os must be one of: linux, macos, windows")
        try:
            interval = int(body.get("interval_minutes", 60))
        except (TypeError, ValueError):
            raise ValueError("interval_minutes must be an integer") from None
        if not 1 <= interval <= 10080:
            raise ValueError("interval_minutes must be between 1 and 10080")
        files = _daemon_configs(os_name, interval)
        _send_json(
            self,
            {
                "ok": True,
                "os": os_name,
                "interval_minutes": interval,
                "files": files,
            },
        )

    def _handle_cli(self) -> None:
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        argv = body.get("argv")
        verr = _validate_cli_argv(argv)
        if verr:
            _send_error(self, verr, 403)
            return
        result = _run_cli(list(argv))
        if "error" in result:
            _send_error(self, result["error"], result.get("status", 500))
            return
        _send_json(
            self,
            {
                "ok": True,
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "returncode": result.get("returncode", 1),
            },
        )

    def _handle_broadcast_draft(self) -> None:
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        title = body.get("title", "")
        laws = body.get("laws", [])
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title is required")
        if not isinstance(laws, list) or not laws:
            raise ValueError("laws must be a non-empty list")
        for law in laws:
            if not isinstance(law, dict) or "name" not in law or "text" not in law:
                raise ValueError("each law must have 'name' and 'text'")
        _send_json(self, {"ok": True, "markdown": _broadcast_markdown(title, laws)})

    def _handle_relocate(self) -> None:
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        real = _require_allowed(body.get("path", ""))
        tier = body.get("tier", "")
        remote_ref = body.get("remote_ref", "")
        dry_run = bool(body.get("dry_run", False))
        if not os.path.isdir(real):
            raise ValueError("path must be an existing directory")
        if tier not in KNOWN_BACKENDS:
            raise ValueError(f"tier must be one of {KNOWN_BACKENDS}")
        if not isinstance(remote_ref, str) or not remote_ref:
            raise ValueError("remote_ref is required")
        mod, merr = _engine(f"clouddog.stores.{tier}")
        if merr:
            _send_error(self, merr, 503)
            return
        inst, ierr = _backend_instance(mod)
        if ierr:
            _send_error(self, ierr, 503)
            return
        try:
            health = inst.health()
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"backend health probe failed: {exc}", 502)
            return
        if health.health.value != "live":
            _send_error(
                self,
                f"tier backend '{tier}' is not live ({health.health.value}): "
                f"{health.reason}. Relocate refused.",
                409,
            )
            return
        entries, merr2 = _relocate_manifest(real)
        if merr2:
            _send_error(self, merr2, 400)
            return
        if not entries:
            _send_error(self, "directory contains no files to relocate", 400)
            return
        shim = _relocate_shim(tier, remote_ref, entries)
        manifest_out = [
            {k: e[k] for k in ("rel", "size", "sha256")} for e in entries
        ]
        if dry_run:
            _send_json(
                self,
                {
                    "ok": True,
                    "dry_run": True,
                    "manifest": manifest_out,
                    "shim": shim,
                    "limits": _RELOCATE_LIMITS,
                },
            )
            return
        stored_refs = []
        try:
            for e in entries:
                per_file_ref = (
                    f"{remote_ref}/{e['rel']}" if tier != "github" else remote_ref
                )
                stored = inst.store(e["local"], per_file_ref, dry_run=False)
                if not inst.verify(e["local"], stored):
                    raise RuntimeError(f"verify failed after store: {e['rel']}")
                stored_refs.append(stored.to_dict())
        except Exception as exc:  # noqa: BLE001
            _send_error(
                self,
                f"relocate aborted during store/verify: {exc}. "
                "Original directory left untouched.",
                502,
            )
            return
        # verify-then-clear: every file verified -> delete original, write shim
        shim_path = real.rstrip(os.sep) + ".retrieve.sh"
        try:
            shutil.rmtree(real)
            with open(shim_path, "w", encoding="utf-8") as fh:
                fh.write(shim)
            os.chmod(shim_path, 0o755)
        except OSError as exc:
            _send_error(
                self,
                f"files are stored and verified, but clearing failed: {exc}. "
                "Nothing was deleted." if os.path.isdir(real)
                else f"shim write failed after clear: {exc}",
                500,
            )
            return
        _send_json(
            self,
            {
                "ok": True,
                "stored": stored_refs,
                "shim": shim_path,
                "limits": _RELOCATE_LIMITS,
            },
        )

    def _handle_torrent_add(self) -> None:
        body, err = _read_json_body(self)
        if err:
            _send_error(self, err, 400)
            return
        magnet = body.get("magnet", "")
        tier = body.get("tier", "")
        remote_ref = body.get("remote_ref", "")
        if not isinstance(magnet, str) or not magnet.startswith("magnet:"):
            raise ValueError("magnet must be a magnet: URI")
        if tier not in KNOWN_BACKENDS:
            raise ValueError(f"tier must be one of {KNOWN_BACKENDS}")
        if not isinstance(remote_ref, str) or not remote_ref:
            raise ValueError("remote_ref is required")
        mod, merr = _engine("clouddog.torrent")
        if merr:
            _send_error(self, merr, 503)
            return
        try:
            if hasattr(mod, "add_magnet"):
                torrent_id = mod.add_magnet(
                    magnet, tier=tier, remote_ref=remote_ref, backend=None
                )
            else:
                torrent_id = mod.add_torrent(magnet, tier=tier, remote_ref=remote_ref)
        except Exception as exc:  # noqa: BLE001
            _send_error(self, f"torrent add failed: {exc}", 502)
            return
        _send_json(self, {"ok": True, "id": torrent_id})


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def serve(port: int = 8899) -> None:
    """Serve the CloudDog dashboard on 127.0.0.1:port (blocking)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.daemon_threads = True
    try:
        print(f"CloudDog dashboard on http://127.0.0.1:{port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    serve(port)
