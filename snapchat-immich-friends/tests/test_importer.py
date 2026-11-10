"""End-to-end tests against fake Immich / Google Photos servers. Run: python -m pytest tests"""
import datetime as dt
import json
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import destinations as D  # noqa: E402
import snapimmich_core as C  # noqa: E402


# ----------------------------------------------------------------------------- fixtures

def make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(zipfile.ZipInfo('memories/2021-07-04_abc-main.jpg', (2021, 7, 4, 15, 30, 0)), b'\xff\xd8jpeg1')
        z.writestr(zipfile.ZipInfo('memories/2021-07-04_abc-overlay.png', (2021, 7, 4, 15, 30, 0)), b'png')
        z.writestr(zipfile.ZipInfo('memories/2019-01-02_def-main.mp4', (2024, 1, 1, 0, 0, 0)), b'mp4data')
        z.writestr('memories/bad:name?.jpg', b'\xff\xd8jpeg2')
        z.writestr('json/memories_history.json', b'{}')
        z.writestr('index.html', b'<html>')
    return path


class Fake(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype='application/json'):
        body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b'')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return self.rfile.read(int(self.headers.get('Content-Length', 0)))

    def do_GET(self):
        s = self.state
        if self.path == '/api/server/version':
            return self._send(200, {'major': s['major'], 'minor': 0, 'patch': 0})
        if self.path == '/api/albums':
            if self.headers.get('x-api-key') != 'good':
                return self._send(401, {'message': 'bad key'})
            return self._send(200, s['albums'])
        if self.path.startswith('/v1/albums'):
            return self._send(200, {'albums': s['g_albums']})
        if self.path == '/api/users/me':
            return self._send(200, {'quotaSizeInBytes': 5000, 'quotaUsageInBytes': 1000})
        if self.path == '/snap.zip':
            return self._send(200, raw=s['zipbytes'], ctype='application/zip')
        self._send(404, {})

    def do_POST(self):
        s = self.state
        body = self._body()
        if self.path == '/api/albums':
            s['albums'].append({'id': 'alb1', 'albumName': json.loads(body)['albumName']})
            return self._send(201, {'id': 'alb1'})
        if self.path == '/api/assets':
            if s['major'] >= 3 and b'deviceAssetId' in body:
                return self._send(400, {'message': 'property deviceAssetId should not exist'})
            s['uploads'].append(body)
            return self._send(201, {'id': f'asset{len(s["uploads"])}', 'status': 'created'})
        if self.path == '/token':
            from urllib.parse import parse_qs
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            assert form['code'] == 'thecode' and form['code_verifier'] and form['client_id'] == 'cid'
            return self._send(200, {'access_token': 'tok', 'refresh_token': 'r', 'expires_in': 3600})
        if self.path == '/v1/albums':
            s['g_albums'].append({'id': 'galb', 'title': json.loads(body)['album']['title']})
            return self._send(200, {'id': 'galb'})
        if self.path == '/v1/uploads':
            assert self.headers['Authorization'] == 'Bearer tok'
            s['g_uploads'].append(body)
            return self._send(200, raw=f'utok{len(s["g_uploads"])}'.encode(), ctype='text/plain')
        if self.path == '/v1/mediaItems:batchCreate':
            req = json.loads(body)
            assert req['albumId'] == 'galb'
            res = [{'uploadToken': m['simpleMediaItem']['uploadToken'], 'status': {'message': 'Success'},
                    'mediaItem': {'id': 'g-' + m['simpleMediaItem']['uploadToken']}} for m in req['newMediaItems']]
            return self._send(200, {'newMediaItemResults': res})
        self._send(404, {})

    def do_PUT(self):
        ids = json.loads(self._body())['ids']
        self.state['album_adds'].extend(ids)
        self._send(200, [{'id': i, 'success': True} for i in ids])


@pytest.fixture
def server(tmp_path):
    Fake.state = {'major': 2, 'albums': [], 'uploads': [], 'album_adds': [], 'g_albums': [],
                  'g_uploads': [], 'zipbytes': make_zip(tmp_path / 'src.zip').read_bytes()}
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_port}', Fake.state
    srv.shutdown()


# ----------------------------------------------------------------------------- unit

def test_clean_link_markdown_and_expiry():
    exp = int(time.time()) + 3600
    url = f'https://storage.googleapis.com/b/mydata~1.zip?Expires={exp}&GoogleAccessId=x&Signature=a%2Fb'
    got, when = C.clean_link(f'[Download]({url.replace("&", chr(92) + "&")})')
    assert got == url and when.year >= 2026
    with pytest.raises(C.ImportError_, match='expired'):
        C.clean_link(url.replace(str(exp), '1000'))
    with pytest.raises(C.ImportError_):
        C.clean_link('https://evil.example.com/x.zip?Expires=1&GoogleAccessId=x&Signature=y')


def test_safe_relpath_windows_rules():
    assert C.safe_relpath('a/bad:name?.jpg').as_posix() == 'a/bad_name_.jpg'
    assert C.safe_relpath('../../etc/CON.jpg').as_posix() == 'etc/_CON.jpg'
    long = C.safe_relpath('x' * 300 + '.mp4')
    assert len(long.name) <= 120 and long.suffix == '.mp4'


def test_dates_and_overlays():
    assert C.date_from_name('2021-07-04_abc-main.jpg') == dt.datetime(2021, 7, 4, 12, tzinfo=dt.timezone.utc)
    assert C.date_from_name('IMG_0001.jpg') is None
    assert C.is_overlay('2021-07-04_abc-overlay.png') and not C.is_overlay('abc-main.jpg')
    assert C.split_links('a\n\n  b \n') == ['a', 'b']


def test_settings_never_store_key(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'config_dir', lambda: tmp_path)
    C.save_settings({'server': 's', 'api_key': 'secret'})
    assert 'secret' not in (tmp_path / 'settings.json').read_text()
    assert C.load_settings() == {'server': 's'}


# ----------------------------------------------------------------------------- end to end

@pytest.mark.parametrize('major', [2, 3])
def test_immich_zip_import_and_resume(server, tmp_path, major):
    base, state = server
    state['major'] = major
    dest = D.ImmichDestination(base, 'good')
    imp = C.Importer(dest, work_dir=tmp_path / 'work', log=lambda m: None)
    dest.connect()
    assert dest.send_device_fields == (major < 3)
    ok, bad = imp.run(zip_files=[tmp_path / 'src.zip'])
    assert (ok, bad) == (1, 0)
    assert len(state['uploads']) == 3  # overlay skipped, json/html ignored
    assert all((b'deviceAssetId' in u) == (major < 3) for u in state['uploads'])
    assert b'2021-07-04T15:30:00' in state['uploads'][0] or any(b'2021-07-04T' in u for u in state['uploads'])
    assert any(b'2019-01-02T12:00:00.000Z' in u for u in state['uploads'])  # filename date beats ZIP date
    assert sorted(state['album_adds']) == ['asset1', 'asset2', 'asset3']
    # second run: journal skips everything
    C.Importer(dest, work_dir=tmp_path / 'work', log=lambda m: None).run(zip_files=[tmp_path / 'src.zip'])
    assert len(state['uploads']) == 3


def test_immich_v3_fallback_without_version(server, tmp_path):
    base, state = server
    state['major'] = 3
    dest = D.ImmichDestination(base, 'good')
    dest.connect()
    dest.send_device_fields = True  # pretend version detection failed
    C.Importer(dest, work_dir=tmp_path / 'w', log=lambda m: None).run(zip_files=[tmp_path / 'src.zip'])
    assert len(state['uploads']) == 3 and not dest.send_device_fields


def test_immich_bad_key(server):
    base, _ = server
    with pytest.raises(C.ImportError_, match='refused the API key'):
        D.ImmichDestination(base, 'nope').connect()


def test_download_from_link(server, tmp_path):
    base, state = server
    imp = C.Importer(D.FolderDestination(str(tmp_path / 'out')), work_dir=tmp_path / 'w', log=lambda m: None)
    imp.download(base + '/snap.zip', tmp_path / 'dl.zip')
    assert zipfile.is_zipfile(tmp_path / 'dl.zip')


def test_folder_destination(tmp_path):
    make_zip(tmp_path / 'src.zip')
    dest = D.FolderDestination(str(tmp_path / 'cloud'), by_year=True)
    dest.connect()
    ok, bad = C.Importer(dest, work_dir=tmp_path / 'w', log=lambda m: None).run(zip_files=[tmp_path / 'src.zip'])
    assert (ok, bad) == (1, 0)
    out = tmp_path / 'cloud' / 'Snapchat Memories'
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
    assert '2019/2019-01-02_def-main.mp4' in files and '2021/2021-07-04_abc-main.jpg' in files
    assert not any('overlay' in f for f in files)
    mp4 = out / '2019' / '2019-01-02_def-main.mp4'
    assert dt.datetime.fromtimestamp(mp4.stat().st_mtime, dt.timezone.utc).date() == dt.date(2019, 1, 2)
    # re-run: nothing duplicated
    C.Importer(dest, work_dir=tmp_path / 'w2', log=lambda m: None).run(zip_files=[tmp_path / 'src.zip'])
    assert len([p for p in out.rglob('*') if p.is_file()]) == 3


def test_google_photos(server, tmp_path, monkeypatch):
    base, state = server
    secret = tmp_path / 'client.json'
    secret.write_text(json.dumps({'installed': {'client_id': 'cid', 'client_secret': 'cs'}}))
    monkeypatch.setattr(D.GooglePhotosDestination, 'API', base + '/v1')
    dest = D.GooglePhotosDestination(str(secret))
    dest.access_token, dest.expires_at = 'tok', time.time() + 3600  # skip browser sign-in
    dest.connect()
    assert dest.album_id == 'galb'
    ok, bad = C.Importer(dest, work_dir=tmp_path / 'w', log=lambda m: None).run(zip_files=[tmp_path / 'src.zip'])
    assert (ok, bad) == (1, 0) and len(state['g_uploads']) == 3
    journal = json.loads(next((tmp_path / 'w').rglob('uploaded-google.json')).read_text())
    assert all(v.startswith('g-utok') for v in journal.values())


def test_google_requires_client_file(tmp_path):
    with pytest.raises(C.ImportError_, match='Google sign-in is not set up'):
        D.GooglePhotosDestination(str(tmp_path / 'missing.json')).connect()


def test_icloud_windows_style_folder_copy(tmp_path, monkeypatch):
    make_zip(tmp_path / 'src.zip')
    dest = D.AppleICloudDestination(str(tmp_path / 'iCloud Photos' / 'Uploads'))
    dest.mac = False
    dest.batch_size = 1
    (tmp_path / 'iCloud Photos' / 'Uploads').mkdir(parents=True)
    monkeypatch.setattr(D.sys, 'platform', 'win32')
    assert 'iCloud' in dest.connect()
    C.Importer(dest, work_dir=tmp_path / 'w', log=lambda m: None).run(zip_files=[tmp_path / 'src.zip'])
    assert len(list((tmp_path / 'iCloud Photos' / 'Uploads').iterdir())) == 3


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS only')
def test_applescript_compiles():
    import subprocess
    for script in (D._APPLESCRIPT_ALBUM, D._APPLESCRIPT_IMPORT):
        r = subprocess.run(['osacompile', '-o', '/dev/null', '-e', script], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_cancel_stops(tmp_path):
    import threading as th
    make_zip(tmp_path / 'src.zip')
    ev = th.Event()
    ev.set()
    imp = C.Importer(D.FolderDestination(str(tmp_path / 'o')), work_dir=tmp_path / 'w', cancel=ev, log=lambda m: None)
    with pytest.raises(C.ImportCancelled):
        imp.run(zip_files=[tmp_path / 'src.zip'])


def test_write_jpeg_date(tmp_path):
    piexif = pytest.importorskip('piexif')
    pil = pytest.importorskip('PIL.Image')
    p = tmp_path / 'x.jpg'
    pil.new('RGB', (4, 4)).save(p)
    assert C.write_jpeg_date(p, dt.datetime(2020, 5, 6, 7, 8, 9, tzinfo=dt.timezone.utc))
    assert piexif.load(str(p))['Exif'][piexif.ExifIFD.DateTimeOriginal] == b'2020:05:06 07:08:09'
    assert not C.write_jpeg_date(p, dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))  # keeps existing date


def test_google_browser_sign_in_flow(server, tmp_path, monkeypatch):
    import requests
    from urllib.parse import parse_qs, urlsplit
    base, _ = server
    secret = tmp_path / 'client.json'
    secret.write_text(json.dumps({'installed': {'client_id': 'cid', 'client_secret': 'cs'}}))
    monkeypatch.setattr(D.GooglePhotosDestination, 'API', base + '/v1')
    monkeypatch.setattr(D.GooglePhotosDestination, 'TOKEN_URL', base + '/token')
    dest = D.GooglePhotosDestination(str(secret))
    dest.log = lambda m: None

    def fake_browser(url):  # what Google does after the user clicks "Allow"
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert 'photoslibrary.appendonly' in q['scope'] and q['code_challenge_method'] == 'S256'
        threading.Thread(target=lambda: requests.get(
            q['redirect_uri'], params={'code': 'thecode', 'state': q['state']}, timeout=5)).start()
    dest.open_browser = fake_browser
    assert 'Signed in' in dest.connect()
    assert dest.access_token == 'tok' and dest.refresh_token == 'r'


def test_space_check(server, tmp_path, monkeypatch):
    base, state = server
    make_zip(tmp_path / 'src.zip')
    imp = C.Importer(D.FolderDestination(str(tmp_path / 'out')), work_dir=tmp_path / 'w', log=lambda m: None)
    assert imp.remote_zip_size(base + '/snap.zip') == len(state['zipbytes'])
    rep = imp.check_space(zip_files=[tmp_path / 'src.zip'])
    media = len(b'\xff\xd8jpeg1') + len(b'mp4data') + len(b'\xff\xd8jpeg2')  # overlay excluded
    assert rep.media == media
    assert rep.local_needed == 2 * media  # same drive: temp copy + destination copy
    assert rep.local_ok and 'same drive' in rep.dest_where
    # pretend the disk is almost full
    import collections
    Usage = collections.namedtuple('Usage', 'total used free')
    monkeypatch.setattr(C.shutil, 'disk_usage', lambda p: Usage(10, 10, 100))
    rep = imp.check_space(zip_files=[tmp_path / 'src.zip'])
    assert not rep.local_ok and 'not enough' in rep.summary()


def test_immich_quota(server):
    base, state = server
    dest = D.ImmichDestination(base, 'good')
    free, where = dest.free_space()
    assert (free, where) == (4000, 'Your Immich storage quota')


def test_window_full_flow(tmp_path, monkeypatch):
    """Drive the real window: add a ZIP, Start, space check, finish screen. Skips without a display."""
    tk = pytest.importorskip('tkinter')
    monkeypatch.setattr(C, 'config_dir', lambda: tmp_path / 'cfg')
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display')
    import gui
    try:
        app = gui.App(root)
        make_zip(tmp_path / 'mydata~1.zip')
        app.dest.set('folder')
        app.folder.set(str(tmp_path / 'Cloud'))
        app.work_dir.set(str(tmp_path / 'work'))
        app._on_dest_change()
        exp = int(time.time()) + 86400
        added, bad = app.add_links_from_text(
            f'junk https://example.com/x.zip [Download](https://storage.googleapis.com/b/a.zip?Expires={exp}'
            f'&GoogleAccessId=a&Signature=b)')
        assert (added, len(bad)) == (1, 1)
        app.items = [{'kind': 'file', 'value': str(tmp_path / 'mydata~1.zip')}]
        app._refresh_items()
        app._start()
        deadline = time.time() + 30
        while time.time() < deadline and not getattr(app, 'finish_window', None):
            root.update()
            time.sleep(0.05)
        assert getattr(app, 'finish_window', None), app.status.get()
        assert 'All done! 3 photos and videos' in app.status.get()
        assert len([p for p in (tmp_path / 'Cloud').rglob('*') if p.is_file()]) == 3
        assert 'api_key' not in (tmp_path / 'cfg' / 'settings.json').read_text()
    finally:
        root.destroy()


def test_space_decision():
    import types
    tk = pytest.importorskip('tkinter')  # noqa: F841
    import gui
    rep = C.SpaceReport()
    rep.local_free, rep.local_needed = 100, 10 ** 12
    assert gui.App.space_decision(None, rep) == 'stop'
    rep.local_free, rep.local_needed, rep.dest_free, rep.media = 10 ** 13, 10, 5, 10 ** 9
    assert gui.App.space_decision(None, rep) == 'ask'
    rep.dest_free = None
    assert gui.App.space_decision(None, rep) == 'go'
