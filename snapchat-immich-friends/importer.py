#!/usr/bin/env python3
"""Snapchat ZIP to Immich importer. Requires requests and requests-toolbelt."""
import datetime as dt
import getpass
import hashlib
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, parse_qs
import zipfile

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

EXTENSIONS={'.jpg','.jpeg','.png','.gif','.heic','.heif','.webp','.tif','.tiff','.mp4','.mov','.m4v','.avi','.mkv','.3gp','.webm'}
ROOT=Path.home()/'Snapchat-Immich-Import'
ROOT.mkdir(parents=True,exist_ok=True)
SESSION=requests.Session()


def clean_link(s):
    s=html.unescape(s.strip().replace('\\&','&').replace('\\~','~').replace('\\?','?'))
    # Markdown link [label](URL), including URLs that end with encoded signature chars.
    m=re.search(r'\]\(\s*(https?://.*?)\s*\)\s*$',s)
    if m:
        s=m.group(1)
    else:
        m=re.search(r'https?://[^\s\]>]+',s)
        if not m:
            raise ValueError('No HTTPS download link found.')
        s=m.group(0).rstrip(')')
    s=s.strip().strip('\'"').replace('\\&','&')
    p=urlsplit(s)
    if p.scheme!='https' or p.hostname!='storage.googleapis.com' or not p.path.endswith('.zip'):
        raise ValueError('This does not look like a Snapchat ZIP URL from Google Cloud Storage.')
    qs=parse_qs(p.query)
    if not {'Expires','GoogleAccessId','Signature'}.issubset(qs):
        raise ValueError('The signed URL is missing Expires, GoogleAccessId or Signature.')
    expiry=int(qs['Expires'][0]); now=int(time.time())
    if expiry<=now:
        raise ValueError('Snapchat signed link has expired. Copy a new Download link from Snapchat.')
    print('  Snapchat URL verified; expires:',dt.datetime.fromtimestamp(expiry,dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    return s


def api(method,path,key,base,**kwargs):
    headers={'x-api-key':key,'Accept':'application/json'}
    headers.update(kwargs.pop('headers',{}))
    resp=SESSION.request(method,base+path,headers=headers,timeout=(20,180),**kwargs)
    if not resp.ok:
        raise RuntimeError(f'Immich HTTP {resp.status_code}: {resp.text[:350]}')
    return resp.json() if resp.content else None


def album_id(key,base):
    albums=api('GET','/albums',key,base)
    for a in albums:
        if a.get('albumName')=='Snapchat Memories':
            return a['id']
    return api('POST','/albums',key,base,json={'albumName':'Snapchat Memories'})['id']


def download(url,out):
    if out.exists() and zipfile.is_zipfile(out):
        print('  Existing valid ZIP found; reusing.')
        return
    partial=out.with_suffix('.zip.part')
    # Requests range support; if ignored start fresh.
    for attempt in range(3):
        start=partial.stat().st_size if partial.exists() else 0
        headers={'Range':f'bytes={start}-'} if start else {}
        try:
            with SESSION.get(url,headers=headers,stream=True,timeout=(30,120)) as r:
                if r.status_code in (400,403):
                    raise RuntimeError('Snapchat rejected this signed link (expired or invalid). Copy a fresh one.')
                r.raise_for_status()
                if start and r.status_code!=206:
                    start=0
                mode='ab' if start else 'wb'
                total=int(r.headers.get('Content-Length','0'))+start
                got=start; last=0
                with open(partial,mode) as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if not chunk: continue
                        f.write(chunk); got+=len(chunk)
                        if got-last>=50*1024*1024:
                            print(f'  Downloaded {got/1024**2:.0f} / {total/1024**2:.0f} MiB' if total else f'  Downloaded {got/1024**2:.0f} MiB')
                            last=got
            if not zipfile.is_zipfile(partial):
                raise RuntimeError('Downloaded data is not a ZIP file.')
            with zipfile.ZipFile(partial) as z:
                bad=z.testzip()
                if bad: raise RuntimeError('ZIP integrity check failed at '+bad)
            partial.replace(out)
            return
        except (requests.RequestException,OSError) as e:
            if attempt==2: raise RuntimeError(f'Download failed: {e}') from e
            print('  Download interrupted; retrying...')
            time.sleep(2)
    raise RuntimeError('Download failed')


def extract(zippath,folder):
    folder.mkdir(parents=True,exist_ok=True)
    media=[]
    root=folder.resolve()
    with zipfile.ZipFile(zippath) as z:
        for entry in z.infolist():
            if entry.is_dir() or Path(entry.filename).suffix.lower() not in EXTENSIONS: continue
            target=(folder/entry.filename).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError('Unsafe file path inside ZIP; refusing to extract.')
            if not target.exists() or target.stat().st_size!=entry.file_size:
                target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(entry) as src, open(target,'wb') as dst:
                    while True:
                        chunk=src.read(1024*1024)
                        if not chunk:break
                        dst.write(chunk)
                try:
                    stamp=dt.datetime(*entry.date_time).timestamp()
                    os.utime(target,(stamp,stamp))
                except (ValueError,OverflowError,OSError): pass
            media.append(target)
    return media


def upload_asset(path,base,key,rel):
    stat=path.stat()
    stamp=dt.datetime.fromtimestamp(stat.st_mtime,dt.timezone.utc).isoformat(timespec='milliseconds')
    fields={'deviceAssetId':hashlib.sha256(rel.encode()).hexdigest(),
            'deviceId':'snapchat-friends-importer','fileCreatedAt':stamp,
            'fileModifiedAt':stamp,'isFavorite':'false'}
    mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    with open(path,'rb') as handle:
        fields['assetData']=(path.name,handle,mime)
        multipart=MultipartEncoder(fields=fields)
        resp=SESSION.post(base+'/assets',data=multipart,headers={'x-api-key':key,'Accept':'application/json','Content-Type':multipart.content_type},timeout=(25,900))
        if resp.status_code not in (200,201):
            raise RuntimeError(f'Upload HTTP {resp.status_code}: {resp.text[:300]}')
        body=resp.json()
        return body['id']


def add_album(ids,album,base,key):
    if not ids:return
    for start in range(0,len(ids),250):
        api('PUT',f'/albums/{album}/assets',key,base,json={'ids':ids[start:start+250]})


def main():
    print('\nSNAPCHAT MEMORIES -> IMMICH (Windows / Mac)')
    print('Downloads and processes each ZIP locally. Nothing is deleted automatically.')
    print('Use your own Immich server, accessible from this computer.')
    server=input('\nImmich server URL (e.g. https://photos.example.com): ').strip().rstrip('/')
    if not server.startswith(('https://','http://')):
        raise ValueError('Server must start with https:// or http://')
    if server.startswith('http://'):
        print('WARNING: HTTP does not encrypt your API key. Use HTTPS outside a private trusted network.')
        if input('Continue with HTTP? (type YES): ').strip()!='YES':return
    base=server if server.endswith('/api') else server+'/api'
    key=getpass.getpass('Immich API key (hidden): ').strip()
    print('Checking Immich API and album permissions...')
    album=album_id(key,base)
    print('Ready. Album: Snapchat Memories')
    print('\nPaste one signed Snapchat Download URL at a time.')
    print('Press Enter on a blank line once all ZIPs are imported.')
    i=1
    while True:
        raw=input(f'\nSnapchat ZIP link {i} (blank to finish): ').strip()
        if not raw: break
        try:
            url=clean_link(raw)
            # Use archive URL path hash to keep resume support across repeated sessions.
            identity=hashlib.sha256(urlsplit(url).path.encode()).hexdigest()[:16]
            folder=ROOT/identity
            folder.mkdir(exist_ok=True)
            zip_path=folder/'snapchat.zip'
            data_dir=folder/'media'
            journal=folder/'uploaded.json'
            print('Downloading ZIP...')
            download(url,zip_path)
            print('Extracting supported image/video files...')
            media=extract(zip_path,data_dir)
            print(f'Found {len(media)} media files.')
            if not media:
                print('No supported media in this ZIP. Some Snapchat exports contain only a download index.')
                continue
            completed={}
            if journal.exists():
                completed=json.loads(journal.read_text(encoding='utf-8'))
            errors=0
            for count,path in enumerate(media,1):
                rel=f'{identity}/{path.relative_to(data_dir).as_posix()}'
                if rel in completed: continue
                try:
                    asset_id=upload_asset(path,base,key,rel)
                    completed[rel]=asset_id
                    journal.write_text(json.dumps(completed,indent=2),encoding='utf-8')
                    print(f'  Uploaded {count}/{len(media)}: {path.name[:65]}')
                except Exception as ex:
                    errors+=1
                    print(f'  FAILED {path.name}: {ex}')
            print('Adding uploaded photos/videos to album...')
            add_album(list(dict.fromkeys(completed.values())),album,base,key)
            print(f'ZIP {i}: {len(completed)} uploaded/verified, {errors} failed.')
            if errors:
                print('Rerun and paste this same link to retry failed files; already uploaded files will be skipped.')
                print('Stopped before next ZIP so you can fix the failures.')
                break
            i+=1
        except (ValueError,RuntimeError,requests.RequestException,zipfile.BadZipFile) as ex:
            print('ERROR:',ex)
            print('You can try again with a fresh link. Already downloaded files are retained.')
    print('\nDone for now. Check the Snapchat Memories album in Immich.')
    print('Your original ZIPs and extracted files are in:',ROOT)
    print('NOTE: Snapchat capture dates/GPS stored only in JSON metadata are not restored by this version.')

if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: print('\nInterrupted; local files are retained for restart.')
    except Exception as ex: print('\nERROR:',ex)
    input('\nPress Enter to close...')
