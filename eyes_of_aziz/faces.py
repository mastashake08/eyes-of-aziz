"""On-device face detection, so the bridge uploads faces instead of scenes.

Every upload is a face search on the backend (AWS Rekognition), and
Rekognition's SearchFacesByImage only searches the *largest* face in an
image. Detecting faces here and sending each one as its own crop means:

- frames with nobody in them are never uploaded (the bulk of the cost);
- every face in a crowd is searched, not just the biggest;
- whole scenes from someone's home never leave it.

Detection uses OpenCV's YuNet model (models/face_detection_yunet_2023mar.onnx,
MIT licensed, from github.com/opencv/opencv_zoo), which runs in real time on
a CPU.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MODEL_FILE = "face_detection_yunet_2023mar.onnx"

# Rekognition refuses images smaller than 80 x 80; crops are scaled up to at
# least this so a small, distant face still gets searched.
MIN_CROP_SIDE = 160


def model_path() -> Path:
    """The bundled model, whether running from source or a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "eyes_of_aziz" / "models" / MODEL_FILE


@dataclass(frozen=True)
class Face:
    x: int
    y: int
    width: int
    height: int
    score: float


class FaceDetector:
    def __init__(self, score_threshold: float = 0.8, min_face_size: int = 40) -> None:
        self._detector = cv2.FaceDetectorYN.create(str(model_path()), "", (320, 320), score_threshold)
        self._min_face_size = min_face_size

    def detect(self, frame: np.ndarray) -> list[Face]:
        """Faces in the frame, largest first, ignoring ones too small to match."""
        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, detections = self._detector.detect(frame)
        if detections is None:
            return []

        faces = [
            Face(int(x), int(y), int(w), int(h), float(row[-1]))
            for row in detections
            for x, y, w, h in [row[:4]]
            if min(w, h) >= self._min_face_size
        ]
        return sorted(faces, key=lambda face: face.width * face.height, reverse=True)


def crop_face(frame: np.ndarray, face: Face, padding: float = 0.4) -> np.ndarray:
    """The face with some margin around it (hair, chin, a little context),
    kept inside the frame and scaled up if it's very small."""
    frame_height, frame_width = frame.shape[:2]
    pad_x = int(face.width * padding)
    pad_y = int(face.height * padding)
    left = max(face.x - pad_x, 0)
    top = max(face.y - pad_y, 0)
    right = min(face.x + face.width + pad_x, frame_width)
    bottom = min(face.y + face.height + pad_y, frame_height)
    crop = frame[top:bottom, left:right]

    shortest = min(crop.shape[:2])
    if 0 < shortest < MIN_CROP_SIDE:
        scale = MIN_CROP_SIDE / shortest
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return crop


class MotionGate:
    """Skips face detection on a scene that hasn't changed, so an idle camera
    costs almost no CPU. Still checks at least every `recheck_every` frames,
    since someone standing still in view doesn't move.
    """

    def __init__(self, threshold: float = 0.01, recheck_every: int = 6) -> None:
        self._threshold = threshold
        self._recheck_every = recheck_every
        self._previous: np.ndarray | None = None
        self._skipped = 0

    def changed(self, frame: np.ndarray) -> bool:
        if self._threshold <= 0:
            return True

        small = cv2.resize(frame, (160, max(1, int(160 * frame.shape[0] / frame.shape[1]))))
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        previous, self._previous = self._previous, gray

        if previous is None or previous.shape != gray.shape:
            return True

        moved = float(np.mean(cv2.absdiff(previous, gray) > 25))
        if moved >= self._threshold or self._skipped >= self._recheck_every:
            self._skipped = 0
            return True

        self._skipped += 1
        return False
