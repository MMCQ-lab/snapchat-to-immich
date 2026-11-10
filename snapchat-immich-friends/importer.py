#!/usr/bin/env python3
"""Snapchat Memories importer.

Opens the desktop window by default. Use ``--cli`` for the text-only version (also used
automatically when the window can't be shown, e.g. Python installed without Tk).
"""
from __future__ import annotations

import getpass
import sys

if sys.version_info < (3, 9):
    print('This tool needs Python 3.9 or newer. Download it from https://www.python.org/downloads/')
    input('\nPress Enter to close...')
    sys.exit(1)

from pathlib import Path  # noqa: E402

from destinations import (AppleICloudDestination, FolderDestination,  # noqa: E402
                          GooglePhotosDestination, ImmichDestination, detect_icloud_windows_folder)
from snapimmich_core import (APP_NAME, DEFAULT_ALBUM, DEFAULT_WORK_DIR, ImportCancelled,  # noqa: E402
                             ImportError_, Importer)


def ask(prompt: str, default: str = '') -> str:
    v = input(f'{prompt}{f" [{default}]" if default else ""}: ').strip()
    return v or default


def cli() -> None:
    print(f'\n{APP_NAME.upper()} (console mode)')
    print('Nothing is deleted automatically. Ctrl+C stops; run again to resume.\n')
    print('Where should your memories go?')
    print('  1) Immich  2) Google Photos  3) Apple iCloud Photos  4) Any other cloud / folder')
    choice = ask('Choose 1-4', '1')
    album = ask('Album name', DEFAULT_ALBUM)
    if choice == '2':
        dest = GooglePhotosDestination(ask('Path to Google OAuth client JSON'), album_name=album)
    elif choice == '3':
        folder = ''
        if sys.platform.startswith('win'):
            folder = ask('iCloud upload folder', str(detect_icloud_windows_folder() or ''))
        dest = AppleICloudDestination(folder, album_name=album)
    elif choice == '4':
        dest = FolderDestination(ask('Destination folder'), album_name=album)
    else:
        server = ask('Immich server URL (e.g. https://photos.example.com)')
        if server.startswith('http://'):
            print('WARNING: http:// does not encrypt your API key. Use https:// outside a trusted network.')
            if input('Continue with http? (type YES): ').strip() != 'YES':
                return
        dest = ImmichDestination(server, getpass.getpass('Immich API key (hidden): '), album_name=album)
    print(dest.connect())

    links, files = [], []
    print('\nPaste Snapchat download links or paths to ZIP files, one per line. Blank line to start.')
    while True:
        raw = input(f'Item {len(links) + len(files) + 1}: ').strip().strip('"')
        if not raw:
            break
        (files if Path(raw).expanduser().is_file() else links).append(raw)
    if not links and not files:
        return
    ok, bad = Importer(dest, work_dir=DEFAULT_WORK_DIR).run(links, files)
    print(f'\nFinished: {ok} export(s) OK, {bad} with problems.')
    print('Downloaded/extracted files are kept in:', DEFAULT_WORK_DIR)


def main() -> None:
    if '--cli' not in sys.argv:
        try:
            import gui
            gui.main()
            return
        except ImportError as ex:  # tkinter missing (e.g. Homebrew Python without python-tk)
            print(f'Window mode unavailable ({ex}); using console mode.\n')
        except Exception as ex:  # no display, broken Tk, etc.
            if ex.__class__.__name__ != 'TclError':
                raise
            print(f'Window mode unavailable ({ex}); using console mode.\n')
    try:
        cli()
    except KeyboardInterrupt:
        print('\nInterrupted; local files are kept so you can resume.')
    except (ImportError_, ImportCancelled) as ex:
        print('\nERROR:', ex)
    input('\nPress Enter to close...')


if __name__ == '__main__':
    main()
