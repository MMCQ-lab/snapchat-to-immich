# Snapchat Memories → Immich

Import your exported Snapchat Memories ZIP files into your own [Immich](https://immich.app/) photo library. Works on **Windows** and **macOS**, without Docker or command-line knowledge beyond launching the installer.

> **Community utility — not affiliated with Snapchat or Immich.** This project is a starting point and has **not** been validated end-to-end against every Snapchat export or Immich version.

## Quick start

### Windows

1. Download this project: **Code → Download ZIP**, then extract the ZIP.
2. Install [Python 3](https://www.python.org/downloads/windows/) if you do not already have it.
3. Double-click **`START-WINDOWS.bat`** in the extracted folder.

### macOS

1. Download this project: **Code → Download ZIP**, then extract the ZIP.
2. Install [Python 3](https://www.python.org/downloads/macos/) if necessary.
3. Open Terminal in the extracted directory and run:

   ```bash
   bash START-MAC.command
   ```

The launchers create a local Python virtual environment, install dependencies, and launch the importer.

## What to prepare

- Your **own** Immich server URL, reachable from your computer (prefer HTTPS).
- An **Immich API key** created in your Immich account. Give it permissions to upload assets and manage albums; you can revoke it after the import.
- Signed ZIP download links from **Snapchat → My Data → Exports**.
- Free disk space for ZIPs **and** extracted media; ZIPs are downloaded **to your computer**, then uploaded to Immich.

At the prompts, enter your Immich URL and API key, then paste **one Snapchat download link at a time**. Both raw URLs and Markdown-formatted links are supported. Press Enter on an empty line when finished.

## Features

- Cleans common copy/paste formatting in signed Snapchat ZIP URLs.
- Warns about expired download URLs.
- Downloads and validates ZIP files, extracting supported photos/videos.
- Uploads media to Immich and creates/reuses an album named **Snapchat Memories**.
- Records completed uploads locally for resuming interrupted sessions.
- Keeps downloaded archives and extracted files until **you** decide to delete them.

## Important limitations

- **Snapchat export types vary.** This version expects ZIP files containing photos/videos. An export containing only HTML/JSON links will not import the linked media automatically.
- **Original capture dates and GPS** stored *only* in Snapchat's JSON metadata are **not** applied. Immich may show ZIP file timestamps instead; embedded EXIF metadata can still help.
- **Restart behavior:** It resumes by the archive's URL path. Copy a fresh signed link to the same archive path when its temporary signature expires. Importing the same media through different archive identities can still create duplicates.
- A working network connection is required to Snapchat storage and to your Immich instance.
- The app is a console utility, not a native Windows/macOS graphical installer.

## Security and privacy

- **Never post signed Snapchat download URLs or Immich API keys** on GitHub, in screenshots, issues, or chat. Anyone with a live signed URL may be able to fetch that archive.
- The script requests your API key using a hidden prompt. It does **not** write your API key to its journal.
- Use HTTPS for Immich over the public internet. HTTP exposes the API key to anyone able to intercept the connection; the script warns before proceeding.
- Personal imports, archives, and virtual environments are intentionally excluded by `.gitignore`.
- Review this script before executing, as with any community tool.

## Where are the downloaded files?

`~/Snapchat-Immich-Import/` on Windows (under your user directory) or macOS. The importer does not delete files automatically. Verify your Immich album and backup before clearing this folder.

## Manual run for developers

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python importer.py
```

On Windows, activate with `.venv\Scripts\activate` instead.

## Troubleshooting

- **HTTP 400 or 403 while downloading:** Refresh/copy a new Snapchat signed ZIP link; old links expire. Ensure you copied the complete URL including its query string.
- **Cannot reach Immich:** Check the server address and whether it is accessible from your computer (e.g. VPN/Tailscale may be required).
- **Immich 401/403:** Create a new API key with asset upload and album permissions.
- **Wrong dates:** This importer does not reconstruct Snapchat-only JSON timestamps.
- **Import interrupted:** Re-run the launcher and re-enter the same archive link; the local journal skips previously successful uploads.

## License

MIT — see [LICENSE](LICENSE).
