---
name: youtube-subtitle-archive
description: Archive YouTube subtitles from a single video or a channel to Google Drive as individual SRT files. Use for caption downloads, backfills, or resuming a failed subtitle archive.
---

# Archive YouTube subtitles

1. Resolve the user's YouTube video or channel URL. Use the repository's shared state directory across sources and restarts. Preserve the detected channel attribution when archiving one video.
2. Run `youtube-subtitles inventory SOURCE` and `youtube-subtitles run SOURCE` from the repository root. Use `--limit` for a small pilot. The CLI serializes attempts and persists the rolling rate limit. On exit 75, keep the state, report or wait for the recorded reset, and then resume without bypassing the delay. Retry failed tracks selectively with `--retry-failed`.
3. Use `youtube-subtitles queue SOURCE` for the local SRT paths, checksums, batch numbers, and channel labels. Use the connected Google Drive tools to find or create `YouTube 字幕 / @channel / batch-NNN / VIDEO_ID` in My Drive. If there is no handle, use the public channel ID. Reuse the correct existing folders and check filenames before uploading. For an existing file, verify its checksum; if bytes differ, update the existing file where supported or flag the conflict. Record the Drive file ID with `youtube-subtitles ack VIDEO_ID FILENAME SHA256 DRIVE_ID` only after the uploaded bytes pass checksum verification. The `ack-json` command accepts an array with these four fields in snake case.
4. Export one per-batch CSV with `youtube-subtitles export SOURCE INDEX.csv --batch N` and put it in that Drive batch. Use `status` and metadata readback to check completion. Keep SRT files, Drive file IDs, and retry state out of Git. Public exports contain only public video and track metadata.
5. Return the verified Drive folder URL, per-status video counts, the number of verified tracks, and any items still pending. Pass the archive source or a subset of videos to `$video-style-analysis` when requested.

Download actual manual and original-language automatic captions. A video can have both. YouTube may withhold a track or provide no independent captions. Track any such outcome without making up a transcript. Only archive to a destination the user requested; preserve existing sharing settings.
