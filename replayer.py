from __future__ import annotations

import argparse
import io
import logging
import subprocess
import time
import zipfile
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    stream_with_context,
)

from common import (
    get_record_root,
    is_video_file,
    live_enabled_cameras,
    load_config,
    recording_enabled_cameras,
)


LOGGER = logging.getLogger("replayer")


def create_app(config_path: str = "setting.json") -> Flask:
    config = load_config(config_path)
    record_root = get_record_root(config)
    recording_camera_by_id = {
        camera["id"]: camera for camera in recording_enabled_cameras(config)
    }
    live_camera_by_id = {
        camera["id"]: camera for camera in live_enabled_cameras(config)
    }
    recording_camera_ids = set(recording_camera_by_id)
    live_camera_ids = set(live_camera_by_id)
    visible_cameras = [
        {
            "id": camera["id"],
            "name": camera.get("name", camera["id"]),
            "recording_enabled": camera["id"] in recording_camera_ids,
            "live_enabled": camera["id"] in live_camera_ids,
        }
        for camera in config["cameras"]
        if camera["id"] in recording_camera_ids or camera["id"] in live_camera_ids
    ]
    live_config = config["replayer"]["live"]
    app = Flask(__name__)
    app.config["APP_CONFIG"] = config
    app.config["RECORD_ROOT"] = record_root

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            cameras=visible_cameras,
            live_enabled=bool(live_config["enabled"]),
        )

    @app.get("/api/cameras")
    def api_cameras():
        return jsonify(
            {"cameras": visible_cameras, "live_enabled": bool(live_config["enabled"])}
        )

    @app.get("/live/<camera_id>.mjpg")
    def live_stream(camera_id: str):
        if not live_config["enabled"] or camera_id not in live_camera_ids:
            abort(404)

        return Response(
            stream_with_context(
                _live_mjpeg_stream(live_camera_by_id[camera_id], live_config)
            ),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @app.get("/api/dates")
    def api_dates():
        camera_id = request.args.get("camera_id", "").strip()
        if camera_id not in recording_camera_ids:
            return jsonify({"dates": []})

        dates = []
        camera_dir = record_root / camera_id
        if camera_dir.exists():
            dates = sorted(
                [path.name for path in camera_dir.iterdir() if path.is_dir()],
                reverse=True,
            )
        return jsonify({"dates": dates})

    @app.get("/api/clips")
    def api_clips():
        camera_id = request.args.get("camera_id", "").strip()
        date = request.args.get("date", "").strip()
        if camera_id not in recording_camera_ids:
            return jsonify({"clips": []})

        day_dir = record_root / camera_id / date
        if not day_dir.exists():
            return jsonify({"clips": []})

        prefix = f"{camera_id}_{date}_"
        clips = []
        for path in sorted(day_dir.iterdir()):
            if not path.is_file() or not is_video_file(path):
                continue
            if not path.name.startswith(prefix):
                continue

            clip_label = _clip_label_from_path(path)
            clips.append(
                {
                    "name": path.name,
                    "label": clip_label,
                    "url": f"/recordings/{camera_id}/{date}/{path.name}",
                }
            )
        return jsonify({"clips": clips})

    @app.get("/recordings/<camera_id>/<date>/<path:filename>")
    def recording_file(camera_id: str, date: str, filename: str):
        if camera_id not in recording_camera_ids:
            abort(404)

        day_dir = (record_root / camera_id / date).resolve()
        try:
            day_dir.relative_to(record_root)
        except ValueError:
            abort(404)

        file_path = day_dir / filename
        if not file_path.exists():
            abort(404)

        download = request.args.get("download", "").strip().lower()
        as_attachment = download in {"1", "true", "yes", "on"}
        return send_from_directory(day_dir, filename, as_attachment=as_attachment)

    @app.get("/download/bundle")
    def download_bundle():
        camera_id = request.args.get("camera_id", "").strip()
        start_date = request.args.get("start_date", "").strip()
        start_name = request.args.get("start_name", "").strip()
        end_date = request.args.get("end_date", "").strip()
        end_name = request.args.get("end_name", "").strip()
        include_sync = request.args.get("include_sync", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if camera_id not in recording_camera_ids:
            abort(404)

        bundle_paths = _collect_bundle_paths(
            record_root=record_root,
            camera_ids=sorted(recording_camera_ids),
            camera_id=camera_id,
            start_date=start_date,
            start_name=start_name,
            end_date=end_date,
            end_name=end_name,
            include_sync=include_sync,
        )
        if not bundle_paths:
            abort(404)

        archive_name = _build_bundle_name(
            camera_id=camera_id,
            start_date=start_date,
            start_name=start_name,
            end_date=end_date,
            end_name=end_name,
            include_sync=include_sync,
        )
        archive_io = io.BytesIO()
        with zipfile.ZipFile(
            archive_io,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for path in bundle_paths:
                arcname = str(path.relative_to(record_root))
                archive.write(path, arcname=arcname)
        archive_io.seek(0)
        return send_file(
            archive_io,
            mimetype="application/zip",
            as_attachment=True,
            download_name=archive_name,
        )

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "record_root": str(record_root),
                "live_enabled": bool(live_config["enabled"]),
            }
        )

    return app


def _live_mjpeg_stream(camera: dict, live_config: dict):
    """Yield a browser-compatible MJPEG stream from one camera's RTSP feed.

    Each connected browser gets its own FFmpeg process. This keeps the endpoint
    simple and makes disconnect cleanup reliable; it is suitable for the small
    number of simultaneous viewers expected by this NVR scaffold.
    """
    reconnect_delay = max(float(live_config["reconnect_delay_seconds"]), 0.1)

    while True:
        process = _start_live_ffmpeg(camera, live_config)
        if process is None:
            return

        try:
            if process.stdout is None:
                return

            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            _stop_live_ffmpeg(process, camera["id"])

        LOGGER.warning("Live stream ended for camera %s; reconnecting", camera["id"])
        time.sleep(reconnect_delay)


def _start_live_ffmpeg(camera: dict, live_config: dict) -> subprocess.Popen | None:
    command = _build_live_ffmpeg_command(camera, live_config)
    LOGGER.info("Starting live stream for camera %s", camera["id"])
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError:
        LOGGER.error("ffmpeg binary not found: %s", live_config["ffmpeg_path"])
    except OSError as error:
        LOGGER.error("Failed to start live stream for camera %s: %s", camera["id"], error)
    return None


def _build_live_ffmpeg_command(camera: dict, live_config: dict) -> list[str]:
    target_fps = max(float(live_config["target_fps"]), 1.0)
    jpeg_quality = min(max(int(live_config["jpeg_quality"]), 2), 31)
    width = max(int(live_config["width"]), 0)
    video_filter = f"fps={target_fps}"
    if width:
        video_filter += f",scale={width}:-2"

    return [
        str(live_config["ffmpeg_path"]),
        "-hide_banner",
        "-loglevel",
        str(live_config["ffmpeg_loglevel"]),
        "-nostdin",
        "-rtsp_transport",
        str(live_config["rtsp_transport"]),
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-analyzeduration",
        "0",
        "-probesize",
        "32",
        "-i",
        str(camera["rtsp_url"]),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        video_filter,
        "-c:v",
        "mjpeg",
        "-q:v",
        str(jpeg_quality),
        "-f",
        "mpjpeg",
        "-boundary_tag",
        "frame",
        "pipe:1",
    ]


def _stop_live_ffmpeg(process: subprocess.Popen, camera_id: str) -> None:
    if process.poll() is not None:
        return

    LOGGER.info("Stopping live stream for camera %s", camera_id)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        LOGGER.warning("Live FFmpeg did not terminate for camera %s; killing", camera_id)
        process.kill()
        process.wait(timeout=5)


def _clip_label_from_path(path) -> str:
    parts = path.stem.split("_")
    if len(parts) < 3:
        return path.name

    start_time = parts[2].replace("-", ":")
    if len(parts) >= 4 and parts[3].startswith("part"):
        return f"{start_time} {parts[3]}"
    return start_time


def _list_camera_clips(record_root: Path, camera_id: str, date: str) -> list[Path]:
    day_dir = record_root / camera_id / date
    if not day_dir.exists():
        return []

    prefix = f"{camera_id}_{date}_"
    return [
        path
        for path in sorted(day_dir.iterdir())
        if path.is_file() and is_video_file(path) and path.name.startswith(prefix)
    ]


def _clip_time_token(path: Path) -> str | None:
    parts = path.stem.split("_")
    if len(parts) < 3:
        return None
    return parts[2]


def _collect_bundle_paths(
    record_root: Path,
    camera_ids: list[str],
    camera_id: str,
    start_date: str,
    start_name: str,
    end_date: str,
    end_name: str,
    include_sync: bool,
) -> list[Path]:
    if not all([start_date, start_name, end_date, end_name]):
        return []

    if start_date > end_date:
        return []

    camera_root = record_root / camera_id
    if not camera_root.exists():
        return []

    primary_paths: list[Path] = []
    date_cursor = sorted(
        {
            path.name
            for path in camera_root.iterdir()
            if path.is_dir()
        }
    )
    selected_dates = [date for date in date_cursor if start_date <= date <= end_date]
    for date in selected_dates:
        clips = _list_camera_clips(record_root, camera_id, date)
        for clip in clips:
            if date == start_date and clip.name < start_name:
                continue
            if date == end_date and clip.name > end_name:
                continue
            primary_paths.append(clip)

    if not primary_paths:
        return []

    if not include_sync:
        return primary_paths

    bundle_map: dict[str, Path] = {str(path): path for path in primary_paths}
    other_camera_ids = [item for item in camera_ids if item != camera_id]
    for primary_path in primary_paths:
        date = primary_path.parent.name
        time_token = _clip_time_token(primary_path)
        if not time_token:
            continue

        for other_camera_id in other_camera_ids:
            for candidate in _list_camera_clips(record_root, other_camera_id, date):
                candidate_time = _clip_time_token(candidate)
                if candidate_time == time_token:
                    bundle_map[str(candidate)] = candidate

    return [bundle_map[key] for key in sorted(bundle_map)]


def _build_bundle_name(
    camera_id: str,
    start_date: str,
    start_name: str,
    end_date: str,
    end_name: str,
    include_sync: bool,
) -> str:
    start_token = Path(start_name).stem
    end_token = Path(end_name).stem
    suffix = "_with_sync" if include_sync else ""
    return f"{camera_id}_{start_date}_{start_token}_to_{end_date}_{end_token}{suffix}.zip"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CCTV clip replay server")
    parser.add_argument(
        "--config",
        default="setting.json",
        help="Path to JSON config file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    app = create_app(args.config)
    replayer_config = config["replayer"]
    app.run(
        host=replayer_config["host"],
        port=int(replayer_config["port"]),
        debug=bool(replayer_config["debug"]),
    )


if __name__ == "__main__":
    main()
