# CCTV NVR

Simple Python-based CCTV NVR scaffold for RTSP cameras.

The project provides:

- `recorder.py`: connects to multiple RTSP cameras and saves 1-minute H.264 MP4 clips with `ffmpeg`
- `replayer.py`: Flask-based viewer with live RTSP viewing plus recorded-clip replay
- `setting.example.json`: example camera and storage configuration

## File layout

- `common.py`: shared config and cleanup helpers
- `recorder.py`: multi-camera recorder
- `replayer.py`: replay web server
- `templates/index.html`: live and replay viewer UI
- `setting.example.json`: example camera, storage, and server settings
- `requirements.txt`: Python dependencies
- `bin/run_recorder.sh`: recorder launcher for systemd
- `bin/run_replayer.sh`: replayer launcher for systemd
- `recorder_healthcheck.py`: detects a stopped or stalled recorder and restarts it
- `systemd/`: systemd unit templates
- `install_systemd.sh`: installs systemd services into `/etc/systemd/system`

## Recording behavior

- Recordings are stored under `record_root/<camera-id>/<YYYY-MM-DD>/`
- Each file is a 1-minute H.264 MP4 clip produced by `ffmpeg`
- The current default recorder setting uses `5 fps`
- File name format:

```text
<camera-id>_<YYYY-MM-DD>_<HH-MM-SS>.mp4
```

- Old files are removed automatically by:
  - maximum storage size
  - maximum retention days

## Configuration

Edit `setting.json` yourself for:

- RTSP address
- user ID / password
- camera ID and display name
- independent recording and live-display enablement per camera
- storage path and retention policy
- `ffmpeg` path, RTSP transport, FPS, codec, preset, CRF, segment length
- replay server host and port, plus optional live-view settings

Current default recorder values:

```json
{
  "segment_seconds": 60,
  "target_fps": 5,
  "video_codec": "libx264",
  "encoder_preset": "veryfast",
  "crf": 23
}
```

Example camera entry:

```json
{
  "id": "cam01",
  "name": "Front Gate",
  "recording_enabled": true,
  "live_enabled": true,
  "rtsp_url": "rtsp://username:password@192.168.0.10:554/stream1"
}
```

Set `recording_enabled` to `false` and `live_enabled` to `true` for a camera
that should appear in Live view without saving any clips. The legacy `enabled`
setting remains supported for existing configurations; when the new settings
are absent, it controls both recording and live display.

`replayer.live.enabled` is still the global master switch for Live view and
must also be `true` for any per-camera live display to be available.

Create your local config from the example before running:

```bash
cp setting.example.json setting.json
```

## Setup

1. Activate the Python environment.

```bash
source pyenv.sh
```

2. Install dependencies.

```bash
pip install -r requirements.txt
```

## Run

Start the recorder:

```bash
python3 recorder.py
```

Start the replay server:

```bash
python3 replayer.py
```

Open the viewer in a browser:

```text
http://localhost:8080
```

## Viewer modes

The viewer has two modes:

- **Replay** (the default): browse, play, and download recorded MP4 clips.
- **Live**: view the selected camera's current RTSP feed.

Live view is relayed through the server as MJPEG because web browsers cannot
play RTSP URLs directly. RTSP credentials therefore remain in `setting.json`
and are never sent to the browser. Each open live-view tab uses one FFmpeg
decoder process; close the tab or switch back to Replay to stop it.

Live-view settings are under `replayer.live`. `width: 0` preserves the source
width; setting a positive width can reduce CPU and network usage. Lower
`jpeg_quality` values produce higher-quality, larger frames (FFmpeg range 2–31).

## Replay features

- Browse clips by camera and date
- Play clips at `0.5x` to `4.0x`
- Automatically move to the next clip when playback ends
- Download the currently selected clip
- Download a selected clip range as a ZIP archive
- Optionally include same-time clips from other configured cameras in downloads

## systemd services

Install and start both services (also enables them at boot):

```bash
chmod +x bin/run_recorder.sh bin/run_replayer.sh bin/run_recorder_healthcheck.sh install_systemd.sh
./install_systemd.sh
```

Start services after they have been installed:

```bash
sudo systemctl start cctv-recorder.service cctv-replayer.service
```

After changing `setting.json` or updating the application, restart both
services so recording and live-display changes take effect:

```bash
sudo systemctl restart cctv-recorder.service cctv-replayer.service
```

Check status:

```bash
sudo systemctl status cctv-recorder.service
sudo systemctl status cctv-replayer.service
```

View logs:

```bash
sudo journalctl -u cctv-recorder.service -f
sudo journalctl -u cctv-replayer.service -f
```

Restart services:

```bash
sudo systemctl restart cctv-recorder.service cctv-replayer.service
```

### Recorder health check

`cctv-recorder-healthcheck.timer` runs every five minutes. It restarts
`cctv-recorder.service` when either the service is not active or a
recording-enabled camera has not updated an MP4 recording for five minutes.
The check only scans today's and yesterday's recording directories, so it does
not traverse the full archive.

Check its result with:

```bash
sudo systemctl status cctv-recorder-healthcheck.timer
sudo journalctl -u cctv-recorder-healthcheck.service -n 50 --no-pager
```

Stop or disable:

```bash
sudo systemctl stop cctv-recorder.service cctv-replayer.service
sudo systemctl disable cctv-recorder.service cctv-replayer.service
```

## Notes

- `ffmpeg` and `libx264` are used to create browser-friendly H.264 MP4 clips.
- The replay UI supports playback speed up to 4x, automatic next-clip playback, single-clip download, and ZIP range download.
- ZIP downloads can optionally include same-time clips from other configured cameras.
- `CRF` controls the quality/size tradeoff for H.264 encoding: lower is higher quality and larger files, higher is lower quality and smaller files.
- This is a practical scaffold, not a production-hardened NVR.
