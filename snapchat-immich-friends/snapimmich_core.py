"""Core Snapchat ZIP -> Immich import logic, shared by the desktop UI and the console mode.

No UI code lives here. Callers pass a ``log`` callback, an optional ``progress`` callback
and an optional ``threading.Event`` used to cancel a running import.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qs, urlsplit

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

APP_NAME = 'Snapchat Memories to Immich'
VERSION = '2.1.0'
DEFAULT_ALBUM = 'Snapchat Memories'
DEFAULT_WORK_DIR = Path.home() / 'Snapchat-Immich-Import'
DEVICE_ID = 'snapchat-friends-importer'

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif', '.webp', '.tif', '.tiff',
              '.mp4', '.mov', '.m4v', '.avi', '.mkv', '.3gp', '.webm'}

# Characters Windows refuses in file names, plus control characters.
_WIN_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_WIN_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                 *(f'LPT{i}' for i in range(1, 10))}
# Snapchat memory names usually start with the capture date: 2023-05-01_<uuid>-main.jpg
_NAME_DATE = re.compile(r'(?<!\d)(\d{4})-(\d{2})-(\d{2})(?:[ _T](\d{2})[-:.]?(\d{2})[-:.]?(\d{2}))?')

LogFn = Callable[[str], None]
ProgressFn = Callable[[str, int, int], None]


class ImportCancelled(Exception):
    """Raised when the user presses Stop."""


class ImportError_(RuntimeError):
    """A user-facing error with a readable message."""


# --------------------------------------------------------------------------- helpers

def resource_dir() -> Path:
    """Folder holding files bundled with the app (PyInstaller sets sys._MEIPASS)."""
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def config_dir() -> Path:
    if sys.platform.startswith('win'):
        base = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return base / 'SnapchatImmich'


def load_settings() -> dict:
    try:
        return json.loads((config_dir() / 'settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    """Persist non-secret settings. The API key is never written to disk."""
    safe = {k: v for k, v in settings.items() if k != 'api_key'}
    try:
        folder = config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'settings.json').write_text(json.dumps(safe, indent=2), encoding='utf-8')
    except OSError:
        pass


def normalize_server(server: str) -> str:
    """Return the Immich API base URL (ending in /api)."""
    server = (server or '').strip().rstrip('/')
    if not server.startswith(('https://', 'http://')):
        raise ImportError_('The Immich server address must start with https:// or http://')
    return server if server.endswith('/api') else server + '/api'


def clean_link(raw: str) -> tuple[str, dt.datetime]:
    """Extract and validate a signed Snapchat ZIP link. Returns (url, expiry_utc)."""
    s = html.unescape(raw.strip().replace('\\&', '&').replace('\\~', '~').replace('\\?', '?'))
    m = re.search(r'\]\(\s*(https?://.*?)\s*\)\s*$', s)  # Markdown [label](url)
    if m:
        s = m.group(1)
    else:
        m = re.search(r'https?://[^\s\]>"\']+', s)
        if not m:
            raise ImportError_('No download link found in what was pasted.')
        s = m.group(0).rstrip(')')
    s = s.strip().strip('\'"').replace('\\&', '&')
    p = urlsplit(s)
    if p.scheme != 'https' or p.hostname != 'storage.googleapis.com' or not p.path.endswith('.zip'):
        raise ImportError_('This does not look like a Snapchat ZIP download link '
                           '(expected https://storage.googleapis.com/....zip).')
    qs = parse_qs(p.query)
    if not {'Expires', 'GoogleAccessId', 'Signature'}.issubset(qs):
        raise ImportError_('The link is incomplete (missing Expires, GoogleAccessId or Signature). '
                           'Copy the whole link again.')
    try:
        expiry = int(qs['Expires'][0])
    except ValueError as ex:
        raise ImportError_('The link has an invalid Expires value.') from ex
    if expiry <= int(time.time()):
        raise ImportError_('This Snapchat link has expired. Copy a fresh Download link from Snapchat.')
    return s, dt.datetime.fromtimestamp(expiry, dt.timezone.utc)


def split_links(text: str) -> list[str]:
    """Split a block of pasted text into individual link entries (one per non-empty line)."""
    return [line.strip() for line in (text or '').splitlines() if line.strip()]


def safe_relpath(name: str) -> PurePosixPath:
    """Turn a ZIP entry name into a relative path that is valid on Windows, macOS and Linux."""
    parts = []
    for part in name.replace('\\', '/').split('/'):
        if part in ('', '.', '..'):
            continue
        part = _WIN_BAD_CHARS.sub('_', part).rstrip(' .') or '_'
        stem = part.split('.')[0].upper()
        if stem in _WIN_RESERVED:
            part = '_' + part
        if len(part) > 120:  # keep well under Windows' 260-char path limit
            p = PurePosixPath(part)
            part = p.stem[:120 - len(p.suffix) - 9] + '_' + hashlib.sha1(part.encode()).hexdigest()[:8] + p.suffix
        parts.append(part)
    if not parts:
        raise ImportError_(f'Unusable file name inside ZIP: {name!r}')
    return PurePosixPath(*parts)


def is_overlay(name: str) -> bool:
    """Snapchat stores captions/stickers as a transparent '<id>-overlay.png' next to '<id>-main.jpg'."""
    return PurePosixPath(name).stem.lower().endswith('-overlay')


def date_from_name(name: str) -> Optional[dt.datetime]:
    m = _NAME_DATE.search(PurePosixPath(name).name)
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    try:
        if hh is not None:
            return dt.datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss), tzinfo=dt.timezone.utc)
        return dt.datetime(int(y), int(mo), int(d), 12, 0, 0, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def capture_time(path: Path) -> dt.datetime:
    """Best guess at when a memory was taken: the filename date if present, else file mtime."""
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    named = date_from_name(path.name)
    if named is None:
        return mtime
    # If the ZIP timestamp is on the same day, it carries a real time-of-day; prefer it.
    if named.hour == 12 and named.minute == 0 and abs((mtime - named).total_seconds()) < 36 * 3600:
        return mtime
    return named


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def human_size(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


def write_jpeg_date(path: Path, when: dt.datetime) -> bool:
    """Embed a capture date into a JPEG that has none, so Google Photos / Apple Photos sort it
    correctly. Needs the optional 'piexif' package; silently does nothing without it."""
    if path.suffix.lower() not in ('.jpg', '.jpeg'):
        return False
    try:
        import piexif  # type: ignore
    except ImportError:
        return False
    try:
        data = piexif.load(str(path))
        exif = data.setdefault('Exif', {})
        if exif.get(piexif.ExifIFD.DateTimeOriginal):
            return False
        stamp = when.astimezone(dt.timezone.utc).strftime('%Y:%m:%d %H:%M:%S').encode()
        exif[piexif.ExifIFD.DateTimeOriginal] = stamp
        exif[piexif.ExifIFD.DateTimeDigitized] = stamp
        data.setdefault('0th', {})[piexif.ImageIFD.DateTime] = stamp
        data.pop('thumbnail', None)
        data['1st'] = {}
        piexif.insert(piexif.dump(data), str(path))
        return True
    except Exception:  # malformed EXIF etc. - never fail an import over metadata
        return False


# --------------------------------------------------------------------------- importer

class Importer:
    """Downloads/extracts Snapchat ZIPs and hands each photo/video to a destination."""

    def __init__(self, destination, *, work_dir: Path = DEFAULT_WORK_DIR, skip_overlays: bool = True,
                 log: LogFn = print, progress: Optional[ProgressFn] = None,
                 cancel: Optional[threading.Event] = None):
        self.dest = destination
        self.work_dir = Path(work_dir).expanduser()
        self.skip_overlays = skip_overlays
        self.log = log
        self.progress = progress or (lambda phase, done, total: None)
        self.cancel = cancel or threading.Event()
        self.session = requests.Session()
        self.session.headers['User-Agent'] = f'snapchat-to-immich/{VERSION}'
        self.sent_total = 0  # files newly sent this run
        self.done_total = 0  # files now in the destination (including earlier runs)
        destination.log = log

    def _check_cancel(self):
        if self.cancel.is_set():
            raise ImportCancelled()

    def download(self, url: str, out: Path) -> None:
        if out.exists() and zipfile.is_zipfile(out):
            self.log('  Already downloaded; reusing the saved ZIP.')
            return
        partial = out.with_name(out.name + '.part')
        for attempt in range(3):
            start = partial.stat().st_size if partial.exists() else 0
            headers = {'Range': f'bytes={start}-'} if start else {}
            try:
                with self.session.get(url, headers=headers, stream=True, timeout=(30, 120)) as r:
                    if r.status_code in (400, 403):
                        raise ImportError_('Snapchat rejected this link (expired or invalid). Copy a fresh one.')
                    if r.status_code != 416:  # 416 = range past end, file already complete
                        r.raise_for_status()
                        if start and r.status_code != 206:
                            start = 0
                        total = int(r.headers.get('Content-Length', '0')) + start
                        got, last = start, 0.0
                        with open(partial, 'ab' if start else 'wb') as f:
                            for chunk in r.iter_content(chunk_size=1024 * 1024):
                                self._check_cancel()
                                if not chunk:
                                    continue
                                f.write(chunk)
                                got += len(chunk)
                                if time.monotonic() - last > 0.25:
                                    self.progress('download', got, total)
                                    last = time.monotonic()
                        self.progress('download', got, total or got)
                if not zipfile.is_zipfile(partial):
                    partial.unlink(missing_ok=True)
                    raise ImportError_('The downloaded file is not a valid ZIP.')
                self.log('  Checking ZIP integrity...')
                with zipfile.ZipFile(partial) as z:
                    bad = z.testzip()
                if bad:
                    partial.unlink(missing_ok=True)
                    raise ImportError_('ZIP integrity check failed at ' + bad)
                partial.replace(out)
                return
            except (requests.RequestException, OSError) as ex:
                if attempt == 2:
                    raise ImportError_(f'Download failed: {ex}') from ex
                self.log('  Download interrupted; retrying...')
                time.sleep(2)

    def media_entries(self, z: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        out, skipped = [], 0
        for e in z.infolist():
            if e.is_dir() or PurePosixPath(e.filename).suffix.lower() not in EXTENSIONS:
                continue
            if self.skip_overlays and is_overlay(e.filename):
                skipped += 1
                continue
            out.append(e)
        if skipped:
            self.log(f'  Skipping {skipped} caption/sticker overlay file(s).')
        return out

    def extract(self, zip_path: Path, folder: Path) -> list[tuple[Path, str]]:
        """Extract supported media. Returns (local_path, relative_name) pairs."""
        folder.mkdir(parents=True, exist_ok=True)
        root = folder.resolve()
        result = []
        with zipfile.ZipFile(zip_path) as z:
            entries = self.media_entries(z)
            need = sum(e.file_size for e in entries)
            free = shutil.disk_usage(folder).free
            if need > free:
                raise ImportError_(f'Not enough disk space: need {human_size(need)}, '
                                   f'only {human_size(free)} free in {folder}.')
            for i, entry in enumerate(entries, 1):
                self._check_cancel()
                rel = safe_relpath(entry.filename)
                target = (folder / Path(*rel.parts)).resolve()
                try:
                    target.relative_to(root)
                except ValueError as ex:
                    raise ImportError_('Unsafe file path inside ZIP; refusing to extract.') from ex
                if not target.exists() or target.stat().st_size != entry.file_size:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(entry) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                    try:
                        stamp = dt.datetime(*entry.date_time).timestamp()
                        os.utime(target, (stamp, stamp))
                    except (ValueError, OverflowError, OSError):
                        pass
                result.append((target, rel.as_posix()))
                self.progress('extract', i, len(entries))
        return result

    def import_link(self, raw: str) -> bool:
        url, expiry = clean_link(raw)
        self.log(f'  Link OK (expires {expiry:%Y-%m-%d %H:%M} UTC).')
        identity = hashlib.sha256(urlsplit(url).path.encode()).hexdigest()[:16]
        folder = self.work_dir / identity
        folder.mkdir(parents=True, exist_ok=True)
        zip_path = folder / 'snapchat.zip'
        self.log('  Downloading ZIP...')
        self.download(url, zip_path)
        return self._process(identity, zip_path, folder)

    def import_zip_file(self, zip_file: Path) -> bool:
        zip_file = Path(zip_file).expanduser()
        if not zip_file.is_file() or not zipfile.is_zipfile(zip_file):
            raise ImportError_(f'Not a ZIP file: {zip_file}')
        st = zip_file.stat()
        identity = 'local-' + hashlib.sha256(f'{zip_file.name}|{st.st_size}'.encode()).hexdigest()[:16]
        folder = self.work_dir / identity
        folder.mkdir(parents=True, exist_ok=True)
        return self._process(identity, zip_file, folder)

    def _process(self, identity: str, zip_path: Path, folder: Path) -> bool:
        data_dir = folder / 'media'
        journal = folder / self.dest.journal_name()
        self.log('  Extracting photos and videos...')
        media = self.extract(zip_path, data_dir)
        self.log(f'  Found {len(media)} photo/video file(s).')
        if not media:
            self.log('  No photos/videos in this ZIP. Some Snapchat exports only contain an index '
                     'page with links; download the "Memories" ZIPs instead.')
            return True
        completed: dict = {}
        if journal.exists():
            try:
                completed = json.loads(journal.read_text(encoding='utf-8'))
            except ValueError:
                self.log('  Warning: resume journal was unreadable; starting it fresh.')

        def save_journal():
            tmp = journal.with_name(journal.name + '.tmp')
            tmp.write_text(json.dumps(completed, indent=2), encoding='utf-8')
            tmp.replace(journal)

        pending = []
        for path, rel_suffix in media:
            rel = f'{identity}/{rel_suffix}'
            if rel not in completed:
                when = capture_time(path)
                if self.dest.wants_embedded_dates:
                    write_jpeg_date(path, when)
                    try:
                        os.utime(path, (when.timestamp(), when.timestamp()))
                    except OSError:
                        pass
                pending.append((path, rel, when))
        if len(pending) < len(media):
            self.log(f'  {len(media) - len(pending)} file(s) were already sent in an earlier run.')
        errors = 0
        batch = max(1, self.dest.batch_size)
        for start in range(0, len(pending), batch):
            self._check_cancel()
            chunk = pending[start:start + batch]
            try:
                results = self.dest.send_batch(chunk)
            except ImportError_ as ex:
                results = {rel: ex for _, rel, _ in chunk}
            for path, rel, _ in chunk:
                res = results.get(rel)
                if isinstance(res, str):
                    completed[rel] = res
                    self.sent_total += 1
                    self.log(f'  Sent {path.name[:70]}')
                else:
                    errors += 1
                    self.log(f'  FAILED {path.name}: {res or "no result"}')
            save_journal()
            self.progress('upload', min(start + batch, len(pending)), len(pending))
        self.dest.finish(list(dict.fromkeys(completed.values())))
        self.done_total += len(completed)
        self.log(f'  Done: {len(completed)} sent/verified, {errors} failed.')
        if errors:
            self.log('  Run this same ZIP/link again to retry the failed files; finished ones are skipped.')
        return errors == 0

    # ---- storage check before starting

    def remote_zip_size(self, url: str) -> Optional[int]:
        """Size of a Snapchat ZIP without downloading it (signed URLs only allow GET, so ask for 1 byte)."""
        try:
            with self.session.get(url, headers={'Range': 'bytes=0-0'}, stream=True, timeout=(15, 30)) as r:
                if r.status_code == 206 and '/' in r.headers.get('Content-Range', ''):
                    return int(r.headers['Content-Range'].rsplit('/', 1)[1])
                if r.ok and r.headers.get('Content-Length'):
                    return int(r.headers['Content-Length'])
        except (requests.RequestException, ValueError):
            pass
        return None

    def check_space(self, links: Iterable[str] = (), zip_files: Iterable[Path] = ()) -> 'SpaceReport':
        """Estimate how much storage the import needs here and at the destination."""
        rep = SpaceReport()
        for raw in links:
            try:
                url, _ = clean_link(raw)
            except ImportError_:
                continue
            identity = hashlib.sha256(urlsplit(url).path.encode()).hexdigest()[:16]
            saved = self.work_dir / identity / 'snapchat.zip'
            if saved.exists() and zipfile.is_zipfile(saved):
                media = self._media_size(saved)
                rep.media += media
                rep.local_needed += max(0, media - self._extracted_size(self.work_dir / identity / 'media'))
                continue
            size = self.remote_zip_size(url)
            if size is None:
                rep.unknown += 1
                continue
            rep.media += size  # photos/videos barely compress, so media ~= ZIP size
            rep.local_needed += 2 * size  # the ZIP itself + the unpacked copy
        for f in zip_files:
            try:
                media = self._media_size(Path(f))
            except (OSError, zipfile.BadZipFile):
                continue
            rep.media += media
            rep.local_needed += media
        probe = self.work_dir
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        rep.local_free = shutil.disk_usage(probe).free
        rep.local_where = str(self.work_dir)
        rep.dest_free, rep.dest_where = self.dest.free_space()
        dest_path = getattr(self.dest, 'space_path', None)
        if rep.dest_free is not None and dest_path:
            try:
                if Path(dest_path).stat().st_dev == probe.stat().st_dev:
                    # Same drive: the temporary copy and the destination copy both need room there.
                    rep.local_needed += rep.media
                    rep.dest_free, rep.dest_where = None, f'{rep.dest_where} is on the same drive (counted above)'
            except OSError:
                pass
        return rep

    def _media_size(self, zip_path: Path) -> int:
        with zipfile.ZipFile(zip_path) as z:
            return sum(e.file_size for e in self.media_entries_quiet(z))

    @staticmethod
    def _extracted_size(folder: Path) -> int:
        return sum(p.stat().st_size for p in folder.rglob('*') if p.is_file()) if folder.exists() else 0

    def media_entries_quiet(self, z: zipfile.ZipFile) -> list:
        log, self.log = self.log, (lambda m: None)
        try:
            return self.media_entries(z)
        finally:
            self.log = log

    def run(self, links: Iterable[str] = (), zip_files: Iterable[Path] = ()) -> tuple[int, int]:
        """Import everything. Returns (succeeded, failed) archive counts."""
        jobs = [('link', x) for x in links] + [('file', Path(x)) for x in zip_files]
        ok = bad = 0
        for n, (kind, item) in enumerate(jobs, 1):
            self._check_cancel()
            label = Path(item).name if kind == 'file' else f'link {n}'
            self.log(f'\n[{n}/{len(jobs)}] {label}')
            self.progress('archive', n - 1, len(jobs))
            try:
                good = self.import_link(item) if kind == 'link' else self.import_zip_file(item)
                ok += good
                bad += not good
            except ImportCancelled:
                raise
            except (ImportError_, zipfile.BadZipFile, OSError) as ex:
                bad += 1
                self.log(f'  ERROR: {ex}')
        self.progress('archive', len(jobs), len(jobs))
        return ok, bad


SAFETY_MARGIN = 1024 ** 3  # keep at least 1 GB free so the computer stays usable


class SpaceReport:
    """Result of Importer.check_space(). Sizes are bytes; None = could not be measured."""

    def __init__(self):
        self.media = 0           # total photo/video size to send
        self.local_needed = 0    # extra space needed on this computer
        self.local_free = 0
        self.local_where = ''
        self.dest_free: Optional[int] = None
        self.dest_where = ''     # human description of where the destination space was measured
        self.unknown = 0         # links whose size could not be checked

    @property
    def local_ok(self) -> bool:
        return self.local_free - self.local_needed >= SAFETY_MARGIN or self.local_needed == 0

    @property
    def dest_ok(self) -> Optional[bool]:
        if self.dest_free is None:
            return None
        return self.dest_free >= self.media

    def summary(self) -> str:
        lines = [f'Your memories are about {human_size(self.media)}.' if self.media else
                 'Could not measure the size of your memories yet.']
        lines.append(f'This computer: needs about {human_size(self.local_needed)}, has '
                     f'{human_size(self.local_free)} free' + (' ✔' if self.local_ok else ' ✖ not enough'))
        if self.dest_free is not None:
            lines.append(f'{self.dest_where}: {human_size(self.dest_free)} free' +
                         (' ✔' if self.dest_ok else ' ✖ not enough'))
        elif self.dest_where:
            lines.append(self.dest_where)
        if self.unknown:
            lines.append(f'({self.unknown} link(s) could not be measured; they may have expired.)')
        return '\n'.join(lines)
