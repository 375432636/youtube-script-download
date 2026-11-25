"""Download YouTube caption tracks and expose a resumable Drive upload queue."""

import argparse
from email.utils import parsedate_to_datetime
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import DownloadError, ExtractorError

from .manifest import export_index, pending_uploads, print_json, summary
from .sources import Video, channel_label, inventory, normalize_source
from .state import ArchiveState


ACCESS_MESSAGES = (
    "private video", "sign in to confirm", "members-only", "members only",
    "not available in your country", "video unavailable", "not made this video available",
    "confirm your age", "join this channel", "http error 403", "not available for playback",
    "premium-only", "removed by the uploader", "has been removed", "account has been terminated",
)
MISSING_TRACK_MESSAGES = ("missing subtitles languages", "subtitles are missing", "failed to download subtitle")
SRT_CUE = re.compile(rb"\d{2,}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2,}:\d{2}:\d{2},\d{3}")


class RateLimited(Exception):
    def __init__(self, seconds=900):
        super().__init__("YouTube returned HTTP 429")
        self.seconds = seconds


def retry_seconds(value, now=None):
    if value:
        try:
            return int(value)
        except ValueError:
            try:
                return int(parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now))
            except (ValueError, TypeError, OverflowError):
                pass
    return 900


class CaptureLog:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        pass

    def warning(self, message):
        self.messages.append(message)

    def error(self, message):
        self.warning(message)


def raw_variants(tracks, automatic):
    for language, variants in (tracks or {}).items():
        candidates = []
        for variant in variants:
            query = parse_qs(urlsplit(variant.get("url") or "").query)
            if not automatic or not query.get("tlang"):
                candidates.append(variant)
        if candidates:
            yield language, candidates


def caption_tracks(manual, automatic):
    labeled = {}
    manual_languages = set(manual or {})
    for kind, source in (("manual", manual), ("auto", automatic)):
        signature_labels = set()
        for language, variants in raw_variants(source, kind == "auto"):
            normalized_language = re.sub(r"[^0-9A-Za-z_-]", "_", language.removesuffix("-orig") if kind == "auto" else language)
            if not normalized_language:
                continue
            if kind == "auto" and normalized_language in manual_languages:
                # YouTube's HLS manifest can contain the same manual track; yt-dlp
                # lists these untyped renditions among automatic captions.
                variants = [
                    variant for variant in variants
                    if variant.get("protocol") not in {"m3u8", "m3u8_native"}
                    or "auto-generated" in (variant.get("name") or "").lower()
                    or parse_qs(urlsplit(variant.get("url") or "").query).get("kind") == ["asr"]
                ]
                if not variants:
                    continue
            signature = (normalized_language, tuple(sorted({
                (v.get("ext") or "", v.get("url") or "", v.get("data") or "")
                for v in variants
            })))
            if signature in signature_labels:
                continue
            signature_labels.add(signature)
            label = f"{normalized_language}.{kind}"
            if label in labeled:
                serial = 2
                while f"{label}.{serial}" in labeled:
                    serial += 1
                label = f"{label}.{serial}"
            labeled[label] = variants
    return labeled


class CaptionDownloader(YoutubeDL):
    def urlopen(self, request):
        try:
            return super().urlopen(request)
        except HTTPError as exc:
            if exc.status == 429:
                headers = getattr(exc.response, "headers", {})
                raise RateLimited(retry_seconds(headers.get("Retry-After"))) from exc
            raise

    def process_video_result(self, info_dict, download=True):
        if info_dict.get("live_status") in {"is_live", "is_upcoming"}:
            return info_dict
        return super().process_video_result(info_dict, download=download)

    def process_subtitles(self, video_id, normal_subtitles, automatic_captions):
        labeled = caption_tracks(normal_subtitles, automatic_captions)
        return super().process_subtitles(video_id, labeled, {})


def local_tracks(stage, directory, video_id):
    results = []
    for part in sorted(stage.glob(f"{video_id}.*")):
        bits = part.name.split(".")
        if len(bits) < 4 or bits[2] not in {"manual", "auto"} or part.suffix == ".part":
            continue
        output = stage / ("converted-" + ".".join(bits[:-1]) + ".srt")
        if part.suffix == ".srt" and SRT_CUE.search(part.read_bytes()):
            content = part.read_bytes()
        else:
            converted = subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(part), str(output)],
                capture_output=True,
            )
            if converted.returncode or not output.is_file():
                continue
            content = output.read_bytes()
        if not SRT_CUE.search(content):
            continue
        filename = ".".join(bits[:-1]) + ".srt"
        destination = directory / "subtitles" / video_id / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(".srt.tmp")
        temp.write_bytes(content)
        temp.replace(destination)
        results.append({
            "video_id": video_id,
            "filename": filename,
            "language": bits[1],
            "kind": bits[2],
            "sha256": hashlib.sha256(content).hexdigest(),
            "md5": hashlib.md5(content, usedforsecurity=False).hexdigest(),
            "nbytes": len(content),
        })
    return results


def fetch_video(video, directory):
    logger = CaptureLog()
    info = {}
    error = None
    with TemporaryDirectory(dir=directory) as temp:
        stage = Path(temp)
        settings = {
            "logger": logger,
            "skip_download": True,
            "noplaylist": True,
            "cachedir": False,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["all"],
            "subtitlesformat": "srt/vtt/best",
            "ignore_no_formats_error": True,
            "ignoreerrors": True,
            "js_runtimes": {"node": {}},
            "extractor_args": {"youtube": {"skip": ["translated_subs", "dash"]}},
            "outtmpl": str(stage / "%(id)s.%(ext)s"),
            "progress_hooks": [],
            "retries": 0,
            "fragment_retries": 0,
            "extractor_retries": 0,
            "socket_timeout": 30,
            "sleep_interval_subtitles": 2,
        }
        with CaptionDownloader(settings) as downloader:
            try:
                info = downloader.extract_info(Video(video["id"]).url, download=True) or {}
            except (DownloadError, ExtractorError, OSError, RateLimited) as exc:
                error = exc
        tracks = local_tracks(stage, directory, video["id"])
    metadata = {"title": info.get("title") or "", "channel": channel_label(info),
                "channel_id": info.get("channel_id") or ""}
    messages = " ".join(logger.messages + ([str(error)] if error else []))
    lower = messages.lower()
    expected = set(info.get("requested_subtitles") or {})
    written = {track["filename"].removeprefix(video["id"] + ".").removesuffix(".srt") for track in tracks}
    if isinstance(error, RateLimited) or "http error 429" in lower or "too many requests" in lower:
        return "rate_limited", "YouTube returned HTTP 429", tracks, metadata, getattr(error, "seconds", 900)
    if info.get("live_status") in {"is_live", "is_upcoming"}:
        status, detail = "restricted", "Live stream has not ended"
    elif any(marker in lower for marker in MISSING_TRACK_MESSAGES):
        status, detail = "restricted", "YouTube restricted access to one or more subtitle tracks"
    elif tracks and expected == written and "unable to download video subtitles" not in lower:
        status, detail = "downloaded", ""
    elif any(marker in lower for marker in ACCESS_MESSAGES):
        status, detail = "restricted", "YouTube did not allow access to the video or some captions"
    elif tracks or expected or error or not info:
        status, detail = "failed", "One or more requested subtitle tracks could not be retrieved or read"
    else:
        status, detail = "no_subtitles", ""
    return status, detail, tracks, metadata, 0


def representative_order(videos):
    selected = []
    for category, target in (("video", 18), ("short", 6), ("live", 3)):
        group = [video for video in videos if video["category"] == category]
        count = min(len(group), target)
        if count == 1:
            selected.extend(group)
        if count > 1:
            selected.extend(group[round(i * (len(group) - 1) / (count - 1))] for i in range(count))
    ids = {video["id"] for video in selected}
    return selected + [video for video in videos if video["id"] not in ids]


def run(state, source, limit, retry_failed, representative):
    if not shutil.which("ffmpeg") or not shutil.which("node"):
        raise ValueError("Install FFmpeg and Node.js before downloading subtitles.")
    eligible = {"pending", "rate_limited"}
    if retry_failed:
        eligible.add("failed")
    with state.worker():
        candidates = [video for video in state.videos(source) if video["status"] in eligible]
        if representative:
            candidates = representative_order(candidates)
        for video in candidates[:limit]:
            state.reserve()
            status, detail, tracks, metadata, retry = fetch_video(video, state.directory)
            state.finish(video, status, detail, tracks, metadata)
            print_json({"video_id": video["id"], "status": status, "tracks": len(tracks), "detail": detail})
            if status == "rate_limited":
                print_json({"paused_until": state.block(retry), "source": source})
                return 75
    return 0


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=".state")
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("inventory")
    discover.add_argument("source")
    download = commands.add_parser("run")
    download.add_argument("source")
    download.add_argument("--limit", type=int, help="Optional maximum videos in this invocation.")
    download.add_argument("--retry-failed", action="store_true")
    download.add_argument("--representative", action="store_true", help="Prioritize samples spread across ages and formats.")
    queued = commands.add_parser("queue")
    queued.add_argument("source")
    queued.add_argument("--limit", type=int)
    status = commands.add_parser("status")
    status.add_argument("source")
    index = commands.add_parser("export")
    index.add_argument("source")
    index.add_argument("output")
    index.add_argument("--batch", type=int)
    ack = commands.add_parser("ack")
    ack.add_argument("video_id")
    ack.add_argument("filename")
    ack.add_argument("sha256")
    ack.add_argument("drive_id")
    ack_batch = commands.add_parser("ack-json")
    ack_batch.add_argument("receipts", help="File containing a JSON list of verified upload receipts.")
    return parser


def main(argv=None):
    args = argument_parser().parse_args(argv)
    try:
        state = ArchiveState(args.state_dir)
        source = normalize_source(args.source)[0] if hasattr(args, "source") else None
        match args.command:
            case "inventory":
                log = CaptureLog()
                videos = inventory(source, log)
                state.add_source(source, videos)
                print_json({"source": source, "videos": len(videos)})
            case "run":
                if args.limit is not None and args.limit < 1:
                    raise ValueError("--limit must be positive.")
                if not state.videos(source):
                    raise ValueError("Run inventory for this source before downloading.")
                return run(state, source, args.limit, args.retry_failed, args.representative)
            case "status":
                print_json(summary(state, source))
            case "queue":
                print_json(pending_uploads(state, source, args.limit))
            case "export":
                export_index(state, source, args.output, args.batch)
            case "ack":
                state.record_upload(args.video_id, args.filename, args.sha256, args.drive_id)
            case "ack-json":
                import json

                with open(args.receipts, encoding="utf-8") as receipts:
                    for receipt in json.load(receipts):
                        state.record_upload(receipt["video_id"], receipt["filename"], receipt["sha256"], receipt["drive_id"])
    except (ValueError, DownloadError, ExtractorError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
