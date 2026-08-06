# CCTV NVR

Simple Python-based CCTV NVR scaffold for RTSP cameras.

The project provides:

- `recorder.py`: connects to multiple RTSP cameras and saves 1-minute H.264 MP4 clips with `ffmpeg`
- `replayer.py`: Flask-based replay server for browsing and downloading recorded clips
- `setting.example.json`: example camera and storage configuration

## File layout

- `common.py`: shared config and cleanup helpers
- `recorder.py`: multi-camera recorder
- `replayer.py`: replay web server
- `templates/index.html`: replay UI
- `setting.example.json`: example camera, storage, and server settings
- `requirements.txt`: Python dependencies
- `bin/run_recorder.sh`: recorder launcher for systemd
- `bin/run_replayer.sh`: replayer launcher for systemd
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
- storage path and retention policy
- `ffmpeg` path, RTSP transport, FPS, codec, preset, CRF, segment length
- replay server host and port

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
  "enabled": true,
  "rtsp_url": "rtsp://username:password@192.168.0.10:554/stream1"
}
```

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

Open the replay page in a browser:

```text
http://localhost:8080
```

## Replayer features

- Browse clips by camera and date
- Play clips at `0.5x` to `4.0x`
- Automatically move to the next clip when playback ends
- Download the currently selected clip
- Download a selected clip range as a ZIP archive
- Optionally include same-time clips from other configured cameras in downloads

## systemd service

Install and start both services:

```bash
chmod +x bin/run_recorder.sh bin/run_replayer.sh install_systemd.sh
./install_systemd.sh
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
sudo systemctl restart cctv-recorder.service
sudo systemctl restart cctv-replayer.service
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
