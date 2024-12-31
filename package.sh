#!/bin/zsh
#dos2unix entitlements.plist
source venv/bin/activate
rm -rf dist
rm -rf build

pyinstaller --noconfirm --clean  -i icons/SpotifyDownloader.icns --windowed SpotifyDownloader.py

deactivate