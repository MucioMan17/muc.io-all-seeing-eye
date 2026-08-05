# Running on Windows

The native desktop app (add/remove cameras, live tracking, face recognition)
runs on Windows. Your TP-Link Tapo (or any RTSP) camera works cross-platform.

## Quick start

1. Install **Python 3.11–3.12** from [python.org](https://www.python.org/downloads/windows/)
   — during setup, check **"Add python.exe to PATH"**.
2. Install **Git** (or download the repo as a ZIP).
3. Clone this repo somewhere simple, e.g. `C:\Users\<you>\muc.io-all-seeing-eye`:
   ```
   git clone https://github.com/MucioMan17/muc.io-all-seeing-eye.git
   ```
4. Double-click **`run-windows.bat`**.
   - First run creates a virtual environment and installs everything
     (OpenCV, InsightFace, onnxruntime, FAISS…). This takes a few minutes and
     downloads the face model (~300 MB) on first face detection.
   - Later runs just launch the app.

The window opens on a synthetic demo camera. Click **➕ Add camera** and enter
your Tapo details (IP, the **Camera Account** username/password from the Tapo
app, stream `stream1`, port `554`). Faces are then detected, matched, enrolled,
and logged locally.

## Notes

- **Your camera password** is stored only in `config/local.yml` on your machine,
  which is git-ignored — it never gets pushed.
- **GPU (optional):** face detection runs on CPU by default and is fine. To use
  your NVIDIA GPU later, swap `onnxruntime` for `onnxruntime-gpu` in
  `requirements-app.txt` and reinstall.
- **Recorded-clip playback (optional):** install `ffmpeg` and put it on PATH.
- The data the app writes — identities and a `sightings.jsonl` log — lives under
  `run/faces/`. That's what the voice assistant will read to answer questions
  about who's been seen.
