SNAPCHAT MEMORIES TO IMMICH - WINDOWS AND MAC

1. Extract the ZIP to a folder.
2. Windows: double-click START-WINDOWS.bat.
   Mac: open Terminal in the extracted folder, run: bash START-MAC.command
3. Enter the URL of YOUR Immich server (must be accessible from your computer).
4. Create an API key in your Immich account with upload and album permissions.
5. Enter your key; it will not be displayed.
6. Paste the Snapchat signed ZIP links one at a time (raw or Markdown-style).
7. Press Enter on a blank line when finished.

Prerequisites: Python 3 and internet access. The launcher uses pip to install
requests and requests-toolbelt in a dedicated local .venv folder.

Signed Snapchat URLs expire. Copy fresh links from Snapchat if necessary.

This is a local importer: Snapchat ZIPs are downloaded to your computer, then
uploaded to the Immich server. You need enough free space for ZIP + extracted
files. The ZIPs are retained in ~/Snapchat-Immich-Import for verification.
Original capture dates and GPS stored only in Snapchat JSON metadata may not
be restored. Files with embedded EXIF can retain their dates in Immich.

Only paste your own Snapchat links and Immich key into this local program.
Don't send keys/links to friends or post them publicly. Use HTTPS for remote
servers. This script has not been end-to-end tested against your Immich server.
