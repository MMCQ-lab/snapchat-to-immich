# PyInstaller recipe for the double-click apps:  pyinstaller app.spec
# Windows -> dist/Snapchat Memories.exe     macOS -> dist/Snapchat Memories.app
import os
import sys

sys.path.insert(0, os.getcwd())
from snapimmich_core import VERSION  # noqa: E402

NAME = 'Snapchat Memories'
datas = [('google_client.json', '.')] if os.path.exists('google_client.json') else []

a = Analysis(['importer.py'], pathex=['.'], datas=datas,
             hiddenimports=['gui', 'destinations', 'snapimmich_core', 'piexif'],
             excludes=['pytest', 'PIL', 'numpy'])
pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
    app = BUNDLE(coll, name=f'{NAME}.app', bundle_identifier='com.mmcqlab.snapchat-memories',
                 version=VERSION, info_plist={
                     'CFBundleShortVersionString': VERSION,
                     'NSHighResolutionCapable': True,
                     # Required, or macOS silently blocks the app from adding photos to the Photos app.
                     'NSAppleEventsUsageDescription':
                         'Snapchat Memories adds your memories to the Photos app so they upload to iCloud.',
                 })
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, console=False, upx=False)
