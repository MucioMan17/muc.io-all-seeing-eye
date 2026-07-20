"""Multi-object tracking.

Greedy centroid tracker: detections from each frame are matched to existing
tracks by nearest centroid. Tracks accumulate a trail of recent centroids
(used by the UI for the tracer line) and survive a few missed frames so
brief detection dropouts don't reset identities.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Tuple


@dataclass
class Detection:
    x: int
    y: int
    w: int
    h: int
    label: str = "motion"
    conf: float = 1.0

    @property
    def centroid(self) -> Tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)


@dataclass
class Track:
    id: int
    box: Tuple[int, int, int, int]
    label: str
    conf: float
    trail: Deque[Tuple[int, int]] = field(default_factory=lambda: deque(maxlen=48))
    hits: int = 1
    missed: int = 0

    @property
    def centroid(self) -> Tuple[float, float]:
        x, y, w, h = self.box
        return (x + w / 2.0, y + h / 2.0)


class CentroidTracker:
    def __init__(self, max_distance: float = 120.0, max_missed: int = 12, min_hits: int = 3,
                 report_grace: int = 3):
        self._ids = itertools.count(1)
        self.tracks: Dict[int, Track] = {}
        self.max_distance = max_distance
        self.max_missed = max_missed
        # A track must be seen this many frames before it's reported,
        # which suppresses one-frame noise blobs.
        self.min_hits = min_hits
        # Classified (non-"motion") tracks stay visible through this many
        # missed frames, bridging the gap between DNN passes.
        self.report_grace = report_grace

    def _inside_classified(self, det: Detection) -> bool:
        """Is this detection's center inside a known classified object?
        Used to stop motion fragments (a waving hand, a turning head) from
        spawning duplicate tracks on top of a detected person/car."""
        cx, cy = det.centroid
        for t in self.tracks.values():
            if t.label == "motion":
                continue
            x, y, w, h = t.box
            px, py = w * 0.2, h * 0.2
            if x - px <= cx <= x + w + px and y - py <= cy <= y + h + py:
                return True
        return False

    def update(self, detections: List[Detection], authoritative: bool = True) -> List[Track]:
        """Match detections to tracks.

        authoritative=True (DNN passes, or motion-only mode): detections
        define box geometry and labels.
        authoritative=False (motion frames between DNN passes): matches only
        keep tracks alive — a classified track's box/label/trail are left
        exactly as the DNN drew them, so partial-motion fragments can't
        shrink a person box down to a moving hand.
        """
        unmatched = list(detections)
        # Match existing tracks to the nearest detection, closest pairs first.
        pairs = []
        for tid, track in self.tracks.items():
            tc = track.centroid
            for i, det in enumerate(unmatched):
                dc = det.centroid
                dist = math.hypot(tc[0] - dc[0], tc[1] - dc[1])
                if dist <= self.max_distance:
                    pairs.append((dist, tid, i))
        pairs.sort(key=lambda p: p[0])

        used_tracks, used_dets = set(), set()
        for dist, tid, di in pairs:
            if tid in used_tracks or di in used_dets:
                continue
            used_tracks.add(tid)
            used_dets.add(di)
            det = unmatched[di]
            track = self.tracks[tid]
            track.hits += 1
            track.missed = 0
            if authoritative or track.label == "motion":
                track.box = (det.x, det.y, det.w, det.h)
                # A classified label (dnn) wins over the generic "motion" label.
                if det.label != "motion" or track.label == "motion":
                    track.label = det.label
                    track.conf = det.conf
                cx, cy = track.centroid
                track.trail.append((int(cx), int(cy)))
            # else: a motion hint on a classified track — geometry untouched.

        # Age out pre-existing tracks that missed this frame (before adding
        # new ones, so a track can't be aged in the update that created it).
        for tid in list(self.tracks):
            if tid in used_tracks:
                continue
            track = self.tracks[tid]
            track.missed += 1
            if track.missed > self.max_missed:
                del self.tracks[tid]

        # Unmatched detections start new tracks — except motion fragments
        # that are just pieces of an object the DNN already boxed.
        for i, det in enumerate(unmatched):
            if i in used_dets:
                continue
            if not authoritative and self._inside_classified(det):
                continue
            tid = next(self._ids)
            track = Track(id=tid, box=(det.x, det.y, det.w, det.h), label=det.label, conf=det.conf)
            cx, cy = track.centroid
            track.trail.append((int(cx), int(cy)))
            self.tracks[tid] = track

        return [
            t for t in self.tracks.values()
            if t.hits >= self.min_hits
            and t.missed <= (0 if t.label == "motion" else self.report_grace)
        ]
