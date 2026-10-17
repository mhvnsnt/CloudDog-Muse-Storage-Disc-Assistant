"""TeraBox store backend.

Implements the proven tbupload.py protocol in-process (no shelling out):
precreate (/api/precreate) -> 4MB chunk uploads via curl to
c-jp.terabox.com/rest/2.0/pcs/superfile2 -> create (/api/create) ->
verify via /api/list (name + byte size).

Auth comes from the operator-managed credentials file
(default ~/workspace/tools/terabox-uploader/.creds.json) holding
``{"jstoken": ..., "cookies": {...}}``. Credentials are kept in memory
only; cookie and jsToken values are never printed or logged.

Honest verification note: the TeraBox list API exposes name + size only,
not content hashes. Byte-verification here is size-exact + name match;
the file sha256 is recorded at upload time and every chunk's md5 is
checked as it uploads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from urllib.parse import quote_plus

import requests

from . import (
    Store,
    StoredRef,
    StoreError,
    StoreUnavailable,
    VerificationFailed,
    _redact,
)

BASE = "https://www.terabox.com"
UP_HOST = "https://c-jp.terabox.com:443"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:121.0) Gecko/20100101 Firefox/121.0"
APP_ID = "250528"
CHUNK = 4 * 1024 * 1024  # 4MB parts (TeraBox superfile2 chunking)
DEFAULT_CREDS = os.path.expanduser("~/workspace/tools/terabox-uploader/.creds.json")
NET_TIMEOUT = 30
CHUNK_TIMEOUT = 600

_JSTOKEN_PATTERNS = [
    r'"jsToken"\s*:\s*"([^"]+)"',
    r"jsToken[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']",
]


class TeraBoxStore(Store):
    """TeraBox backend via the web API."""

    name = "terabox"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        creds_path = config.get("creds_path", DEFAULT_CREDS)
        try:
            with open(creds_path, encoding="utf-8") as fh:
                creds = json.load(fh)
            self._jstoken = str(creds.get("jstoken", ""))
            self._cookies = dict(creds["cookies"])
        except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
            raise StoreUnavailable(
                "TeraBox API credentials not configured. One-time setup is performed "
                "by the operator (see docs/OPERATIONS.md); CloudDog never handles "
                "raw cookie values."
            ) from exc
        self.remote_root = "/" + str(config.get("remote_root", "/CN-Gigaleak")).strip("/")

    # -- auth helpers ----------------------------------------------------

    def _resolve_jstoken(self) -> str:
        """Fetch a fresh jsToken from /main HTML. Raises StoreError on failure."""
        try:
            resp = requests.get(
                BASE + "/main",
                headers={"User-Agent": UA},
                cookies=self._cookies,
                timeout=20,
            )
            html = resp.text
        except requests.RequestException as exc:
            raise StoreError(f"TeraBox jsToken resolve failed: {_redact(str(exc))}") from exc
        for pat in _JSTOKEN_PATTERNS:
            match = re.search(pat, html)
            if match:
                return match.group(1)
        raise StoreError("TeraBox jsToken resolve failed: token not found in /main HTML")

    # -- protocol steps --------------------------------------------------

    def _list(self, remote_dir: str) -> list[dict]:
        """List a remote dir. Returns entry dicts; raises StoreError on failure."""
        params = {
            "app_id": APP_ID,
            "web": "1",
            "channel": "dubox",
            "clienttype": "5",
            "jsToken": self._jstoken,
            "dir": remote_dir,
            "num": "1000",
            "page": "1",
            "order": "time",
            "desc": "1",
            "showempty": "0",
        }
        try:
            resp = requests.get(
                BASE + "/api/list",
                headers={"User-Agent": UA, "Referer": BASE + "/main"},
                cookies=self._cookies,
                params=params,
                timeout=20,
            )
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise StoreError(
                f"TeraBox list failed for {remote_dir!r}: {_redact(str(exc))}"
            ) from exc
        if data.get("errno") != 0:
            raise StoreError(
                f"TeraBox list failed for {remote_dir!r}: errno={data.get('errno')} "
                f"errmsg={_redact(str(data.get('errmsg')))}"
            )
        return data.get("list", [])

    def _precreate(self, remote_path: str, remote_dir: str, block_list: str) -> str:
        try:
            resp = requests.post(
                BASE + "/api/precreate",
                headers={
                    "User-Agent": UA,
                    "Origin": BASE,
                    "Referer": BASE + "/main?category=all",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                cookies=self._cookies,
                data={
                    "app_id": APP_ID,
                    "web": "1",
                    "channel": "dubox",
                    "clienttype": "0",
                    "jsToken": self._jstoken,
                    "path": remote_path,
                    "autoinit": "1",
                    "target_path": remote_dir,
                    "block_list": block_list,
                },
                timeout=NET_TIMEOUT,
            )
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise StoreError(f"TeraBox precreate failed: {_redact(str(exc))}") from exc
        if "uploadid" not in data:
            raise StoreError(
                f"TeraBox precreate failed: errno={data.get('errno')} "
                f"errmsg={_redact(str(data.get('errmsg')))}"
            )
        return str(data["uploadid"])

    def _upload_chunks(
        self,
        local_path: str,
        remote_path: str,
        uploadid: str,
        md5s: list[str],
    ) -> None:
        """Upload each 4MB chunk via curl. Chunk temp files never survive the call."""
        cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        with open(local_path, "rb") as fh:
            for i, md5 in enumerate(md5s):
                chunk = fh.read(CHUNK)
                fd, tmp_path = tempfile.mkstemp(prefix="clouddog-tb-", suffix=".part", dir="/tmp")
                try:
                    try:
                        os.write(fd, chunk)
                    finally:
                        os.close(fd)
                    url = (
                        f"{UP_HOST}/rest/2.0/pcs/superfile2?method=upload&type=tmpfile"
                        f"&app_id={APP_ID}&path={quote_plus(remote_path)}"
                        f"&uploadid={uploadid}&partseq={i}"
                    )
                    cmd = [
                        "curl",
                        "-s",
                        "-X",
                        "POST",
                        "-H",
                        f"User-Agent:{UA}",
                        "-H",
                        f"Origin:{BASE}",
                        "-H",
                        f"Referer:{BASE}/main?category=all",
                        "-b",
                        cookie_str,
                        "-F",
                        f"file=@{tmp_path}",
                        url,
                    ]
                    try:
                        proc = subprocess.run(
                            cmd,
                            check=False,
                            capture_output=True,
                            timeout=CHUNK_TIMEOUT,
                        )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        raise StoreError(
                            f"TeraBox chunk {i} upload failed: {_redact(str(exc))}"
                        ) from exc
                    try:
                        body = json.loads(proc.stdout.decode("utf-8"))
                    except ValueError as exc:
                        raise StoreError(
                            f"TeraBox chunk {i} upload failed: non-JSON response"
                        ) from exc
                    if not isinstance(body, dict) or body.get("md5") != md5:
                        raise StoreError(
                            f"TeraBox chunk {i} upload failed: md5 mismatch "
                            f"({_redact(str(body)[:120])})"
                        )
                finally:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def _create(
        self,
        remote_path: str,
        remote_dir: str,
        uploadid: str,
        size: int,
        block_list: str,
    ) -> None:
        try:
            resp = requests.post(
                BASE + "/api/create",
                headers={
                    "User-Agent": UA,
                    "Origin": BASE,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                cookies=self._cookies,
                params={"isdir": "0", "rtype": "1", "app_id": APP_ID, "jsToken": self._jstoken},
                data={
                    "path": remote_path,
                    "uploadid": uploadid,
                    "target_path": remote_dir + "/",
                    "size": str(size),
                    "block_list": block_list,
                },
                timeout=NET_TIMEOUT,
            )
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise StoreError(f"TeraBox create failed: {_redact(str(exc))}") from exc
        if data.get("errno") != 0:
            raise StoreError(
                f"TeraBox create failed: errno={data.get('errno')} "
                f"errmsg={_redact(str(data.get('errmsg')))}"
            )

    # -- Store interface --------------------------------------------------

    def upload(
        self,
        local_path: str,
        remote_dir: str = "",
        remote_name: str | None = None,
    ) -> StoredRef:
        if not os.path.isfile(local_path):
            raise StoreError(f"Local file not found: {local_path!r}")
        remote_dir = "/" + (remote_dir or self.remote_root).strip("/")
        name = remote_name or os.path.basename(local_path)
        size = os.path.getsize(local_path)

        # Pass 1: sha256 + per-chunk md5s (streamed; file re-read for upload).
        digest = hashlib.sha256()
        md5s: list[str] = []
        with open(local_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                digest.update(chunk)
                md5s.append(hashlib.md5(chunk).hexdigest())

        if not self._jstoken:
            self._jstoken = self._resolve_jstoken()
        block_list = json.dumps(md5s)
        remote_path = f"{remote_dir}/{name}"

        uploadid = self._precreate(remote_path, remote_dir, block_list)
        self._upload_chunks(local_path, remote_path, uploadid, md5s)
        self._create(remote_path, remote_dir, uploadid, size, block_list)

        ref = StoredRef(
            store="terabox",
            remote_path=remote_path,
            size=size,
            sha256=digest.hexdigest(),
            uri=f"terabox:{remote_path}",
        )
        if not self.verify(ref):
            raise VerificationFailed(
                f"TeraBox upload of {local_path!r} did not verify at {remote_path!r}"
            )
        return ref

    def verify(self, ref: StoredRef) -> bool:
        """True iff an entry with matching name AND byte size exists right now.

        Limitation, stated honestly: the TeraBox list API exposes name + size
        only, not content hashes. Verification here is size-exact + name match;
        the sha256 was recorded at upload time and chunk md5s were checked
        during upload.
        """
        remote_dir, _, name = ref.remote_path.rpartition("/")
        remote_dir = remote_dir or "/"
        for entry in self._list(remote_dir):
            if entry.get("isdir") == 1:
                continue
            if entry.get("server_filename") == name and int(entry.get("size", -1)) == ref.size:
                return True
        return False

    def exists(self, remote_dir: str, remote_name: str) -> StoredRef | None:
        remote_dir = "/" + remote_dir.strip("/")
        for entry in self._list(remote_dir):
            if entry.get("isdir") == 1 or entry.get("server_filename") != remote_name:
                continue
            return StoredRef(
                store="terabox",
                remote_path=f"{remote_dir}/{remote_name}",
                size=int(entry.get("size", 0)),
                sha256="",  # the list API exposes no hashes
                uri=f"terabox:{remote_dir}/{remote_name}",
            )
        return None
