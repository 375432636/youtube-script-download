"""Store queue, rolling request starts, and archive receipts in one workspace."""

from contextlib import contextmanager
from dataclasses import asdict
import fcntl
from pathlib import Path
import sqlite3
import time


HOUR = 3600
MAX_ATTEMPTS = 200
MIN_INTERVAL = 18
MAX_RETRY_AFTER = 86400


def next_start(now, starts, blocked_until=0):
    recent = sorted(start for start in starts if start > now - HOUR)
    last_start = max(starts, default=now - MIN_INTERVAL)
    next_slot = recent[-MAX_ATTEMPTS] + HOUR if len(recent) >= MAX_ATTEMPTS else now
    return max(now, last_start + MIN_INTERVAL, next_slot, blocked_until)


class ArchiveState:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "archive.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, channel TEXT NOT NULL,
                channel_id TEXT NOT NULL, category TEXT NOT NULL, batch INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
                on_disk_attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sources (
                source TEXT NOT NULL, video_id TEXT NOT NULL, position INTEGER NOT NULL,
                PRIMARY KEY (source, video_id)
            );
            CREATE TABLE IF NOT EXISTS attempts (started REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS attempt_time ON attempts (started);
            CREATE TABLE IF NOT EXISTS limits (name TEXT PRIMARY KEY, until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tracks (
                video_id TEXT NOT NULL, filename TEXT NOT NULL, language TEXT NOT NULL,
                kind TEXT NOT NULL, sha256 TEXT NOT NULL, md5 TEXT NOT NULL,
                nbytes INTEGER NOT NULL, drive_id TEXT,
                PRIMARY KEY (video_id, filename)
            );
        """)

    @contextmanager
    def worker(self):
        with (self.directory / "download.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("A download worker already holds this workspace lock.") from exc
            yield

    def add_source(self, source, videos):
        with self.db:
            for position, video in enumerate(videos):
                self.db.execute("""
                    INSERT INTO videos (id, title, channel, channel_id, category, batch)
                    VALUES (:id, :title, :channel, :channel_id, :category, :batch)
                    ON CONFLICT(id) DO UPDATE SET
                        title = COALESCE(NULLIF(videos.title, ''), excluded.title),
                        channel = COALESCE(NULLIF(videos.channel, ''), excluded.channel),
                        channel_id = COALESCE(NULLIF(videos.channel_id, ''), excluded.channel_id),
                        category = CASE WHEN videos.category = 'video'
                            THEN excluded.category ELSE videos.category END
                """, {**asdict(video), "batch": position // MAX_ATTEMPTS + 1})
                self.db.execute("""
                    INSERT INTO sources (source, video_id, position) VALUES (?, ?, ?)
                    ON CONFLICT(source, video_id) DO UPDATE SET position = excluded.position
                """, (source, video.id, position))

    def videos(self, source=None):
        if source is None:
            return self.db.execute("SELECT * FROM videos ORDER BY rowid").fetchall()
        return self.db.execute("""
            SELECT videos.* FROM videos JOIN sources ON videos.id = sources.video_id
            WHERE sources.source = ? ORDER BY sources.position, videos.rowid
        """, (source,)).fetchall()

    def reserve(self, clock=time.time, sleep=time.sleep):
        while True:
            now = clock()
            with self.db:
                self.db.execute("DELETE FROM attempts WHERE started <= ?", (now - HOUR,))
                starts = [row[0] for row in self.db.execute("SELECT started FROM attempts")]
                blocked = self.db.execute("SELECT until FROM limits WHERE name = 'youtube'").fetchone()
                start_at = next_start(now, starts, blocked[0] if blocked else 0)
                if start_at <= now:
                    self.db.execute("INSERT INTO attempts (started) VALUES (?)", (now,))
                    return
            sleep(min(30, start_at - now))

    def finish(self, video, status, detail, tracks, metadata=None, retry_after=0):
        metadata = metadata or {}
        paused_until = None
        with self.db:
            if status == "rate_limited":
                deadline = time.time() + min(MAX_RETRY_AFTER, max(60, retry_after))
                paused_until = self.db.execute("""
                    INSERT INTO limits VALUES ('youtube', ?)
                    ON CONFLICT(name) DO UPDATE SET until = MAX(limits.until, excluded.until)
                    RETURNING until
                """, (deadline,)).fetchone()[0]
            self.db.execute("""
                UPDATE videos SET status = ?, detail = ?, on_disk_attempts = on_disk_attempts + 1,
                    title = COALESCE(NULLIF(?, ''), title),
                    channel = COALESCE(NULLIF(?, ''), channel),
                    channel_id = COALESCE(NULLIF(?, ''), channel_id)
                WHERE id = ?
            """, (status, detail, metadata.get("title", ""), metadata.get("channel", ""),
                  metadata.get("channel_id", ""), video["id"]))
            for track in tracks:
                self.db.execute("""
                    INSERT INTO tracks
                        (video_id, filename, language, kind, sha256, md5, nbytes)
                    VALUES (:video_id, :filename, :language, :kind, :sha256, :md5, :nbytes)
                    ON CONFLICT(video_id, filename) DO UPDATE SET
                        drive_id = CASE WHEN tracks.sha256 = excluded.sha256 THEN tracks.drive_id END,
                        sha256 = excluded.sha256, md5 = excluded.md5, nbytes = excluded.nbytes
                """, track)
        return paused_until

    def tracks(self, source=None, pending_only=False, limit=None):
        source_filter = "AND EXISTS (SELECT 1 FROM sources WHERE source = ? AND video_id = v.id)" if source else ""
        pending_filter = "AND t.drive_id IS NULL" if pending_only else ""
        sql = f"""
            SELECT t.*, v.title, v.channel, v.channel_id, v.batch, v.category
            FROM tracks t JOIN videos v ON v.id = t.video_id
            WHERE 1 {source_filter} {pending_filter}
            ORDER BY v.batch, v.rowid, t.filename
        """
        params = [source] if source else []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return self.db.execute(sql, params).fetchall()

    def record_upload(self, video_id, filename, sha256, drive_id):
        with self.db:
            changed = self.db.execute("""
                UPDATE tracks SET drive_id = ?
                WHERE video_id = ? AND filename = ? AND sha256 = ?
            """, (drive_id, video_id, filename, sha256)).rowcount
            if changed != 1:
                raise ValueError(f"Missing or changed local track: {video_id}/{filename}")
