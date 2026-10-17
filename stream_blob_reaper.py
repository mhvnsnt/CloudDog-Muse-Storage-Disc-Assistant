#!/usr/bin/env python3
"""
Stream Blob Reaper
Deletes local files immediately as each upload hash verifies against GitHub/TeraBox.
Enforces: Disk falls while upload runs instead of after.
"""

import os
import sys
import hashlib
import json
from pathlib import Path

def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192 * 1024):
            h.update(chunk)
    return h.hexdigest()

def reap_blob_on_verify(local_file_path, remote_hash, manifest_path="manifest.json"):
    local_hash = compute_sha256(local_file_path)
    if local_hash == remote_hash:
        size = os.path.getsize(local_file_path)
        os.unlink(local_file_path)
        print(f"✓ Verified {local_file_path.name} -> Deleted on the spot! Freed {size / 1e6:.1f} MB")
        
        # Keep manifest reference
        manifest = {}
        if os.path.exists(manifest_path):
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
        manifest[local_file_path.name] = {"hash": remote_hash, "size": size, "status": "stored_remote"}
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        return True
    else:
        print(f"❌ Hash mismatch for {local_file_path.name}! Preserving local file.")
        return False

if __name__ == "__main__":
    print("Stream Blob Reaper initialized. Watching in-flight upload lanes...")
