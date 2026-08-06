"""Script entrypoint for the native desktop app.

Launched by the .app bundle as a plain script path (not `python -m ...`).
"""

import os
import sys

# Quiet OpenCV/ffmpeg so they don't print raw RTSP URLs (with credentials) on
# connection errors. Must be set before cv2 is imported.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Send noisy C-level stderr (ffmpeg/h264 "SEI ... truncated" decoder warnings)
# to a log file so the console stays clean however the app is launched. Real
# errors still land in run/engine.log.
try:
    os.makedirs(os.path.join(HERE, "run"), exist_ok=True)
    _errlog = open(os.path.join(HERE, "run", "engine.log"), "ab")
    os.dup2(_errlog.fileno(), 2)
except Exception:
    pass

if "--config" not in sys.argv:
    sys.argv += ["--config", os.path.join(HERE, "config", "local.yml")]

from allseeingeye.desktop import main  # noqa: E402

main()
