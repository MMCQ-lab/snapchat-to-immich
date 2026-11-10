"""Desktop window for the Snapchat Memories importer, written for non-technical people.

Tkinter ships with Python on Windows/macOS and is bundled in the standalone apps.
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

from destinations import (AppleICloudDestination, FolderDestination, GooglePhotosDestination,
                          ImmichDestination, builtin_google_client, detect_icloud_windows_folder)
from snapimmich_core import (APP_NAME, DEFAULT_ALBUM, DEFAULT_WORK_DIR, VERSION, ImportCancelled,
                             ImportError_, Importer, clean_link, human_size, load_settings,
                             save_settings)

IS_MAC = sys.platform == 'darwin'
IS_WIN = sys.platform.startswith('win')
PAD = 8
SNAP_DATA_URL = 'https://accounts.snapchat.com/accounts/downloadmydata'
GOOGLE_STORAGE_URL = 'https://one.google.com/storage'

RESULT_NAMES = {'google': 'Google Photos', 'icloud': 'iCloud Photos', 'folder': 'your folder',
                'immich': 'Immich'}

DESTS = [
    ('google', 'Google Photos', 'Your Google account'),
    ('icloud', 'Apple iCloud Photos', 'Your iPhone / Mac photo library'),
    ('folder', 'A folder or other cloud', 'OneDrive, Dropbox, Google Drive, a USB drive…'),
    ('immich', 'Immich', 'A private photo server someone set up for you'),
]

HELP_SNAPCHAT = """How to get your Snapchat memories

1. Click "Open Snapchat" below and log in.
2. Under "Select data to include", turn on "Export your Memories".
3. Click "Request Only Memories" and submit.
4. Wait for Snapchat's email. It can take from a few hours to several days.
5. Go back to the same page, then click "See exports".
6. For each file, either:
   • right-click "Download" → "Copy link", then click "Paste link" in this app, or
   • download the ZIP file, then click "Add ZIP files" in this app.

Tip: Snapchat's links stop working after about a week, so use them soon."""

HELP_GOOGLE = """Sending to Google Photos

When you click Start, your web browser opens and asks you to sign in to Google.
Pick your account and click Allow.

Google may say the app "hasn't been verified". That's expected for a small
private app shared between friends: click "Continue".

The app can only ADD photos and create its own album. It can't see or delete
anything else in your Google Photos.

Photos use your Google storage, so check you have enough free space."""

HELP_ICLOUD_MAC = """Sending to Apple iCloud Photos (Mac)

Your memories are added to the Photos app, which uploads them to iCloud.

• Make sure iCloud Photos is on: open Photos → Settings → iCloud.
• The first time, your Mac asks whether this app may control Photos: click OK.
• Leave your Mac awake and online until Photos finishes uploading."""

HELP_ICLOUD_WIN = """Sending to Apple iCloud Photos (Windows)

1. Install "iCloud" from the Microsoft Store and sign in with your Apple ID.
2. Turn on "Photos" in the iCloud app.
3. This app copies your memories into the iCloud Photos folder (usually
   Pictures → iCloud Photos → Uploads), and iCloud uploads them.

Keep the iCloud app running until it finishes uploading."""

HELP_FOLDER = """Saving to a folder or another cloud

Pick any folder. Your memories are copied there, sorted by year, with the
correct dates.

To get them into a cloud service, pick a folder that service syncs, e.g.:
• OneDrive → the "OneDrive" folder
• Dropbox → the "Dropbox" folder
• Google Drive for desktop → "Google Drive" → "My Drive"
• A USB drive or a NAS / network drive"""

HELP_IMMICH = """Sending to Immich

You need two things from the person who runs the Immich server:
• the server address, for example https://photos.example.com
• an API key: in Immich, click your profile picture → Account Settings →
  API Keys → New API Key. Give it permission to upload photos and manage albums.

Paste them here. The key is never saved on this computer."""


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.google_dest: GooglePhotosDestination | None = None
        self.items: list[dict] = []  # {'kind': 'link'|'file', 'value': str}
        self.active_dest = None
        s = load_settings()

        root.title(f'{APP_NAME}')
        root.minsize(680, 600)
        self._style()

        self.dest = tk.StringVar(value=s.get('destination', 'google'))
        self.server = tk.StringVar(value=s.get('server', ''))
        self.api_key = tk.StringVar()
        self.google_file = tk.StringVar(value=s.get('google_client_file', ''))
        self.icloud_folder = tk.StringVar(value=s.get('icloud_folder') or str(detect_icloud_windows_folder() or ''))
        self.folder = tk.StringVar(value=s.get('folder', ''))
        self.by_year = tk.BooleanVar(value=s.get('by_year', True))
        self.album = tk.StringVar(value=s.get('album', DEFAULT_ALBUM))
        self.skip_overlays = tk.BooleanVar(value=s.get('skip_overlays', True))
        self.work_dir = tk.StringVar(value=s.get('work_dir', str(DEFAULT_WORK_DIR)))
        self.status = tk.StringVar(value='')
        self.phase = tk.StringVar(value='')
        self.items_note = tk.StringVar(value='')

        outer = ttk.Frame(root, padding=(PAD * 3, PAD * 2))
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text='Move your Snapchat Memories', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(outer, style='Hint.TLabel', text='Three steps. Nothing is deleted from Snapchat or your computer.'
                  ).grid(row=1, column=0, sticky='w')

        self._step1(outer).grid(row=2, column=0, sticky='ew', pady=(PAD * 2, 0))
        self._step2(outer).grid(row=3, column=0, sticky='nsew', pady=(PAD * 2, 0))
        self._step3(outer).grid(row=4, column=0, sticky='nsew', pady=(PAD * 2, 0))
        outer.rowconfigure(4, weight=1)

        self._on_dest_change()
        self._refresh_items()
        root.protocol('WM_DELETE_WINDOW', self._on_close)
        root.after(100, self._pump)

    # ================================================================== layout

    def _style(self):
        st = ttk.Style(self.root)
        if not (IS_MAC or IS_WIN) and 'clam' in st.theme_names():
            st.theme_use('clam')
        default = tkfont.nametofont('TkDefaultFont')
        size = default.cget('size')
        grow = (lambda n: size + n) if size > 0 else (lambda n: size - n)
        self.f_title = default.copy()
        self.f_title.configure(size=grow(7), weight='bold')
        self.f_step = default.copy()
        self.f_step.configure(size=grow(3), weight='bold')
        self.f_bold = default.copy()
        self.f_bold.configure(weight='bold')
        self.f_big = default.copy()
        self.f_big.configure(size=grow(3), weight='bold')
        st.configure('Title.TLabel', font=self.f_title)
        st.configure('Step.TLabel', font=self.f_step)
        st.configure('Bold.TLabel', font=self.f_bold)
        st.configure('Hint.TLabel', foreground='#666')
        st.configure('Good.TLabel', foreground='#1a7f37')
        st.configure('Bad.TLabel', foreground='#c62828')
        st.configure('Big.TButton', font=self.f_big, padding=(PAD * 3, PAD * 1.5))

    def _header(self, parent, number, text, help_text=None, help_title=None, help_link=None):
        row = ttk.Frame(parent)
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text=f'{number}  {text}', style='Step.TLabel').grid(row=0, column=0, sticky='w')
        if help_text:
            ttk.Button(row, text='Help', width=6, command=lambda: self._help(
                help_title or text, help_text() if callable(help_text) else help_text,
                help_link)).grid(row=0, column=2, sticky='e')
        return row

    def _step1(self, parent):
        box = ttk.Frame(parent)
        box.columnconfigure(0, weight=1)
        self._header(box, '1', 'Where should your memories go?', self._dest_help, 'Help').grid(
            row=0, column=0, sticky='ew')
        choices = ttk.Frame(box)
        choices.grid(row=1, column=0, sticky='ew', pady=(PAD, 0))
        for i, (key, title, desc) in enumerate(DESTS):
            cell = ttk.Frame(choices, padding=(0, 2))
            cell.grid(row=i // 2, column=i % 2, sticky='w', padx=(PAD * 2 if i % 2 == 0 else 0, PAD * 4))
            ttk.Radiobutton(cell, text=title, value=key, variable=self.dest,
                            command=self._on_dest_change).pack(anchor='w')
            ttk.Label(cell, text=desc, style='Hint.TLabel').pack(anchor='w', padx=(22, 0))

        self.panels = {k: ttk.Frame(box) for k, *_ in DESTS}
        for p in self.panels.values():
            p.columnconfigure(1, weight=1)

        # Google
        p = self.panels['google']
        self.google_msg = ttk.Label(p, wraplength=600, justify='left')
        self.google_msg.grid(row=0, column=0, columnspan=3, sticky='w')
        self.google_own = ttk.Frame(p)
        self.google_own.columnconfigure(1, weight=1)
        ttk.Label(self.google_own, text='Google client file').grid(row=0, column=0, sticky='w')
        ttk.Entry(self.google_own, textvariable=self.google_file).grid(row=0, column=1, sticky='ew', padx=PAD)
        ttk.Button(self.google_own, text='Choose…', command=self._pick_google).grid(row=0, column=2)

        # iCloud
        p = self.panels['icloud']
        self.icloud_msg = None
        if IS_MAC:
            ttk.Label(p, wraplength=600, justify='left', text=(
                'They will be added to the Photos app, which uploads them to iCloud. '
                'If your Mac asks to let this app control Photos, click OK.')).grid(row=0, column=0, sticky='w')
        elif IS_WIN:
            ttk.Label(p, text='iCloud Photos folder').grid(row=0, column=0, sticky='w')
            ttk.Entry(p, textvariable=self.icloud_folder).grid(row=0, column=1, sticky='ew', padx=PAD)
            ttk.Button(p, text='Choose…', command=lambda: self._pick_dir(self.icloud_folder)).grid(row=0, column=2)
            self.icloud_msg = ttk.Label(p, style='Hint.TLabel', wraplength=600, justify='left')
            self.icloud_msg.grid(row=1, column=0, columnspan=3, sticky='w')
        else:
            ttk.Label(p, style='Bad.TLabel', text='iCloud Photos needs a Mac or Windows PC. '
                      'Choose "A folder or other cloud" instead.').grid(row=0, column=0, sticky='w')

        # Folder
        p = self.panels['folder']
        ttk.Label(p, text='Save into').grid(row=0, column=0, sticky='w')
        ttk.Entry(p, textvariable=self.folder).grid(row=0, column=1, sticky='ew', padx=PAD)
        ttk.Button(p, text='Choose folder…', command=lambda: self._pick_dir(self.folder)).grid(row=0, column=2)
        ttk.Checkbutton(p, text='Sort into a folder for each year', variable=self.by_year).grid(
            row=1, column=1, sticky='w', pady=(4, 0))

        # Immich
        p = self.panels['immich']
        ttk.Label(p, text='Server address').grid(row=0, column=0, sticky='w', pady=2)
        ttk.Entry(p, textvariable=self.server).grid(row=0, column=1, columnspan=2, sticky='ew', padx=(PAD, 0), pady=2)
        ttk.Label(p, text='API key').grid(row=1, column=0, sticky='w', pady=2)
        self.key_entry = ttk.Entry(p, textvariable=self.api_key, show='•')
        self.key_entry.grid(row=1, column=1, sticky='ew', padx=(PAD, 0), pady=2)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(p, text='Show', variable=self.show_key, command=lambda: self.key_entry.configure(
            show='' if self.show_key.get() else '•')).grid(row=1, column=2, padx=(PAD, 0))
        return box

    def _step2(self, parent):
        box = ttk.Frame(parent)
        box.columnconfigure(0, weight=1)
        self._header(box, '2', 'Add your Snapchat memories', HELP_SNAPCHAT, 'Getting your memories from Snapchat',
                     ('Open Snapchat', SNAP_DATA_URL)).grid(row=0, column=0, columnspan=2, sticky='ew')
        btns = ttk.Frame(box)
        btns.grid(row=1, column=0, columnspan=2, sticky='w', pady=(PAD, 4))
        ttk.Button(btns, text='Paste link', command=self._paste).pack(side='left')
        ttk.Button(btns, text='Add ZIP files…', command=self._add_zips).pack(side='left', padx=PAD)
        ttk.Button(btns, text="I don't have these yet", command=lambda: self._help(
            'Getting your memories from Snapchat', HELP_SNAPCHAT, ('Open Snapchat', SNAP_DATA_URL))
                   ).pack(side='left')
        self.listbox = tk.Listbox(box, height=4, activestyle='none', selectmode='extended')
        self.listbox.grid(row=2, column=0, sticky='nsew')
        self.listbox.bind('<Delete>', lambda e: self._remove())
        self.listbox.bind('<BackSpace>', lambda e: self._remove())
        for seq in ('<Control-v>', '<Command-v>'):
            try:
                self.listbox.bind(seq, lambda e: self._paste())
            except tk.TclError:
                pass
        ttk.Button(box, text='Remove', command=self._remove).grid(row=2, column=1, sticky='n', padx=(PAD, 0))
        ttk.Label(box, textvariable=self.items_note, style='Hint.TLabel').grid(row=3, column=0, sticky='w', pady=(2, 0))
        return box

    def _step3(self, parent):
        box = ttk.Frame(parent)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(5, weight=1)
        self._header(box, '3', 'Start').grid(row=0, column=0, sticky='ew')
        row = ttk.Frame(box)
        row.grid(row=1, column=0, sticky='ew', pady=(PAD, 0))
        self.start_btn = ttk.Button(row, text='Start moving my memories', style='Big.TButton', command=self._start)
        self.start_btn.pack(side='left')
        self.stop_btn = ttk.Button(row, text='Pause', command=self._stop, state='disabled')
        self.stop_btn.pack(side='left', padx=PAD)
        ttk.Label(box, textvariable=self.status, wraplength=620, justify='left', style='Bold.TLabel').grid(
            row=2, column=0, sticky='w', pady=(PAD, 0))
        self.bar = ttk.Progressbar(box, mode='determinate', maximum=1000)
        self.bar.grid(row=3, column=0, sticky='ew', pady=(4, 0))
        ttk.Label(box, textvariable=self.phase, style='Hint.TLabel').grid(row=4, column=0, sticky='w')

        lower = ttk.Frame(box)
        lower.grid(row=5, column=0, sticky='nsew', pady=(PAD, 0))
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(1, weight=1)
        toggles = ttk.Frame(lower)
        toggles.grid(row=0, column=0, sticky='w')
        self.details_btn = ttk.Button(toggles, text='▸ Show details', command=self._toggle_details)
        self.details_btn.pack(side='left')
        self.more_btn = ttk.Button(toggles, text='▸ More options', command=self._toggle_more)
        self.more_btn.pack(side='left', padx=PAD)

        self.log = ScrolledText(lower, height=8, state='disabled', wrap='word', font=tkfont.nametofont('TkFixedFont'))
        self.more = ttk.Frame(lower, padding=(0, PAD))
        m = self.more
        m.columnconfigure(1, weight=1)
        ttk.Label(m, text='Album name').grid(row=0, column=0, sticky='w', pady=2)
        ttk.Entry(m, textvariable=self.album, width=30).grid(row=0, column=1, sticky='w', padx=PAD, pady=2)
        ttk.Checkbutton(m, text='Skip the separate caption/sticker images Snapchat adds (recommended)',
                        variable=self.skip_overlays).grid(row=1, column=0, columnspan=4, sticky='w', pady=2)
        ttk.Label(m, text='Temporary files').grid(row=2, column=0, sticky='w', pady=2)
        ttk.Entry(m, textvariable=self.work_dir).grid(row=2, column=1, sticky='ew', padx=PAD, pady=2)
        ttk.Button(m, text='Choose…', command=lambda: self._pick_dir(self.work_dir)).grid(row=2, column=2)
        ttk.Button(m, text='Open', command=lambda: self._open(self.work_dir.get())).grid(row=2, column=3, padx=(4, 0))
        ttk.Label(m, style='Hint.TLabel', wraplength=560, justify='left', text=(
            'Downloads are kept here so you can pause and resume. Delete this folder once you have '
            'checked your photos arrived.')).grid(row=3, column=1, columnspan=3, sticky='w')
        ttk.Label(m, text='Google (advanced)').grid(row=4, column=0, sticky='w', pady=(PAD, 2))
        ttk.Button(m, text='Use my own Google client file…', command=self._pick_google).grid(
            row=4, column=1, sticky='w', padx=PAD, pady=(PAD, 2))
        ttk.Label(m, style='Hint.TLabel', text=f'Version {VERSION}').grid(row=5, column=0, sticky='w', pady=(PAD, 0))
        return box

    # ================================================================== small helpers

    def _dest_help(self):
        return {'google': HELP_GOOGLE, 'icloud': HELP_ICLOUD_MAC if IS_MAC else HELP_ICLOUD_WIN,
                'folder': HELP_FOLDER, 'immich': HELP_IMMICH}[self.dest.get()]

    def _help(self, title, text, link=None):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        frame = ttk.Frame(win, padding=PAD * 3)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=text, justify='left').pack(anchor='w')
        btns = ttk.Frame(frame)
        btns.pack(anchor='e', pady=(PAD * 2, 0))
        if link:
            ttk.Button(btns, text=link[0], command=lambda: webbrowser.open(link[1])).pack(side='left', padx=PAD)
        if 'Google storage' in text:
            ttk.Button(btns, text='Check my Google storage',
                       command=lambda: webbrowser.open(GOOGLE_STORAGE_URL)).pack(side='left', padx=PAD)
        ttk.Button(btns, text='Got it', command=win.destroy).pack(side='left')
        win.bind('<Escape>', lambda e: win.destroy())
        self._center(win)
        return win

    def _center(self, win):
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_reqheight()) // 3)
        win.geometry(f'+{max(0, x)}+{max(0, y)}')

    def _toggle_details(self):
        if self.log.winfo_ismapped():
            self.log.grid_remove()
            self.details_btn.configure(text='▸ Show details')
        else:
            self.log.grid(row=1, column=0, sticky='nsew', pady=(PAD, 0))
            self.details_btn.configure(text='▾ Hide details')

    def _toggle_more(self):
        if self.more.winfo_ismapped():
            self.more.grid_remove()
            self.more_btn.configure(text='▸ More options')
        else:
            self.more.grid(row=2, column=0, sticky='ew')
            self.more_btn.configure(text='▾ More options')

    def _on_dest_change(self):
        d = self.dest.get()
        for k, p in self.panels.items():
            if k == d:
                p.grid(row=2, column=0, sticky='ew', pady=(PAD, 0), padx=(PAD * 2, 0))
            else:
                p.grid_remove()
        if d == 'google':
            if self.google_file.get().strip():
                self.google_msg.configure(style='TLabel', text='Using your own Google client file. '
                                          'Your browser will open to sign in when you click Start.')
                self.google_own.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(4, 0))
            elif builtin_google_client():
                self.google_msg.configure(style='TLabel', text='When you click Start, your browser opens so '
                                          "you can sign in to Google. That's it.")
                self.google_own.grid_remove()
            else:
                self.google_msg.configure(style='Bad.TLabel', text=(
                    'This copy of the app has no Google sign-in built in. Ask the person who shared it '
                    'for the latest version, or choose a Google client file (see README).'))
                self.google_own.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(4, 0))
        if d == 'icloud' and self.icloud_msg is not None:
            found = bool(self.icloud_folder.get()) and Path(self.icloud_folder.get()).is_dir()
            self.icloud_msg.configure(text='Found your iCloud Photos folder ✔' if found else
                                      'iCloud for Windows not found. Click Help to see how to set it up.')
        self._update_ready()

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if d:
            var.set(d)
            self._on_dest_change()

    def _pick_google(self):
        f = filedialog.askopenfilename(title='Google OAuth client file (.json)',
                                       filetypes=[('JSON', '*.json'), ('All files', '*')])
        if f:
            self.google_file.set(f)
            self.google_dest = None
            self.dest.set('google')
            self._on_dest_change()

    def _open(self, target: str):
        if not target:
            return
        if target == 'photos-app':
            subprocess.Popen(['open', '-a', 'Photos'])
        elif target.startswith(('http://', 'https://')):
            webbrowser.open(target)
        else:
            path = Path(target).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            if IS_WIN:
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(['open' if IS_MAC else 'xdg-open', str(path)])

    # ================================================================== items (links / ZIPs)

    def add_links_from_text(self, text: str) -> tuple[int, list]:
        candidates = re.findall(r'\[[^\]]*\]\([^)]*\)|https?://\S+', text or '')
        added, bad = 0, []
        for c in candidates:
            try:
                url, expiry = clean_link(c)
            except ImportError_ as ex:
                bad.append(str(ex))
                continue
            if any(i['value'] == url for i in self.items):
                continue
            self.items.append({'kind': 'link', 'value': url, 'expiry': expiry})
            added += 1
        self._refresh_items()
        return added, bad

    def _paste(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ''
        added, bad = self.add_links_from_text(text)
        if not added:
            msg = bad[0] if bad else 'There is no link on your clipboard yet.'
            messagebox.showinfo('Paste link', msg + '\n\nOn Snapchat\'s "See exports" page, right-click '
                                '"Download" and choose "Copy link", then click "Paste link" again.\n'
                                'Or download the file and use "Add ZIP files".')

    def _add_zips(self):
        files = filedialog.askopenfilenames(title='Choose your Snapchat ZIP files',
                                            filetypes=[('ZIP files', '*.zip'), ('All files', '*')])
        for f in files:
            if not any(i['value'] == f for i in self.items):
                self.items.append({'kind': 'file', 'value': f})
        self._refresh_items()

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            del self.items[i]
        self._refresh_items()

    def _refresh_items(self):
        self.listbox.delete(0, 'end')
        links = files = 0
        for it in self.items:
            if it['kind'] == 'link':
                links += 1
                self.listbox.insert('end', f'  Snapchat download {links}   ·   link works until '
                                    f'{it["expiry"].astimezone():%b %d}')
            else:
                files += 1
                try:
                    size = human_size(Path(it['value']).stat().st_size)
                except OSError:
                    size = 'file missing!'
                self.listbox.insert('end', f'  {Path(it["value"]).name}   ·   {size}')
        if not self.items:
            self.items_note.set('Nothing added yet. Paste a Snapchat download link or add ZIP files.')
        else:
            parts = ([f'{links} link' + ('s' if links != 1 else '')] if links else []) + \
                    ([f'{files} ZIP file' + ('s' if files != 1 else '')] if files else [])
            self.items_note.set(' and '.join(parts) + ' ready ✔')
        self._update_ready()

    def _update_ready(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.items:
            self.status.set('Add your Snapchat memories in step 2 to begin.')
        else:
            self.status.set(f'Ready to move your memories to {RESULT_NAMES[self.dest.get()]}.')

    # ================================================================== settings → destination

    def _settings(self) -> dict:
        return {'destination': self.dest.get(), 'server': self.server.get().strip(),
                'google_client_file': self.google_file.get().strip(),
                'icloud_folder': self.icloud_folder.get().strip(), 'folder': self.folder.get().strip(),
                'by_year': self.by_year.get(), 'album': self.album.get().strip() or DEFAULT_ALBUM,
                'skip_overlays': self.skip_overlays.get(), 'work_dir': self.work_dir.get().strip()}

    def _make_destination(self):
        s, kw = self._settings(), {'album_name': self.album.get(), 'cancel': self.cancel}
        d = s['destination']
        if d == 'immich':
            if not s['server']:
                raise ImportError_('Type the Immich server address in step 1 (ask whoever runs the server).')
            if s['server'].startswith('http://') and not messagebox.askyesno('Unencrypted connection', (
                    'This address starts with http://, so the connection is not encrypted.\n\n'
                    'Only continue if you are on the same home network as the server or on Tailscale. Continue?')):
                raise ImportCancelled()
            return ImmichDestination(s['server'], self.api_key.get(), **kw)
        if d == 'google':
            want = s['google_client_file']
            g = self.google_dest
            target = str(Path(want).expanduser()) if want else str(builtin_google_client() or '')
            if g and str(g.client_secret_file) == target:
                g.album_name, g.cancel, g.album_id = s['album'], self.cancel, ''
                return g
            self.google_dest = GooglePhotosDestination(want, **kw)
            return self.google_dest
        if d == 'icloud':
            return AppleICloudDestination(s['icloud_folder'], **kw)
        if not s['folder']:
            raise ImportError_('Choose a folder in step 1 (click "Choose folder…").')
        return FolderDestination(s['folder'], by_year=s['by_year'], **kw)

    # ================================================================== run

    def _busy(self, busy: bool):
        self.start_btn.configure(state='disabled' if busy else 'normal')
        self.stop_btn.configure(state='normal' if busy else 'disabled')

    def _thread(self, fn):
        self.cancel.clear()
        self._busy(True)

        def wrapper():
            try:
                fn()
            except ImportCancelled:
                self.events.put(('log', '\nPaused. Click Start again any time to continue where you left off.'))
                self.events.put(('status', '⏸  Paused. Click Start to continue where you left off.'))
                self.events.put(('done', None))
            except ImportError_ as ex:
                self.events.put(('log', f'ERROR: {ex}'))
                self.events.put(('error', str(ex)))
            except Exception as ex:  # never die silently
                self.events.put(('log', f'Unexpected error: {ex!r}'))
                self.events.put(('error', f'Something unexpected went wrong: {ex}'))
        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def _start(self):
        if not self.items:
            self._help('Add your memories first', HELP_SNAPCHAT, ('Open Snapchat', SNAP_DATA_URL))
            return
        try:
            dest = self._make_destination()
        except ImportCancelled:
            return
        except ImportError_ as ex:
            messagebox.showwarning('One more thing', str(ex))
            return
        save_settings(self._settings())
        s = self._settings()
        links = [i['value'] for i in self.items if i['kind'] == 'link']
        files = [i['value'] for i in self.items if i['kind'] == 'file']
        self._clear_log()
        importer = Importer(dest, work_dir=Path(s['work_dir'] or DEFAULT_WORK_DIR),
                            skip_overlays=s['skip_overlays'], log=self._log_ts,
                            progress=lambda ph, d, t: self.events.put(('progress', (ph, d, t))),
                            cancel=self.cancel)
        self.active_dest = dest

        def prepare():
            browser = dest.key == 'google' and not getattr(dest, 'access_token', '')
            self.events.put(('status', 'Connecting…' + ('  Your browser will open: sign in to Google there.'
                                                        if browser else '')))
            self.events.put(('log', dest.connect()))
            self.events.put(('status', 'Checking you have enough storage space…'))
            report = importer.check_space(links, files)
            self.events.put(('log', report.summary()))
            self.events.put(('space', (report, importer, links, files)))
        self._thread(prepare)

    def space_decision(self, report) -> str:
        """'stop', 'ask' or 'go' for a SpaceReport (kept separate so it can be tested)."""
        if not report.local_ok:
            return 'stop'
        if report.dest_ok is False:
            return 'ask'
        return 'go'

    def _after_space_check(self, report, importer, links, files):
        decision = self.space_decision(report)
        if decision == 'stop':
            short = report.local_needed - report.local_free
            messagebox.showerror('Not enough space on this computer', (
                f'Moving these memories needs about {human_size(report.local_needed)} of free space on '
                f'this computer, but only {human_size(report.local_free)} is free.\n\n'
                f'Free up about {human_size(short + 1024 ** 3)} (empty the Trash or Recycle Bin, delete '
                f'old downloads), or under "More options" choose a "Temporary files" folder on a '
                f'bigger drive. Then click Start again.'))
            self.status.set('✖  Not enough free space on this computer. See the message for what to do.')
            self._busy(False)
            return
        if decision == 'ask' and not messagebox.askyesno('Destination may be full', (
                f'Your memories are about {human_size(report.media)}, but {report.dest_where.lower()} '
                f'only has {human_size(report.dest_free)} free.\n\nSome photos may not fit. Continue anyway?')):
            self.status.set('Stopped before starting: the destination does not have enough space.')
            self._busy(False)
            return
        if report.dest_free is None and self.active_dest.key == 'google' and report.media:
            self._log_ts(f'Make sure your Google account has at least {human_size(report.media)} free.')

        def job():
            self.events.put(('status', f'Moving about {human_size(report.media)} of memories… '
                                       'You can keep using your computer.'))
            ok, bad = importer.run(links, files)
            self.events.put(('finished', (ok, bad, importer.sent_total, importer.done_total)))
        self._thread(job)

    def _finished(self, ok, bad, sent, done):
        self._busy(False)
        self.bar['value'] = 1000
        self.phase.set('')
        dest = self.active_dest
        name = RESULT_NAMES[dest.key]
        if bad:
            self.status.set(f'⚠  Finished with some problems: {done} photos/videos are in {name}. '
                            'Click Start again to retry the rest.')
            if not self.log.winfo_ismapped():
                self._toggle_details()
        else:
            self.status.set(f'✔  All done! {done} photos and videos are in {name}.')
        win = tk.Toplevel(self.root)
        win.title('Finished')
        win.transient(self.root)
        f = ttk.Frame(win, padding=PAD * 3)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text='All done!' if not bad else 'Almost done', style='Title.TLabel').pack(anchor='w')
        lines = [f'{done} photos and videos are now in {name}' +
                 (f' (album "{dest.album_name}").' if dest.key != 'folder' else '.')]
        if sent < done:
            lines.append(f'{done - sent} of them had already been moved in an earlier run.')
        if bad:
            lines.append(f'{bad} export(s) had problems. Click Start again to retry; finished files are skipped.')
        if dest.key == 'icloud':
            lines.append('iCloud keeps uploading in the background. Leave your computer on and online for a while.')
        lines.append('Once you have checked your photos, you can delete the temporary files '
                     '(More options → Temporary files → Open).')
        ttk.Label(f, text='\n\n'.join(lines), justify='left', wraplength=460).pack(anchor='w', pady=(PAD, 0))
        b = ttk.Frame(f)
        b.pack(anchor='e', pady=(PAD * 2, 0))
        where = dest.result_location()
        if where:
            ttk.Button(b, text=f'Open {name}', command=lambda: self._open(where)).pack(side='left', padx=PAD)
        ttk.Button(b, text='Close', command=win.destroy).pack(side='left')
        self._center(win)
        self.finish_window = win

    def _stop(self):
        self.cancel.set()
        self.status.set('Pausing after the current file…')
        self.stop_btn.configure(state='disabled')

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno('Still working', 'Your memories are still being moved. Pause and quit?\n\n'
                                       'Next time, add the same links/files and click Start: '
                                       'it continues where it stopped.'):
                return
            self.cancel.set()
        save_settings(self._settings())
        self.root.destroy()

    # ================================================================== worker → UI

    def _log_ts(self, msg: str):
        self.events.put(('log', msg))

    def _clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def _pump(self):
        try:
            while True:
                kind, val = self.events.get_nowait()
                if kind == 'log':
                    self.log.configure(state='normal')
                    self.log.insert('end', val + '\n')
                    self.log.see('end')
                    self.log.configure(state='disabled')
                elif kind == 'status':
                    self.status.set(val)
                elif kind == 'error':
                    self._busy(False)
                    self.status.set('✖  ' + val)
                    messagebox.showerror('Something went wrong', val)
                elif kind == 'done':
                    self._busy(False)
                elif kind == 'space':
                    self._after_space_check(*val)
                elif kind == 'finished':
                    self._finished(*val)
                elif kind == 'progress':
                    phase, done, total = val
                    if phase == 'download':
                        self.phase.set(f'Downloading from Snapchat: {human_size(done)}'
                                       + (f' of {human_size(total)}' if total else ''))
                    elif phase == 'extract':
                        self.phase.set(f'Unpacking: {done} of {total}')
                    elif phase == 'upload':
                        self.phase.set(f'Sending: {done} of {total}')
                    if phase != 'archive':
                        self.bar['value'] = 1000 * done / total if total else 0
        except queue.Empty:
            pass
        self.root.after(100, self._pump)


def main():
    if IS_WIN:
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()

    def report(exc, val, tb):  # no console in the app versions, so show errors in a dialog
        import traceback
        messagebox.showerror('Unexpected error', ''.join(traceback.format_exception(exc, val, tb))[-1500:])
    root.report_callback_exception = report
    try:
        App(root)
    except Exception as ex:
        messagebox.showerror(APP_NAME, f'Could not start: {ex!r}')
        raise
    root.mainloop()


if __name__ == '__main__':
    main()
