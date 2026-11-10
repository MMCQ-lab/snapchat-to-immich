"""Desktop window for the Snapchat Memories importer (Tkinter: ships with Python on Windows/macOS)."""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

from destinations import (AppleICloudDestination, FolderDestination, GooglePhotosDestination,
                          ImmichDestination, detect_icloud_windows_folder)
from snapimmich_core import (APP_NAME, DEFAULT_ALBUM, DEFAULT_WORK_DIR, VERSION, ImportCancelled,
                             ImportError_, Importer, human_size, load_settings, save_settings,
                             split_links)

IS_MAC = sys.platform == 'darwin'
IS_WIN = sys.platform.startswith('win')
GOOGLE_HELP = 'https://developers.google.com/photos/overview/configure-your-app'
PAD = 8


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.google_dest: GooglePhotosDestination | None = None  # keeps sign-in for the session
        self.zip_files: list[str] = []
        s = load_settings()

        root.title(f'{APP_NAME}  v{VERSION}')
        root.minsize(720, 640)
        self._style()

        self.dest = tk.StringVar(value=s.get('destination', 'immich'))
        self.server = tk.StringVar(value=s.get('server', ''))
        self.api_key = tk.StringVar()
        self.show_key = tk.BooleanVar(value=False)
        self.google_file = tk.StringVar(value=s.get('google_client_file', ''))
        self.icloud_folder = tk.StringVar(value=s.get('icloud_folder') or str(detect_icloud_windows_folder() or ''))
        self.folder = tk.StringVar(value=s.get('folder', ''))
        self.by_year = tk.BooleanVar(value=s.get('by_year', True))
        self.album = tk.StringVar(value=s.get('album', DEFAULT_ALBUM))
        self.skip_overlays = tk.BooleanVar(value=s.get('skip_overlays', True))
        self.work_dir = tk.StringVar(value=s.get('work_dir', str(DEFAULT_WORK_DIR)))
        self.status = tk.StringVar(value='Choose where your memories should go, then add your Snapchat exports.')

        outer = ttk.Frame(root, padding=PAD * 2)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        ttk.Label(outer, text='Snapchat Memories  →  your photo library', style='Title.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, PAD))

        self._build_destination(outer).grid(row=1, column=0, sticky='ew', pady=(0, PAD))
        self._build_sources(outer).grid(row=2, column=0, sticky='nsew', pady=(0, PAD))
        self._build_run(outer).grid(row=3, column=0, sticky='nsew')

        self._on_dest_change()
        root.protocol('WM_DELETE_WINDOW', self._on_close)
        root.after(100, self._pump)

    # ------------------------------------------------------------------ layout

    def _style(self):
        st = ttk.Style(self.root)
        if not (IS_MAC or IS_WIN) and 'clam' in st.theme_names():
            st.theme_use('clam')
        default = tkfont.nametofont('TkDefaultFont')
        self.title_font = default.copy()
        self.title_font.configure(size=default.cget('size') + 5 if default.cget('size') > 0
                                  else default.cget('size') - 5, weight='bold')
        self.bold_font = default.copy()
        self.bold_font.configure(weight='bold')
        st.configure('Title.TLabel', font=self.title_font)
        st.configure('Hint.TLabel', foreground='#666')
        st.configure('Accent.TButton', font=self.bold_font)

    def _build_destination(self, parent):
        box = ttk.LabelFrame(parent, text=' 1. Send to ', padding=PAD)
        box.columnconfigure(1, weight=1)
        radios = ttk.Frame(box)
        radios.grid(row=0, column=0, columnspan=3, sticky='w')
        for i, (key, label) in enumerate([('immich', 'Immich'), ('google', 'Google Photos'),
                                          ('icloud', 'Apple iCloud Photos'),
                                          ('folder', 'Any other cloud / folder')]):
            ttk.Radiobutton(radios, text=label, value=key, variable=self.dest,
                            command=self._on_dest_change).grid(row=0, column=i, padx=(0, PAD * 2))

        self.panels = {k: ttk.Frame(box) for k in ('immich', 'google', 'icloud', 'folder')}
        for p in self.panels.values():
            p.columnconfigure(1, weight=1)

        # Immich
        p = self.panels['immich']
        ttk.Label(p, text='Server address').grid(row=0, column=0, sticky='w', pady=2)
        ttk.Entry(p, textvariable=self.server).grid(row=0, column=1, columnspan=2, sticky='ew', pady=2)
        ttk.Label(p, text='API key').grid(row=1, column=0, sticky='w', pady=2)
        self.key_entry = ttk.Entry(p, textvariable=self.api_key, show='•')
        self.key_entry.grid(row=1, column=1, sticky='ew', pady=2)
        ttk.Checkbutton(p, text='Show', variable=self.show_key, command=lambda: self.key_entry.configure(
            show='' if self.show_key.get() else '•')).grid(row=1, column=2, padx=(PAD, 0))
        ttk.Label(p, style='Hint.TLabel', wraplength=600, justify='left', text='Immich → Account Settings → API Keys. Needs asset upload + '
                  'album permissions. The key is never saved to disk.').grid(row=2, column=1, columnspan=2, sticky='w')

        # Google
        p = self.panels['google']
        ttk.Label(p, text='OAuth client file').grid(row=0, column=0, sticky='w', pady=2)
        ttk.Entry(p, textvariable=self.google_file).grid(row=0, column=1, sticky='ew', pady=2)
        ttk.Button(p, text='Browse…', command=self._pick_google).grid(row=0, column=2, padx=(PAD, 0))
        hint = ttk.Label(p, style='Hint.TLabel', cursor='hand2', wraplength=600, justify='left', text=(
            'Google requires your own free "Desktop app" OAuth client (one-time, ~5 min). '
            'Steps are in the README — click here for Google\'s guide.'))
        hint.grid(row=1, column=1, columnspan=2, sticky='w')
        hint.bind('<Button-1>', lambda e: webbrowser.open(GOOGLE_HELP))

        # iCloud
        p = self.panels['icloud']
        if IS_MAC:
            ttk.Label(p, wraplength=640, justify='left', text=(
                'Memories are imported into the Photos app, which uploads them to iCloud when '
                'iCloud Photos is on (Photos → Settings → iCloud). macOS will ask once to let this '
                'app control Photos — click OK.')).grid(row=0, column=0, columnspan=3, sticky='w')
        elif IS_WIN:
            ttk.Label(p, text='iCloud upload folder').grid(row=0, column=0, sticky='w', pady=2)
            ttk.Entry(p, textvariable=self.icloud_folder).grid(row=0, column=1, sticky='ew', pady=2)
            ttk.Button(p, text='Browse…', command=lambda: self._pick_dir(self.icloud_folder)).grid(
                row=0, column=2, padx=(PAD, 0))
            ttk.Label(p, style='Hint.TLabel', wraplength=600, justify='left', text=(
                'Needs iCloud for Windows with Photos turned on. Files copied here are uploaded by '
                'the iCloud app in the background — keep it running until it finishes.')).grid(
                row=1, column=1, columnspan=2, sticky='w')
        else:
            ttk.Label(p, text='Apple iCloud Photos needs macOS or Windows. Use "Any other cloud / folder".',
                      style='Hint.TLabel').grid(row=0, column=0, sticky='w')

        # Folder
        p = self.panels['folder']
        ttk.Label(p, text='Folder').grid(row=0, column=0, sticky='w', pady=2)
        ttk.Entry(p, textvariable=self.folder).grid(row=0, column=1, sticky='ew', pady=2)
        ttk.Button(p, text='Browse…', command=lambda: self._pick_dir(self.folder)).grid(
            row=0, column=2, padx=(PAD, 0))
        ttk.Checkbutton(p, text='Sort into year folders', variable=self.by_year).grid(
            row=1, column=1, sticky='w')
        ttk.Label(p, style='Hint.TLabel', wraplength=600, justify='left', text=(
            'Pick a folder synced by OneDrive, Dropbox, Google Drive, Nextcloud, Synology/NAS, '
            'or any backup drive. Dates are fixed so photos sort correctly.')).grid(
            row=2, column=1, columnspan=2, sticky='w')

        self.panel_row = 1
        common = ttk.Frame(box)
        common.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(PAD, 0))
        common.columnconfigure(1, weight=1)
        ttk.Label(common, text='Album name').grid(row=0, column=0, sticky='w')
        ttk.Entry(common, textvariable=self.album, width=30).grid(row=0, column=1, sticky='w', padx=(PAD, 0))
        self.test_btn = ttk.Button(common, text='Test connection', command=self._test)
        self.test_btn.grid(row=0, column=2, sticky='e')
        self.dest_box = box
        return box

    def _build_sources(self, parent):
        box = ttk.LabelFrame(parent, text=' 2. Snapchat exports ', padding=PAD)
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text='Paste Snapchat download links (one per line) — or add ZIPs you already downloaded:',
                  ).grid(row=0, column=0, columnspan=2, sticky='w')
        self.links = tk.Text(box, height=4, wrap='none', undo=True)
        self.links.grid(row=1, column=0, sticky='nsew', pady=(2, PAD))
        btns = ttk.Frame(box)
        btns.grid(row=1, column=1, sticky='n', padx=(PAD, 0))
        ttk.Button(btns, text='Paste', command=self._paste).pack(fill='x')
        ttk.Button(btns, text='Clear', command=lambda: self.links.delete('1.0', 'end')).pack(fill='x', pady=2)

        ttk.Label(box, text='ZIP files already on this computer:').grid(row=2, column=0, sticky='w')
        self.files_list = tk.Listbox(box, height=3, activestyle='none', selectmode='extended')
        self.files_list.grid(row=3, column=0, sticky='ew')
        fbtns = ttk.Frame(box)
        fbtns.grid(row=3, column=1, sticky='n', padx=(PAD, 0))
        ttk.Button(fbtns, text='Add ZIPs…', command=self._add_zips).pack(fill='x')
        ttk.Button(fbtns, text='Remove', command=self._remove_zip).pack(fill='x', pady=2)

        opts = ttk.Frame(box)
        opts.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(PAD, 0))
        opts.columnconfigure(2, weight=1)
        ttk.Checkbutton(opts, text='Skip caption/sticker overlay images (-overlay.png)',
                        variable=self.skip_overlays).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(opts, text='Working folder').grid(row=1, column=0, sticky='w', pady=(4, 0))
        ttk.Entry(opts, textvariable=self.work_dir).grid(row=1, column=1, columnspan=2, sticky='ew',
                                                         padx=(PAD, 0), pady=(4, 0))
        ttk.Button(opts, text='Browse…', command=lambda: self._pick_dir(self.work_dir)).grid(
            row=1, column=3, padx=(PAD, 0), pady=(4, 0))
        ttk.Button(opts, text='Open', command=self._open_work_dir).grid(row=1, column=4, padx=(4, 0), pady=(4, 0))
        return box

    def _build_run(self, parent):
        box = ttk.LabelFrame(parent, text=' 3. Import ', padding=PAD)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(3, weight=1)
        bar = ttk.Frame(box)
        bar.grid(row=0, column=0, sticky='ew')
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.status, wraplength=520).grid(row=0, column=0, sticky='w')
        self.start_btn = ttk.Button(bar, text='Start import', style='Accent.TButton', command=self._start)
        self.start_btn.grid(row=0, column=1, padx=(PAD, 0))
        self.stop_btn = ttk.Button(bar, text='Stop', command=self._stop, state='disabled')
        self.stop_btn.grid(row=0, column=2, padx=(4, 0))
        self.phase = tk.StringVar(value='')
        ttk.Label(box, textvariable=self.phase, style='Hint.TLabel').grid(row=1, column=0, sticky='w', pady=(PAD, 0))
        self.bar = ttk.Progressbar(box, mode='determinate', maximum=1000)
        self.bar.grid(row=2, column=0, sticky='ew', pady=(2, PAD))
        self.log = ScrolledText(box, height=10, state='disabled', wrap='word', font=tkfont.nametofont('TkFixedFont'))
        self.log.grid(row=3, column=0, sticky='nsew')
        return box

    # ------------------------------------------------------------------ actions

    def _on_dest_change(self):
        for k, p in self.panels.items():
            if k == self.dest.get():
                p.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(PAD, 0))
            else:
                p.grid_remove()

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if d:
            var.set(d)

    def _pick_google(self):
        f = filedialog.askopenfilename(title='Google OAuth client JSON',
                                       filetypes=[('JSON', '*.json'), ('All files', '*')])
        if f:
            self.google_file.set(f)

    def _paste(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        current = self.links.get('1.0', 'end').strip()
        self.links.insert('end', ('\n' if current else '') + text.strip())

    def _add_zips(self):
        files = filedialog.askopenfilenames(title='Snapchat export ZIPs',
                                            filetypes=[('ZIP archives', '*.zip'), ('All files', '*')])
        for f in files:
            if f not in self.zip_files:
                self.zip_files.append(f)
                try:
                    size = human_size(Path(f).stat().st_size)
                except OSError:
                    size = '?'
                self.files_list.insert('end', f'{Path(f).name}   ({size})')

    def _remove_zip(self):
        for i in reversed(self.files_list.curselection()):
            self.files_list.delete(i)
            del self.zip_files[i]

    def _open_work_dir(self):
        path = Path(self.work_dir.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        import subprocess
        import os
        if IS_WIN:
            os.startfile(path)  # type: ignore[attr-defined]
        elif IS_MAC:
            subprocess.Popen(['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])

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
            server = s['server']
            if server.startswith('http://') and not self._confirm_http(server):
                raise ImportCancelled()
            return ImmichDestination(server, self.api_key.get(), **kw)
        if d == 'google':
            g = self.google_dest
            if g and str(g.client_secret_file) == str(Path(s['google_client_file']).expanduser()):
                g.album_name, g.cancel, g.album_id = kw['album_name'].strip() or DEFAULT_ALBUM, self.cancel, ''
                return g
            self.google_dest = GooglePhotosDestination(s['google_client_file'], **kw)
            return self.google_dest
        if d == 'icloud':
            return AppleICloudDestination(s['icloud_folder'], **kw)
        return FolderDestination(s['folder'], by_year=s['by_year'], **kw)

    def _confirm_http(self, server: str) -> bool:
        return messagebox.askyesno('Unencrypted connection', (
            f'{server} uses http://, so your API key is sent unencrypted.\n\n'
            'Only continue on a trusted private network (home LAN or Tailscale). Continue?'))

    def _busy(self, busy: bool):
        self.start_btn.configure(state='disabled' if busy else 'normal')
        self.test_btn.configure(state='disabled' if busy else 'normal')
        self.stop_btn.configure(state='normal' if busy else 'disabled')

    def _run_in_thread(self, fn):
        self.cancel.clear()
        self._busy(True)
        save_settings(self._settings())

        def wrapper():
            try:
                fn()
            except ImportCancelled:
                self.events.put(('log', '\nStopped. Everything downloaded so far is kept; start again to resume.'))
                self.events.put(('status', 'Stopped.'))
            except ImportError_ as ex:
                self.events.put(('log', f'ERROR: {ex}'))
                self.events.put(('error', str(ex)))
            except Exception as ex:  # unexpected: show it rather than die silently
                self.events.put(('log', f'Unexpected error: {ex!r}'))
                self.events.put(('error', f'Unexpected error: {ex}'))
            finally:
                self.events.put(('done', None))
        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def _test(self):
        try:
            dest = self._make_destination()
        except ImportCancelled:
            return
        except ImportError_ as ex:
            messagebox.showerror('Check settings', str(ex))
            return
        dest.log = self._log_threadsafe
        self.status.set('Connecting…')

        def job():
            msg = dest.connect()
            self.events.put(('log', msg))
            self.events.put(('status', '✔ ' + msg))
        self._run_in_thread(job)

    def _start(self):
        links = split_links(self.links.get('1.0', 'end'))
        if not links and not self.zip_files:
            messagebox.showinfo('Nothing to import', 'Paste at least one Snapchat download link or add a ZIP file.')
            return
        try:
            dest = self._make_destination()
        except ImportCancelled:
            return
        except ImportError_ as ex:
            messagebox.showerror('Check settings', str(ex))
            return
        files = list(self.zip_files)
        s = self._settings()
        self._clear_log()

        def job():
            importer = Importer(dest, work_dir=Path(s['work_dir'] or DEFAULT_WORK_DIR),
                                skip_overlays=s['skip_overlays'], log=self._log_threadsafe,
                                progress=lambda ph, d, t: self.events.put(('progress', (ph, d, t))),
                                cancel=self.cancel)
            self.events.put(('status', f'Connecting to {dest.label}…'))
            self.events.put(('log', dest.connect()))
            ok, bad = importer.run(links, files)
            summary = f'Finished: {ok} export(s) imported' + (f', {bad} with problems — see log.' if bad else '.')
            self.events.put(('log', '\n' + summary + f'\nDownloaded/extracted files are kept in: {importer.work_dir}'))
            self.events.put(('status', ('⚠ ' if bad else '✔ ') + summary))
        self._run_in_thread(job)

    def _stop(self):
        self.cancel.set()
        self.status.set('Stopping after the current file…')
        self.stop_btn.configure(state='disabled')

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno('Import running', 'An import is running. Stop it and quit?\n'
                                       'You can resume later; finished files are remembered.'):
                return
            self.cancel.set()
        save_settings(self._settings())
        self.root.destroy()

    # ------------------------------------------------------------------ worker → UI

    def _log_threadsafe(self, msg: str):
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
                    self.status.set('✖ ' + val)
                elif kind == 'progress':
                    phase, done, total = val
                    names = {'download': 'Downloading', 'extract': 'Extracting', 'upload': 'Sending',
                             'archive': 'Exports'}
                    if phase == 'download':
                        text = f'{names[phase]} {human_size(done)}' + (f' of {human_size(total)}' if total else '')
                    else:
                        text = f'{names.get(phase, phase)} {done} of {total}'
                    if phase != 'archive':
                        self.phase.set(text)
                        self.bar['value'] = 1000 * done / total if total else 0
                elif kind == 'done':
                    self._busy(False)
                    self.bar['value'] = 0 if 'Stopped' in self.status.get() else self.bar['value']
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

    def report(exc, val, tb):  # with pythonw there is no console, so show errors in a dialog
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
