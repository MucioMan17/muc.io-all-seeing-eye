"""Append-only sighting log: who was seen, when, on which camera.

Mirrors recorder.EventLog's JSONL approach. One line per sighting; a simple
reverse scan answers "recent sightings" and a day-grouped aggregation answers
"how many times / at what times was this face seen today".
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import List, Optional


class SightingLog:
    def __init__(self, root: str):
        self.root = root
        self.path = os.path.join(root, "sightings.jsonl")
        os.makedirs(root, exist_ok=True)
        self._lock = threading.Lock()

    def add(self, identity_id: str, name: str, camera: str, score: float,
            status: str, snapshot: Optional[str] = None) -> dict:
        rec = {
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "identity_id": identity_id,
            "name": name,
            "camera": camera,
            "score": round(float(score), 3),
            "status": status,             # "known" | "unknown"
            "snapshot": snapshot,
        }
        with self._lock, open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def _read(self) -> List[str]:
        try:
            with self._lock, open(self.path) as f:
                return f.readlines()
        except FileNotFoundError:
            return []

    def list(self, limit: int = 100, identity_id: Optional[str] = None,
             day: Optional[str] = None, status: Optional[str] = None) -> List[dict]:
        out: List[dict] = []
        for line in reversed(self._read()):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if identity_id and r.get("identity_id") != identity_id:
                continue
            if status and r.get("status") != status:
                continue
            if day and datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d") != day:
                continue
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def relabel(self, identity_id: str, name: str) -> int:
        """Rename all past sightings of one identity, so history reads
        consistently after a face is given a real name. Returns rows changed."""
        with self._lock:
            try:
                with open(self.path) as f:
                    lines = f.readlines()
            except FileNotFoundError:
                return 0
            out: List[str] = []
            changed = 0
            for line in lines:
                s = line.strip()
                if not s:
                    continue
                try:
                    r = json.loads(s)
                except json.JSONDecodeError:
                    out.append(s)
                    continue
                if r.get("identity_id") == identity_id:
                    r["name"] = name
                    changed += 1
                out.append(json.dumps(r))
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                f.write("\n".join(out) + ("\n" if out else ""))
            os.replace(tmp, self.path)
            return changed

    def summary(self, day: Optional[str] = None) -> List[dict]:
        """Per-identity count + list of times for a given day (default today)."""
        day = day or datetime.now().strftime("%Y-%m-%d")
        agg: dict = {}
        for line in self._read():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d") != day:
                continue
            k = r["identity_id"]
            a = agg.setdefault(k, {"identity_id": k, "name": r["name"],
                                   "count": 0, "first": None, "last": None, "times": []})
            a["name"] = r["name"]
            a["count"] += 1
            hhmmss = datetime.fromtimestamp(r["ts"]).strftime("%H:%M:%S")
            a["times"].append(hhmmss)
            a["first"] = a["first"] or hhmmss
            a["last"] = hhmmss
        return sorted(agg.values(), key=lambda x: x["count"], reverse=True)
