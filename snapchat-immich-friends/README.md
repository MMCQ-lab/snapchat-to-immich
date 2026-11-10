# Snapchat Memories → Immich, Google Photos, iCloud, or any cloud

A small desktop app for **Windows** and **macOS** that takes your exported Snapchat Memories and puts them in the photo library you choose:

| Destination | How it works |
|---|---|
| **Immich** (self-hosted) | Uploads through the Immich API (v1.1xx, v2 and v3) into an album. |
| **Google Photos** | Uploads through Google's Photos Library API into an album. Needs a one-time setup (below). |
| **Apple iCloud Photos** | **Mac:** imports into the Photos app, which uploads to iCloud. **Windows:** copies into the iCloud for Windows upload folder. |
| **Any other cloud / folder** | Copies into any folder, sorted by year: OneDrive, Dropbox, Google Drive for desktop, Nextcloud, Synology/NAS, an external drive, and so on. |

> **Community utility — not affiliated with Snapchat, Immich, Google or Apple.** Keep your original Snapchat ZIPs until you've checked the result.

## Quick start

1. Download this project: **Code → Download ZIP**, then extract it.
2. Install **Python 3.9+** from [python.org](https://www.python.org/downloads/) if you don't have it.
   *Windows:* tick **"Add python.exe to PATH"** in the installer.
3. Start it:
   - **Windows:** double-click **`START-WINDOWS.bat`**
   - **Mac:** double-click **`START-MAC.command`** (first time: see [macOS: first launch](#macos-first-launch))

The first start takes about a minute while it sets itself up. After that, the window opens:

1. **Send to:** pick a destination and fill in its settings. Click **Test connection**.
2. **Snapchat exports:** paste the download links from **Snapchat → Settings → My Data** (one per line), or click **Add ZIPs…** for exports you've already downloaded.
3. Click **Start import**. You can **Stop** at any time. Run it again later and it picks up where it stopped.

## Destination setup

### Immich
In Immich, go to **Account Settings → API Keys** and create a key with **asset upload** and **album read/write** permissions. The key is never saved to disk; you paste it each session. Use `https://` unless the server is on your home network or Tailscale.

### Google Photos (one-time, about 5 minutes)
Google only lets an app upload to your library if you sign in through an OAuth client registered in a Google Cloud project. It's free:

1. Open [console.cloud.google.com](https://console.cloud.google.com/), then create a project (any name).
2. **APIs & Services → Library**: search for **Photos Library API** and click **Enable**.
3. **APIs & Services → OAuth consent screen** (also called *Google Auth Platform*): choose **External**, fill in the app name and your email, and add **your own Google account as a Test user**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**, application type **Desktop app**. Download the JSON file.
5. In the app, choose **Google Photos**, click **Browse…**, and pick that JSON file. Then click **Test connection**. Your browser opens, and you sign in and allow access. (Google will warn that the app is unverified. That's expected, because it's your own app: click *Continue*.)

The app asks only for permission to **add** photos and to manage albums it creates. It can't see or delete the rest of your library. Sign-in lasts until you close the app.

### Apple iCloud Photos
- **Mac:** make sure **Photos → Settings → iCloud → iCloud Photos** is on. The first time, macOS asks to let Terminal/Python control Photos. Click **OK**. If you clicked *Don't Allow*, re-enable it in **System Settings → Privacy & Security → Automation**.
- **Windows:** install **iCloud for Windows** (Microsoft Store), sign in, and turn on **Photos**. The app finds the iCloud Photos folder automatically, or you can browse to it (usually `Pictures\iCloud Photos\Uploads`). The files upload in the background, so keep iCloud running until it finishes.

Apple doesn't offer an upload API, so these are the supported routes.

### Any other cloud / folder
Pick a folder that your cloud app syncs (for example `OneDrive\Pictures`, `Dropbox`, or `Google Drive\My Drive`). Files land in `<folder>/<album name>/<year>/`.

## What it fixes along the way
- **Dates:** Snapchat names memories like `2021-07-04_<id>-main.jpg`. That date is used as the capture date. Immich gets the date directly. For other destinations, it's written into the photo's EXIF and the file time, so Google/Apple/your cloud sort it correctly.
- **Caption overlays:** Snapchat exports captions and stickers as separate transparent `-overlay.png` files, which show up as blank or black images. They're skipped by default (you can untick this).
- **Windows-safe file names:** names with characters Windows forbids, or names that are too long, are cleaned up.
- **Resume:** each export keeps a small record of what was already sent, so re-running doesn't duplicate anything.

## Limitations
- GPS locations and exact times stored *only* in Snapchat's `memories_history.json` aren't applied. Videos get file-time dates but no embedded date.
- Some Snapchat exports contain only an HTML page with links, not the media. For those, request the **Memories** export as ZIPs.
- Google Photos uploads count against your Google storage.
- Signed Snapchat links expire after a few days. Copy fresh ones if downloads fail with HTTP 400/403.

## macOS: first launch
macOS blocks scripts downloaded from the internet the first time:
- Double-click `START-MAC.command`. If you get *"cannot be opened"*, go to **System Settings → Privacy & Security**, scroll down, and click **Open Anyway**. (On older macOS: right-click the file → **Open**.)
- Or open **Terminal** in the folder and run `bash START-MAC.command`.

For the window you need Python from **python.org** (recommended) or Homebrew with `brew install python-tk`. Apple's built-in Python 3.9 has a broken Tk, so the launcher automatically picks a better Python if one is installed, or otherwise falls back to a text-only version.

## Where are the files?
Downloads and extracted media are kept in `Snapchat-Immich-Import` in your home folder (changeable in the app under *Working folder*). Nothing is deleted automatically. Check your library, then delete the folder yourself.

## Security and privacy
- Everything runs on your computer. Nothing is sent anywhere except Snapchat (download) and the destination you choose.
- **Never share** Snapchat download links, Immich API keys, or your Google client JSON. Anyone with them can access your data.
- Saved settings (server address, folders, choices) live in `%APPDATA%\SnapchatImmich` on Windows and `~/Library/Application Support/SnapchatImmich` on Mac. API keys and Google sign-ins are **not** saved.

## Troubleshooting
| Problem | Fix |
|---|---|
| HTTP 400/403 while downloading | The Snapchat link expired. Copy a fresh one. |
| Can't reach Immich | Check the address and that this computer can reach it (VPN/Tailscale). |
| Immich 401/403 | Create a new API key with upload and album permissions. |
| Google "access blocked" / 403 | Add your account as a **Test user** on the consent screen, and make sure the Photos Library API is enabled. |
| Mac: "not authorized to send Apple events" | System Settings → Privacy & Security → Automation → allow Terminal/Python to control Photos. |
| Windows: window doesn't open | Re-run the Python installer → *Modify* → tick **tcl/tk and IDLE**. The text version runs meanwhile. |
| Import interrupted | Start again with the same links/ZIPs. Finished files are skipped. |

## For developers
```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest
python importer.py          # window
python importer.py --cli    # text mode
python -m pytest tests      # tests use fake Immich/Google servers
```
`snapimmich_core.py` handles download, extraction and resume. `destinations.py` holds the four destinations, and `gui.py` is the Tkinter window. CI runs the tests and launchers on Windows, macOS and Linux.

## License
MIT — see [LICENSE](LICENSE).
