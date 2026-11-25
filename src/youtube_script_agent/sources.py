"""Normalize supported URLs and enumerate public channel entries."""

from dataclasses import dataclass
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

from yt_dlp import YoutubeDL


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
CHANNEL_TABS = {"videos", "shorts", "streams", "featured", "about", "community", "posts"}


@dataclass(frozen=True)
class Video:
    id: str
    title: str = ""
    channel: str = ""
    channel_id: str = ""
    category: str = "video"

    @property
    def url(self):
        return f"https://www.youtube.com/watch?v={self.id}"


def normalize_source(source):
    source = source.strip()
    if source.startswith("@"):
        source = "https://www.youtube.com/" + source
    if VIDEO_ID.fullmatch(source):
        return Video(source).url, True

    parts = urlsplit(source)
    host = parts.hostname
    if parts.scheme not in {"https", "http"} or parts.username or parts.password:
        raise ValueError("Use a YouTube video or channel URL, or a channel @handle.")
    path = [unquote(segment) for segment in parts.path.split("/") if segment]
    video_id = None
    if host == "youtu.be" and len(path) == 1:
        video_id = path[0]
    elif host in YOUTUBE_HOSTS and path:
        if path == ["watch"]:
            video_id = parse_qs(parts.query).get("v", [""])[0]
        elif len(path) == 2 and path[0] in {"shorts", "live", "embed"}:
            video_id = path[1]
        else:
            channel_path = path[:-1] if path[-1] in CHANNEL_TABS else path
            if (len(channel_path) == 1 and re.fullmatch(r"@[^/?#\s]+", channel_path[0])) or (
                len(channel_path) == 2
                and channel_path[0] in {"channel", "c", "user"}
                and re.fullmatch(r"[^/?#\s]+", channel_path[1])
            ):
                return "https://www.youtube.com/" + "/".join(quote(p, safe="@") for p in channel_path), False
    if video_id and VIDEO_ID.fullmatch(video_id):
        return Video(video_id).url, True
    raise ValueError("Use a YouTube video or channel URL, or a channel @handle.")


def channel_label(data):
    for key in ("uploader_id", "uploader_url", "channel_url"):
        value = data.get(key) or ""
        handle = value.rstrip("/").rsplit("/", 1)[-1]
        if re.fullmatch(r"@[^/?#\s]+", handle):
            return handle
    return data.get("channel_id") or ""


def inventory(source, logger):
    url, is_video = normalize_source(source)
    if is_video:
        return [Video(parse_qs(urlsplit(url).query)["v"][0])]

    with YoutubeDL({
        "extract_flat": "in_playlist",
        "logger": logger,
        "cachedir": False,
        "retries": 0,
        "extractor_retries": 0,
        "socket_timeout": 30,
    }) as downloader:
        root = downloader.extract_info(url, download=False)
    videos = {}

    def visit(entry, channel, channel_id, category):
        if not entry:
            return
        channel_id = entry.get("channel_id") or channel_id
        channel = channel_label(entry) or channel
        if entry.get("_type") in {"playlist", "multi_video"}:
            path = urlsplit(entry.get("webpage_url") or entry.get("original_url") or "").path
            tab = path.rstrip("/").rsplit("/", 1)[-1]
            category = {"videos": "video", "shorts": "short", "streams": "live"}.get(tab, category)
            for child in entry.get("entries") or []:
                visit(child, channel, channel_id, category)
            return
        video_id = entry.get("id") or ""
        if VIDEO_ID.fullmatch(video_id) and entry.get("live_status") not in {"is_live", "is_upcoming"}:
            video_category = "live" if entry.get("live_status") in {"was_live", "post_live"} else category
            item = Video(video_id, entry.get("title") or "", channel, channel_id, video_category)
            old = videos.get(video_id)
            if old is None or (old.category == "video" and video_category != "video"):
                videos[video_id] = item

    visit(root, channel_label(root), root.get("channel_id") or "", "video")
    if not videos:
        raise ValueError("The channel listing returned no public videos.")
    return list(videos.values())
