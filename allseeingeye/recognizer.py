"""Face recognition layer.

Detect faces (InsightFace / ArcFace via onnxruntime), embed them, and match
against a local identity store. Unknown faces are auto-enrolled so repeat
visits are recognized and counted.

Speed strategy for live/moving subjects: the caller (camera worker) throttles
how often we run and dedupes by track, and we only identify from a good crop
(large enough + sharp enough), so ArcFace runs sparingly on quality inputs.

Everything is local — the identity store is just files on disk.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import List, Optional, Tuple

import cv2
import numpy as np
from insightface.app import FaceAnalysis

MODEL_NAME = "buffalo_l"          # accurate; reuses the weights already cached
KNOWN_THRESHOLD = 0.42            # cosine similarity to count as the same person
MIN_FACE_PX = 60                  # ignore faces smaller than this (too far/blurry)
MIN_SHARPNESS = 40.0             # Laplacian variance floor (reject motion blur)

_app: Optional[FaceAnalysis] = None
_app_lock = threading.Lock()


def get_app(det_size: Tuple[int, int] = (640, 640)) -> FaceAnalysis:
    global _app
    with _app_lock:
        if _app is None:
            a = FaceAnalysis(name=MODEL_NAME, providers=["CPUExecutionProvider"])
            a.prepare(ctx_id=0, det_size=det_size)
            _app = a
    return _app


def detect(frame_bgr) -> list:
    """All faces in a frame (each has .bbox, .det_score, .normed_embedding)."""
    return get_app().get(frame_bgr)


def face_quality(frame_bgr, face) -> Tuple[int, float]:
    """(face_size_px, sharpness) for gating which crops are worth identifying."""
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    size = min(x2 - x1, y2 - y1)
    crop = frame_bgr[max(0, y1):y2, max(0, x1):x2]
    if crop.size == 0:
        return 0, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return size, float(cv2.Laplacian(gray, cv2.CV_64F).var())


def is_good_crop(frame_bgr, face) -> bool:
    size, sharp = face_quality(frame_bgr, face)
    return size >= MIN_FACE_PX and sharp >= MIN_SHARPNESS


def crop_face(frame_bgr, face, margin=0.25):
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    mx, my = int((x2 - x1) * margin), int((y2 - y1) * margin)
    x1, y1 = max(0, x1 - mx), max(0, y1 - my)
    x2, y2 = min(w, x2 + mx), min(h, y2 + my)
    return frame_bgr[y1:y2, x1:x2].copy()


class IdentityStore:
    """On-disk set of known people: metadata JSON + a matrix of embeddings.

    Matching is cosine similarity (embeddings are unit-norm), which for a home
    database (tens to low hundreds of identities) is a fast single matmul.
    """

    def __init__(self, base_dir: str):
        self.dir = base_dir
        self.thumbs_dir = os.path.join(base_dir, "known")
        os.makedirs(self.thumbs_dir, exist_ok=True)
        self.meta_path = os.path.join(base_dir, "identities.json")
        self.emb_path = os.path.join(base_dir, "embeddings.npy")
        self._lock = threading.Lock()
        self.identities: List[dict] = []
        self.embeddings = np.zeros((0, 512), np.float32)
        self._load()

    def _load(self):
        if os.path.exists(self.meta_path):
            try:
                with open(self.meta_path) as f:
                    self.identities = json.load(f)
            except (OSError, json.JSONDecodeError):
                self.identities = []
        if os.path.exists(self.emb_path):
            try:
                self.embeddings = np.load(self.emb_path)
            except (OSError, ValueError):
                self.embeddings = np.zeros((0, 512), np.float32)
        # Keep metadata and embeddings row-aligned.
        n = min(len(self.identities), len(self.embeddings))
        self.identities = self.identities[:n]
        self.embeddings = self.embeddings[:n]

    def _save(self):
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.identities, f)
        os.replace(tmp, self.meta_path)
        np.save(self.emb_path, self.embeddings)

    def match(self, emb) -> Tuple[Optional[int], float]:
        """Return (row_index, cosine_similarity) of the closest identity."""
        if len(self.embeddings) == 0:
            return None, 0.0
        sims = self.embeddings @ emb.astype(np.float32)
        i = int(np.argmax(sims))
        return i, float(sims[i])

    def add(self, emb, crop_bgr, name: Optional[str] = None, auto: bool = True) -> dict:
        with self._lock:
            iid = uuid.uuid4().hex[:8]
            if name is None:
                n_auto = sum(1 for x in self.identities if x.get("auto"))
                name = f"Unknown-{n_auto + 1}"
            thumb = f"{iid}.jpg"
            cv2.imwrite(os.path.join(self.thumbs_dir, thumb), crop_bgr)
            rec = {"id": iid, "name": name, "created": time.time(),
                   "thumb": thumb, "auto": auto}
            self.identities.append(rec)
            self.embeddings = np.vstack(
                [self.embeddings, emb.astype(np.float32)[None, :]])
            self._save()
            return rec

    def by_id(self, iid: str) -> Optional[dict]:
        return next((x for x in self.identities if x["id"] == iid), None)

    def rename(self, iid: str, name: str) -> bool:
        with self._lock:
            rec = self.by_id(iid)
            if not rec:
                return False
            rec["name"] = name
            rec["auto"] = False  # a named identity is no longer an auto-unknown
            self._save()
            return True

    def remove(self, iid: str) -> bool:
        with self._lock:
            idx = next((i for i, x in enumerate(self.identities) if x["id"] == iid), None)
            if idx is None:
                return False
            thumb = self.identities[idx].get("thumb")
            if thumb:
                try:
                    os.remove(os.path.join(self.thumbs_dir, thumb))
                except OSError:
                    pass
            del self.identities[idx]
            self.embeddings = np.delete(self.embeddings, idx, axis=0)
            self._save()
            return True

    def all(self) -> List[dict]:
        return list(self.identities)
