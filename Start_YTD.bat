@echo off
title YouTube Downloader - Hi Tech Version
cd /d "%~dp0"
set UV_LINK_MODE=copy
echo Checking for yt-dlp updates...
uv lock --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs --quiet
if errorlevel 1 echo Update check failed (offline?) - starting with the current version.
echo Starting YouTube Downloader...
uv run python app.py
pause
