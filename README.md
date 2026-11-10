# Snapchat Memories mover

Move all your saved Snapchat Memories into **Google Photos**, **Apple iCloud Photos**, **OneDrive / Dropbox / any folder**, or **Immich**, with the right dates, in three clicks. Works on **Windows** and **Mac**. Nothing to install.

## ⬇️ Download

| Your computer | Download |
|---|---|
| **Windows** | [**Download for Windows**](https://github.com/MMCQ-lab/snapchat-to-immich/releases/latest/download/SnapchatMemories-Windows.exe) |
| **Mac with Apple chip** (most Macs from 2021 on) | [**Download for Mac (Apple chip)**](https://github.com/MMCQ-lab/snapchat-to-immich/releases/latest/download/SnapchatMemories-Mac-AppleSilicon.zip) |
| **Older Mac with Intel chip** | [**Download for Mac (Intel)**](https://github.com/MMCQ-lab/snapchat-to-immich/releases/latest/download/SnapchatMemories-Mac-Intel.zip) |

*Which Mac do I have?* Apple menu → **About This Mac**. "Chip: Apple M…" means Apple chip; "Processor: Intel" means Intel.

## How to use it

**Before you start:** ask Snapchat for your memories. Go to [accounts.snapchat.com](https://accounts.snapchat.com/accounts/downloadmydata) → **My Data** → turn on **Export your Memories** → **Request Only Memories**. Snapchat emails you when they're ready, which can take a few hours to a few days.

1. **Open the app.** The first time:
   - *Windows:* if you see "Windows protected your PC", click **More info → Run anyway**.
   - *Mac:* unzip it and drag it to **Applications**. If it "can't be opened", go to **System Settings → Privacy & Security → Open Anyway**.
2. **Choose where your memories should go:** Google Photos, iCloud Photos, a folder, or Immich.
3. **Add your memories:** on Snapchat's *See exports* page, right-click **Download** → **Copy link**, then click **Paste link** in the app. Or download the ZIP files and click **Add ZIP files**.
4. **Click "Start moving my memories."** The app first checks that you have enough storage space, then gets to work. You can pause and continue later.

Every step in the app has a **Help** button.

## Good to know
- **Nothing is deleted:** not from Snapchat, and not from your computer.
- **Dates are kept**, so memories show up on the day you took them, not the day you imported them.
- Snapchat's separate caption and sticker images are skipped, so you don't get blank photos.
- Your passwords and keys are never saved, and nothing is sent anywhere except Snapchat and the place you choose.

---

*For techy folks:* the source, a run-from-source option, and maintainer setup are in [`snapchat-immich-friends/README.md`](snapchat-immich-friends/README.md). MIT licensed. Not affiliated with Snapchat, Google, Apple or Immich.
