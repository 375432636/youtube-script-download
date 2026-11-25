"""Expose safe publication metadata and private transfer instructions."""

from collections import defaultdict
import csv
import json
from pathlib import Path


def pending_uploads(state, source, limit):
    rows = []
    for track in state.tracks(source, pending_only=True, limit=limit):
        item = dict(track)
        item["file_path"] = str(state.directory / "subtitles" / item["video_id"] / item["filename"])
        rows.append(item)
    return rows


def summary(state, source):
    tracks = defaultdict(list)
    for track in state.tracks(source):
        tracks[track["video_id"]].append(track["drive_id"] is not None)
    counts = defaultdict(int)
    for video in state.videos(source):
        receipts = tracks[video["id"]]
        status = "archived" if video["status"] == "downloaded" and receipts and all(receipts) else video["status"]
        counts[status] += 1
    total_tracks = sum(map(len, tracks.values()))
    archived_tracks = sum(sum(receipts) for receipts in tracks.values())
    return {"video_statuses": dict(counts), "tracks": total_tracks, "drive_verified": archived_tracks}


def export_index(state, source, output, batch=None):
    by_video = defaultdict(list)
    for track in state.tracks(source):
        by_video[track["video_id"]].append(track)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(("video_id", "url", "category", "language", "kind", "status"))
        for video in state.videos(source):
            if batch is not None and video["batch"] != batch:
                continue
            url = f"https://www.youtube.com/watch?v={video['id']}"
            for track in by_video[video["id"]] or [None]:
                if track is not None:
                    status = "archived" if track["drive_id"] else "downloaded"
                    language, kind = track["language"], track["kind"]
                else:
                    status = video["status"]
                    language, kind = "", ""
                writer.writerow((video["id"], url, video["category"], language, kind, status))
            if by_video[video["id"]] and video["status"] != "downloaded":
                writer.writerow((video["id"], url, video["category"], "", "", video["status"]))
    tmp.replace(path)


def print_json(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)
