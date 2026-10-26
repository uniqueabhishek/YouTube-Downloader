"""Background thread for fetching video metadata."""
import yt_dlp
from PyQt5.QtCore import QThread, pyqtSignal  # type: ignore


class TitleFetchThread(QThread):
    """Thread to fetch video title without blocking UI."""

    # Signal emitted when title is fetched: (url, title)
    title_fetched = pyqtSignal(str, str)
    # Signal emitted on error: (url, error_message)
    fetch_failed = pyqtSignal(str, str)

    def __init__(self, url: str):
        """Initialize with URL to fetch.

        Args:
            url: YouTube URL to fetch metadata for
        """
        super().__init__()
        self.url = url

    def run(self):
        """Fetch video title in background."""
        try:
            ydl_opts = {
                "quiet": True,
                "skip_download": True,
                "no_warnings": True,
                "noplaylist": True,  # watch?v=...&list=... means this video only
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore[arg-type]
                info = ydl.extract_info(self.url, download=False)
                if info:
                    title = info.get("title", "Unknown Title")
                    self.title_fetched.emit(self.url, title)
                else:
                    self.fetch_failed.emit(self.url, "No video info found")

        except Exception as e:  # pylint: disable=broad-exception-caught
            self.fetch_failed.emit(self.url, str(e))
