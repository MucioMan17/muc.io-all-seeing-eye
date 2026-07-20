"""Frame detectors.

MotionDetector: MOG2 background subtraction -> contour bounding boxes.
Works out of the box with zero model files; this is the default pipeline
and it's cheap enough to run per-frame on a Pi 5 / Pi 500.

DnnDetector: MobileNet-SSD (Caffe) object detection via OpenCV's DNN module.
Optional — enable with `detect.mode: dnn` after running
scripts/download-models.sh. Gives labeled boxes (person, car, dog, ...).
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import cv2
import numpy as np

from .tracker import Detection

log = logging.getLogger(__name__)

# Pascal VOC classes for the chuanqi305 MobileNet-SSD model.
VOC_CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse", "motorbike",
    "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor",
]

# Width frames are downscaled to before background subtraction.
MOTION_PROC_WIDTH = 640


def _merge_boxes(boxes: List[List[int]], pad: int = 10) -> List[List[int]]:
    """Union boxes that overlap (after padding) until stable, so one moving
    object fragmented into several contours becomes a single detection."""
    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        out: List[List[int]] = []
        while boxes:
            x, y, w, h = boxes.pop()
            i = 0
            while i < len(boxes):
                x2, y2, w2, h2 = boxes[i]
                if (x - pad < x2 + w2 and x + w + pad > x2 and
                        y - pad < y2 + h2 and y + h + pad > y2):
                    nx, ny = min(x, x2), min(y, y2)
                    w = max(x + w, x2 + w2) - nx
                    h = max(y + h, y2 + h2) - ny
                    x, y = nx, ny
                    boxes.pop(i)
                    merged = True
                else:
                    i += 1
            out.append([x, y, w, h])
        boxes = out
    return boxes


def in_ignore_zone(cx_norm: float, cy_norm: float, zones: List[dict]) -> bool:
    """True if a normalized (0..1) point falls inside any ignore rectangle."""
    return any(
        z["x"] <= cx_norm <= z["x"] + z["w"] and z["y"] <= cy_norm <= z["y"] + z["h"]
        for z in zones
    )


class MotionDetector:
    def __init__(self, min_area: int = 600, ignore: List[dict] | None = None):
        self.min_area = min_area
        self.ignore = ignore or []
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=32, detectShadows=True
        )
        self._frames_seen = 0
        self._zone_mask: Optional[np.ndarray] = None

    def _mask_for(self, w: int, h: int) -> Optional[np.ndarray]:
        """Binary mask (255 = watched, 0 = muted) at processing resolution."""
        if not self.ignore:
            return None
        if self._zone_mask is None or self._zone_mask.shape != (h, w):
            m = np.full((h, w), 255, np.uint8)
            for z in self.ignore:
                x0 = max(0, int(z["x"] * w))
                y0 = max(0, int(z["y"] * h))
                x1 = min(w, int((z["x"] + z["w"]) * w))
                y1 = min(h, int((z["y"] + z["h"]) * h))
                m[y0:y1, x0:x1] = 0
            self._zone_mask = m
        return self._zone_mask

    def detect(self, frame: np.ndarray) -> List[Detection]:
        h, w = frame.shape[:2]
        scale = w / MOTION_PROC_WIDTH if w > MOTION_PROC_WIDTH else 1.0
        small = cv2.resize(frame, (int(w / scale), int(h / scale))) if scale > 1.0 else frame

        mask = self.bg.apply(small)
        self._frames_seen += 1
        # Let the background model settle before reporting anything.
        if self._frames_seen < 30:
            return []

        # 127 = shadow pixels in MOG2; keep only confident foreground (255).
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        zone_mask = self._mask_for(small.shape[1], small.shape[0])
        if zone_mask is not None:
            mask = cv2.bitwise_and(mask, zone_mask)
        mask = cv2.dilate(mask, None, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_area_scaled = self.min_area / (scale * scale)
        boxes = [
            list(cv2.boundingRect(c))
            for c in contours
            if cv2.contourArea(c) >= min_area_scaled
        ]
        boxes = _merge_boxes(boxes)

        return [
            Detection(
                x=int(x * scale), y=int(y * scale),
                w=int(bw * scale), h=int(bh * scale),
                label="motion", conf=1.0,
            )
            for x, y, bw, bh in boxes
        ]


class DnnDetector:
    """MobileNet-SSD object detector. Raises FileNotFoundError if the model
    files aren't present so the caller can fall back to motion detection."""

    PROTOTXT = "MobileNetSSD_deploy.prototxt"
    WEIGHTS = "MobileNetSSD_deploy.caffemodel"

    def __init__(self, models_dir: str, confidence: float = 0.5, classes: List[str] | None = None):
        proto = os.path.join(models_dir, self.PROTOTXT)
        weights = os.path.join(models_dir, self.WEIGHTS)
        if not (os.path.exists(proto) and os.path.exists(weights)):
            raise FileNotFoundError(
                f"DNN model not found in {models_dir} — run scripts/download-models.sh"
            )
        self.net = cv2.dnn.readNetFromCaffe(proto, weights)
        self.confidence = confidence
        self.classes = set(classes) if classes else None

    def detect(self, frame: np.ndarray) -> List[Detection]:
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5
        )
        self.net.setInput(blob)
        out = self.net.forward()

        detections: List[Detection] = []
        for i in range(out.shape[2]):
            conf = float(out[0, 0, i, 2])
            if conf < self.confidence:
                continue
            cls = int(out[0, 0, i, 1])
            label = VOC_CLASSES[cls] if 0 <= cls < len(VOC_CLASSES) else "object"
            if self.classes and label not in self.classes:
                continue
            x1 = max(0, int(out[0, 0, i, 3] * w))
            y1 = max(0, int(out[0, 0, i, 4] * h))
            x2 = min(w, int(out[0, 0, i, 5] * w))
            y2 = min(h, int(out[0, 0, i, 6] * h))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(Detection(x=x1, y=y1, w=x2 - x1, h=y2 - y1, label=label, conf=conf))
        return detections
