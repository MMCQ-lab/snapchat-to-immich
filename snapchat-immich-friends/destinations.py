"""Where imported Snapchat memories go: Immich, Google Photos, Apple iCloud Photos, or any folder."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.server
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

from snapimmich_core import (DEFAULT_ALBUM, DEVICE_ID, VERSION, ImportCancelled, ImportError_,
                             iso, normalize_server)

Item = tuple  # (path: Path, rel: str, captured: datetime)


class Destination:
    key = 'base'
    label = 'Base'
    batch_size = 1
    wants_embedded_dates = True  # write dates into JPEG EXIF + file times before sending

    def __init__(self, album_name: str = DEFAULT_ALBUM, cancel: Optional[threading.Event] = None):
        self.album_name = (album_name or DEFAULT_ALBUM).strip() or DEFAULT_ALBUM
        self.cancel = cancel or threading.Event()
        self.log = print

    def journal_name(self) -> str:
        return f'uploaded-{self.key}.json'

    def connect(self) -> str:
        raise NotImplementedError

    def send_batch(self, items: list) -> dict:
        """Send items; return {rel: remote_id_str or Exception}."""
        raise NotImplementedError

    def finish(self, ids: list) -> None:
        pass


# ============================================================================ Immich

class ImmichDestination(Destination):
    key = 'immich'
    label = 'Immich (self-hosted)'
    wants_embedded_dates = False  # Immich gets the date through fileCreatedAt

    def __init__(self, server: str, api_key: str, verify_tls: bool = True, **kw):
        super().__init__(**kw)
        self.base = normalize_server(server)
        self.key_ = (api_key or '').strip()
        if not self.key_:
            raise ImportError_('Enter your Immich API key.')
        self.session = requests.Session()
        self.session.verify = verify_tls
        self.session.headers['User-Agent'] = f'snapchat-to-immich/{VERSION}'
        self.album_id: Optional[str] = None
        self.server_version = ''
        self.send_device_fields = True

    def journal_name(self) -> str:
        return 'uploaded.json'  # same name as v1 so earlier runs resume

    def api(self, method: str, path: str, **kwargs):
        headers = {'x-api-key': self.key_, 'Accept': 'application/json'}
        headers.update(kwargs.pop('headers', {}))
        try:
            resp = self.session.request(method, self.base + path, headers=headers, timeout=(20, 180), **kwargs)
        except requests.exceptions.SSLError as ex:
            raise ImportError_(f'Secure connection to Immich failed (certificate problem): {ex}') from ex
        except requests.ConnectionError as ex:
            raise ImportError_(f'Cannot reach Immich at {self.base}. Check the address and that this '
                               f'computer can reach it (VPN/Tailscale?).') from ex
        except requests.Timeout as ex:
            raise ImportError_('Immich did not respond in time.') from ex
        if resp.status_code in (401, 403):
            raise ImportError_(f'Immich refused the API key (HTTP {resp.status_code}). Create a key with '
                               f'asset upload and album read/write permissions.')
        if not resp.ok:
            raise ImportError_(f'Immich HTTP {resp.status_code} on {path}: {resp.text[:300]}')
        try:
            return resp.json() if resp.content else None
        except ValueError as ex:
            raise ImportError_(f'{self.base} did not answer like an Immich server. Check the address.') from ex

    def connect(self) -> str:
        if self.base.startswith('http://'):
            self.log('WARNING: http:// does not encrypt your API key. Use https:// outside a trusted network.')
        try:
            info = self.api('GET', '/server/version')
            self.server_version = f"{info.get('major')}.{info.get('minor')}.{info.get('patch')}"
            # Immich v3 removed deviceId/deviceAssetId from uploads.
            self.send_device_fields = int(info.get('major', 1)) < 3
        except (ImportError_, TypeError, ValueError, AttributeError):
            self.server_version = 'unknown'
        for a in self.api('GET', '/albums') or []:
            if a.get('albumName') == self.album_name:
                self.album_id = a['id']
                break
        else:
            self.album_id = self.api('POST', '/albums', json={'albumName': self.album_name})['id']
            self.log(f'Created album "{self.album_name}".')
        return f'Connected to Immich {self.server_version}. Album: "{self.album_name}"'

    def _upload(self, path: Path, rel: str, captured: dt.datetime) -> str:
        stamp = iso(captured)
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        for attempt in range(2):
            fields = {'fileCreatedAt': stamp, 'fileModifiedAt': stamp, 'isFavorite': 'false'}
            if self.send_device_fields:
                fields['deviceAssetId'] = hashlib.sha256(rel.encode()).hexdigest()
                fields['deviceId'] = DEVICE_ID
            with open(path, 'rb') as handle:
                fields['assetData'] = (path.name, handle, mime)
                mp = MultipartEncoder(fields=fields)
                try:
                    resp = self.session.post(self.base + '/assets', data=mp, timeout=(25, 900), headers={
                        'x-api-key': self.key_, 'Accept': 'application/json', 'Content-Type': mp.content_type})
                except requests.RequestException as ex:
                    raise ImportError_(f'Upload failed: {ex}') from ex
            if (resp.status_code == 400 and self.send_device_fields and attempt == 0
                    and 'device' in resp.text.lower()):
                self.send_device_fields = False  # server rejects the legacy fields; retry without
                continue
            if resp.status_code not in (200, 201):
                raise ImportError_(f'Upload HTTP {resp.status_code}: {resp.text[:300]}')
            return resp.json()['id']
        raise ImportError_('Upload failed')

    def send_batch(self, items):
        out = {}
        for path, rel, captured in items:
            try:
                out[rel] = self._upload(path, rel, captured)
            except ImportError_ as ex:
                out[rel] = ex
        return out

    def finish(self, ids):
        self.log(f'  Adding to album "{self.album_name}"...')
        for start in range(0, len(ids), 250):
            self.api('PUT', f'/albums/{self.album_id}/assets', json={'ids': ids[start:start + 250]})


# ============================================================================ Google Photos

GOOGLE_SCOPES = ('https://www.googleapis.com/auth/photoslibrary.appendonly '
                 'https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata')


class GooglePhotosDestination(Destination):
    """Google Photos Library API. Uploading and creating albums is still allowed after Google's
    March 2025 API changes (photoslibrary.appendonly scope). Google requires each user to sign in
    with an OAuth 'Desktop app' client from their own Google Cloud project (see README)."""
    key = 'google'
    label = 'Google Photos'
    batch_size = 50  # mediaItems:batchCreate limit

    AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
    TOKEN_URL = 'https://oauth2.googleapis.com/token'
    API = 'https://photoslibrary.googleapis.com/v1'

    def __init__(self, client_secret_file: str, **kw):
        super().__init__(**kw)
        self.client_secret_file = Path(client_secret_file or '').expanduser()
        self.client_id = self.client_secret = ''
        self.access_token = ''
        self.refresh_token = ''
        self.expires_at = 0.0
        self.album_id = ''
        self.session = requests.Session()
        self.open_browser = webbrowser.open

    def journal_name(self) -> str:
        return 'uploaded-google.json'

    # ---- OAuth (installed-app loopback flow with PKCE)

    def _load_client(self):
        if not self.client_secret_file.is_file():
            raise ImportError_('Choose the Google OAuth client file (client_secret_....json). '
                               'See the README for the 5-minute setup.')
        try:
            data = json.loads(self.client_secret_file.read_text(encoding='utf-8'))
            info = data.get('installed') or data.get('web') or data
            self.client_id, self.client_secret = info['client_id'], info.get('client_secret', '')
        except (ValueError, KeyError, AttributeError) as ex:
            raise ImportError_('That file is not a Google OAuth client JSON (expected "installed" -> '
                               '"client_id"). Create a "Desktop app" OAuth client.') from ex

    def _sign_in(self):
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        state = secrets.token_urlsafe(16)
        result: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                qs = parse_qs(urlsplit(self.path).query)
                if 'code' in qs or 'error' in qs:
                    result.update({k: v[0] for k, v in qs.items()})
                msg = ('Signed in. You can close this tab and return to the importer.'
                       if 'code' in qs else 'Sign-in did not complete. Return to the importer.')
                body = f'<html><body style="font-family:sans-serif;padding:2em"><h2>{msg}</h2></body></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        server.timeout = 0.5
        redirect = f'http://127.0.0.1:{server.server_port}'
        url = self.AUTH_URL + '?' + urlencode({
            'client_id': self.client_id, 'redirect_uri': redirect, 'response_type': 'code',
            'scope': GOOGLE_SCOPES, 'code_challenge': challenge, 'code_challenge_method': 'S256',
            'state': state, 'access_type': 'offline', 'prompt': 'consent'})
        self.log('Opening your browser to sign in to Google...')
        self.log(f'If it does not open, visit:\n{url}')
        self.open_browser(url)
        deadline = time.monotonic() + 300
        try:
            while not result and time.monotonic() < deadline:
                if self.cancel.is_set():
                    raise ImportCancelled()
                server.handle_request()
        finally:
            server.server_close()
        if not result:
            raise ImportError_('Google sign-in timed out after 5 minutes.')
        if 'error' in result:
            raise ImportError_(f"Google sign-in failed: {result['error']}")
        if result.get('state') != state:
            raise ImportError_('Google sign-in failed (state mismatch). Try again.')
        self._token({'grant_type': 'authorization_code', 'code': result['code'],
                     'redirect_uri': redirect, 'code_verifier': verifier})

    def _token(self, form: dict):
        form = {'client_id': self.client_id, 'client_secret': self.client_secret, **form}
        resp = self.session.post(self.TOKEN_URL, data=form, timeout=30)
        if not resp.ok:
            raise ImportError_(f'Google token error {resp.status_code}: {resp.text[:300]}')
        tok = resp.json()
        self.access_token = tok['access_token']
        self.refresh_token = tok.get('refresh_token', self.refresh_token)
        self.expires_at = time.time() + int(tok.get('expires_in', 3600)) - 60

    def _auth(self) -> dict:
        if time.time() >= self.expires_at and self.refresh_token:
            self._token({'grant_type': 'refresh_token', 'refresh_token': self.refresh_token})
        return {'Authorization': f'Bearer {self.access_token}'}

    def _req(self, method, url, **kw):
        extra = kw.pop('headers', {})
        for attempt in range(5):
            headers = {**self._auth(), **extra}
            try:
                resp = self.session.request(method, url, headers=headers, timeout=(20, 900), **kw)
            except requests.RequestException as ex:
                raise ImportError_(f'Google Photos request failed: {ex}') from ex
            if resp.status_code in (429, 500, 502, 503) and attempt < 4:
                time.sleep(2 ** attempt * 2)
                if 'data' in kw and hasattr(kw['data'], 'seek'):
                    kw['data'].seek(0)
                continue
            if resp.status_code in (401, 403):
                raise ImportError_(f'Google Photos refused access (HTTP {resp.status_code}): {resp.text[:300]}')
            if not resp.ok:
                raise ImportError_(f'Google Photos HTTP {resp.status_code}: {resp.text[:300]}')
            return resp
        raise ImportError_('Google Photos is rate-limiting; try again later.')

    def connect(self) -> str:
        self._load_client()
        if not self.access_token:
            self._sign_in()
        token = None
        while True:  # only albums this app created are visible
            params = {'pageSize': 50, **({'pageToken': token} if token else {})}
            data = self._req('GET', f'{self.API}/albums', params=params).json()
            for a in data.get('albums', []):
                if a.get('title') == self.album_name:
                    self.album_id = a['id']
            token = data.get('nextPageToken')
            if self.album_id or not token:
                break
        if not self.album_id:
            self.album_id = self._req('POST', f'{self.API}/albums',
                                      json={'album': {'title': self.album_name}}).json()['id']
            self.log(f'Created Google Photos album "{self.album_name}".')
        return f'Signed in to Google Photos. Album: "{self.album_name}"'

    def send_batch(self, items):
        out, tokens = {}, []
        for path, rel, _ in items:
            if self.cancel.is_set():
                break
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            try:
                with open(path, 'rb') as fh:
                    tok = self._req('POST', f'{self.API}/uploads', data=fh, headers={
                        'Content-Type': 'application/octet-stream', 'X-Goog-Upload-Content-Type': mime,
                        'X-Goog-Upload-Protocol': 'raw'}).text
                tokens.append((rel, path.name, tok))
            except (ImportError_, OSError) as ex:
                out[rel] = ex if isinstance(ex, ImportError_) else ImportError_(str(ex))
        if tokens:
            body = {'albumId': self.album_id, 'newMediaItems': [
                {'simpleMediaItem': {'uploadToken': t, 'fileName': name}} for _, name, t in tokens]}
            results = self._req('POST', f'{self.API}/mediaItems:batchCreate', json=body).json()
            by_token = {r.get('uploadToken'): r for r in results.get('newMediaItemResults', [])}
            for rel, _, t in tokens:
                r = by_token.get(t, {})
                status = r.get('status', {})
                if r.get('mediaItem', {}).get('id') and not status.get('code'):
                    out[rel] = r['mediaItem']['id']
                else:
                    out[rel] = ImportError_(status.get('message') or 'Google did not accept the file')
        return out


# ============================================================================ Apple iCloud Photos

def icloud_windows_folder_candidates() -> list[Path]:
    home = Path.home()
    pics = [home / 'Pictures', home / 'OneDrive' / 'Pictures']
    out = []
    for p in pics:
        out += [p / 'iCloud Photos' / 'Uploads', p / 'iCloud Photos' / 'Photos', p / 'iCloud Photos']
    out.append(home / 'iCloud Photos' / 'Uploads')
    return out


def detect_icloud_windows_folder() -> Optional[Path]:
    return next((p for p in icloud_windows_folder_candidates() if p.is_dir()), None)


_APPLESCRIPT_ALBUM = '''on run argv
  set albumName to item 1 of argv
  tell application "Photos"
    if not (exists album albumName) then make new album named albumName
  end tell
  return "ok"
end run'''

_APPLESCRIPT_IMPORT = '''on run argv
  set albumName to item 1 of argv
  set theFiles to {}
  repeat with i from 2 to count of argv
    set end of theFiles to (POSIX file (item i of argv)) as alias
  end repeat
  tell application "Photos"
    set imported to import theFiles into album albumName skip check duplicates true
    set out to ""
    repeat with m in imported
      set out to out & (id of m) & linefeed
    end repeat
    return out
  end tell
end run'''


class AppleICloudDestination(Destination):
    """No public iCloud Photos upload API exists. On macOS we import into the Photos app (which
    uploads to iCloud when iCloud Photos is on). On Windows we copy into the iCloud for Windows
    upload folder, which the iCloud app uploads in the background."""
    key = 'icloud'
    label = 'Apple iCloud Photos'

    def __init__(self, windows_folder: str = '', **kw):
        super().__init__(**kw)
        self.mac = sys.platform == 'darwin'
        self.batch_size = 20 if self.mac else 1
        self.folder = Path(windows_folder).expanduser() if windows_folder else None

    def _osascript(self, script: str, *args: str, timeout: int = 1800) -> str:
        try:
            proc = subprocess.run(['osascript', '-', *args], input=script, capture_output=True,
                                  text=True, timeout=timeout)
        except FileNotFoundError as ex:
            raise ImportError_('osascript not found; Apple Photos import only works on macOS.') from ex
        except subprocess.TimeoutExpired as ex:
            raise ImportError_('Photos took too long to respond.') from ex
        if proc.returncode != 0:
            err = proc.stderr.strip()
            if '-1743' in err or 'Not authorized' in err:
                err = ('macOS blocked access to Photos. Open System Settings > Privacy & Security > '
                       'Automation and allow this app (Terminal/Python) to control Photos.')
            raise ImportError_(f'Photos import failed: {err[:400]}')
        return proc.stdout

    def connect(self) -> str:
        if self.mac:
            self.log('Asking the Photos app to get ready (macOS may ask you to allow access)...')
            self._osascript(_APPLESCRIPT_ALBUM, self.album_name, timeout=300)
            return (f'Photos app ready. Album: "{self.album_name}". Photos uploads to iCloud if '
                    f'iCloud Photos is turned on in Photos > Settings.')
        if sys.platform.startswith('win'):
            self.folder = self.folder or detect_icloud_windows_folder()
            if not self.folder or not self.folder.is_dir():
                raise ImportError_('iCloud Photos folder not found. Install iCloud for Windows, turn on '
                                   'Photos, then choose its upload folder (e.g. Pictures\\iCloud Photos\\Uploads).')
            return f'Copying into iCloud for Windows folder: {self.folder}'
        raise ImportError_('Apple iCloud Photos is only supported on macOS and Windows. '
                           'Use "Any other cloud / folder" here instead.')

    def journal_name(self) -> str:
        if self.mac:
            return 'uploaded-icloud-mac.json'
        tag = hashlib.sha256(str(self.folder).encode()).hexdigest()[:8]
        return f'uploaded-icloud-{tag}.json'

    def send_batch(self, items):
        if not self.mac:
            return {rel: copy_unique(path, self.folder) for path, rel, _ in items}
        # Snapchat names files like 2023-01-01_<uuid>-main.jpg; Photos needs readable paths only.
        try:
            out = self._osascript(_APPLESCRIPT_IMPORT, self.album_name, *[str(p) for p, _, _ in items])
        except ImportError_ as ex:
            return {rel: ex for _, rel, _ in items}
        ids = [x for x in out.splitlines() if x.strip()]
        if len(ids) == len(items):
            return {rel: i for (_, rel, _), i in zip(items, ids)}
        return {rel: f'photos-app:{rel}' for _, rel, _ in items}


# ============================================================================ any folder

def copy_unique(src: Path, folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / src.name
    n = 1
    while target.exists():
        if target.stat().st_size == src.stat().st_size and _same(src, target):
            return str(target)
        target = folder / f'{src.stem}_{n}{src.suffix}'
        n += 1
    tmp = target.with_name(target.name + '.partial')
    shutil.copy2(src, tmp)  # copy2 keeps the (corrected) file dates
    tmp.replace(target)
    return str(target)


def _same(a: Path, b: Path) -> bool:
    def digest(p):
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.digest()
    return digest(a) == digest(b)


class FolderDestination(Destination):
    """Copies memories into any folder, sorted by year, with dates fixed. Point it at a folder
    synced by OneDrive, Dropbox, Google Drive for desktop, Nextcloud, Synology, a NAS share, etc."""
    key = 'folder'
    label = 'Any other cloud / folder'

    def __init__(self, folder: str, by_year: bool = True, **kw):
        super().__init__(**kw)
        if not folder:
            raise ImportError_('Choose a destination folder.')
        self.root = Path(folder).expanduser()
        self.by_year = by_year

    def journal_name(self) -> str:
        return f'uploaded-folder-{hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:8]}.json'

    def connect(self) -> str:
        base = self.root / self.album_name
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / '.write-test'
            probe.write_text('ok')
            probe.unlink()
        except OSError as ex:
            raise ImportError_(f'Cannot write to {base}: {ex}') from ex
        free = shutil.disk_usage(base).free
        return f'Saving into {base} ({free / 1024 ** 3:.1f} GB free)'

    def send_batch(self, items):
        out = {}
        for path, rel, captured in items:
            sub = self.root / self.album_name
            if self.by_year:
                sub = sub / f'{captured:%Y}'
            try:
                out[rel] = copy_unique(path, sub)
            except OSError as ex:
                out[rel] = ImportError_(str(ex))
        return out


DESTINATIONS = {
    'immich': ImmichDestination,
    'google': GooglePhotosDestination,
    'icloud': AppleICloudDestination,
    'folder': FolderDestination,
}
