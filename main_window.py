"""YouTube Downloader main window module.

This module contains the YouTubeDownloader class which provides the main
GUI interface for downloading YouTube videos using PyQt5.
"""
import os
import re
import sys

import yt_dlp
from loguru import logger
from PyQt5.QtCore import QSettings, Qt, QTimer  # pylint: disable=no-name-in-module
from PyQt5.QtGui import QColor  # pylint: disable=no-name-in-module
from PyQt5.QtWidgets import (  # pylint: disable=no-name-in-module
    QAction,
    QApplication,
    QComboBox,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app_dir_creator import get_database_path, get_download_folder
from database_handler import DatabaseManager, init_db
from download_thread import DownloadThread
from ffmpeg_utils import get_ffmpeg_path as find_ffmpeg
from queue_item import QueueItem, QueueStatus
from queue_manager import QueueManager
from smart_paste_utils import UrlLineEdit
from theme import MAIN_STYLESHEET
from update_thread import UpdateThread

logger.add("downloader.log", rotation="500 KB", retention=5)


# class UrlLineEdit(QLineEdit, SmartPasteMixin):
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)


# ======================= Helper Functions =======================
def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal and remove dangerous characters.
    Removes: < > : " / \\ | ? * and control characters (0x00-0x1F)
    """
    # Remove path separators and dangerous characters
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip(". ")
    # Ensure filename is not empty
    if not sanitized:
        sanitized = "video"
    return sanitized


def validate_url(url: str) -> bool:
    """
    Validate if the URL is a valid YouTube URL.
    Returns True if valid, False otherwise.
    """
    # Basic URL pattern for YouTube
    youtube_pattern = (
        r"^https?://(www\.)?"
        r"(youtube\.com/(watch\?v=|shorts/|live/|embed/|v/)|youtu\.be/).+"
    )
    return bool(re.match(youtube_pattern, url, re.IGNORECASE))


# ======================= Main App =======================
class YouTubeDownloader(QWidget):
    """Main application window for the YouTube Downloader.

    Provides a PyQt5-based GUI with download queue management,
    format selection, and system tray integration.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube Downloader")
        self.setGeometry(400, 100, 800, 550)

        # Frameless window for custom title bar with rounded corners
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.drag_position = None

        # Apply Windows 7 Aero Blue Theme
        self.setStyleSheet(MAIN_STYLESHEET)

        # Initialize app environment
        self.output_folder = get_download_folder()
        self.db_path = get_database_path()
        init_db(self.db_path)

        # FFmpeg will be loaded lazily on first download (for faster startup)
        self.ffmpeg_path = None

        # ---------------------------------------------------

        # Queue manager handles all queue operations
        self.queue_manager: QueueManager | None = None  # Initialized in init_ui
        self.download_thread = None
        self.update_thread = None
        self.downloading = False

        self.settings = QSettings("YouTubeDownloader", "Settings")
        # self.output_folder = self.settings.value("output_folder", os.getcwd())

        # Only overwrite output_folder if user has previously saved a folder
        saved_folder = self.settings.value("output_folder")
        if saved_folder:
            self.output_folder = saved_folder

        # -------------video + audio codec number defined here
        # --------------------------Added the format mapping here
        self.format_map = {
            "Mp4-High (720p)": {"video": "136", "audio": "140"},
            "Mp4-HD (1080p)": {"video": "137", "audio": "251"},
            "Mkv-High (720p)": {"video": "247", "audio": "251"},
            "Mkv-HD (1080p)": {"video": "248", "audio": "251"},
            "WebM-High (720p)": {"video": "247", "audio": "251"},
            "WebM-HD (1080p)": {"video": "248", "audio": "251"},
            "Super High WebM": "bestvideo+bestaudio",
            "Audio Only (MP3)": "bestaudio",
        }
        # -------------------------------------------

        self.init_ui()
        self.init_tray()

    # ------------------Shows Dropwnlist format - size------------

    def fetch_format_sizes(self, url):
        """Fetch available format sizes for a given YouTube URL."""
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,  # watch?v=...&list=... means this video only
            "js_runtimes": {"node": {}},
            "extractor_args": {"youtube": {"player_client": ["default", "mweb"]}},
            "socket_timeout": 30,
        }
        formats_list = []

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore[arg-type]
                info = ydl.extract_info(url, download=False)
                if not info:
                    return []
                formats = info.get("formats", [])

                for f in formats:  # type: ignore[union-attr]
                    fmt_id = f["format_id"]
                    ext = f["ext"]
                    res = f.get("height") or "audio"

                    # Calculate approximate size
                    size = f.get("filesize") or f.get("filesize_approx")
                    if not size:
                        duration = f.get("duration")
                        tbr = f.get("tbr")  # total bitrate in kbps
                        if duration and tbr:
                            size = (
                                duration * tbr * 1000 / 8
                            )  # convert kbps * sec → bytes

                    # ensure numeric
                    size_mb = round(size / (1024 * 1024), 1) if size else 0

                    formats_list.append(
                        {
                            "format_id": fmt_id,
                            "ext": ext,
                            "resolution": res,
                            "size_mb": size_mb,
                        }
                    )
        except (yt_dlp.utils.DownloadError, KeyError, TypeError) as e:
            self.status_label.setText(f"Error fetching formats: {e}")

        return formats_list

    def update_format_dropdown(self):
        """Update the format dropdown with sizes for the current URL."""
        url = self.url_input.text().strip()
        if not url:
            return

        self.format_quality_combo.clear()
        formats_list = self.fetch_format_sizes(url)

        for display_name, codes in self.format_map.items():
            size_mb = 0

            if isinstance(codes, dict) and "video" in codes:
                match = next(
                    (
                        f
                        for f in formats_list
                        if str(f["format_id"]) == str(codes["video"])
                    ),
                    None,
                )
                if match:
                    size_mb = match["size_mb"]

            elif display_name == "Super High WebM":
                largest = max(
                    (f for f in formats_list if f.get("height")),
                    key=lambda x: x["size_mb"]
                    if isinstance(x["size_mb"], (int, float))
                    else 0,
                    default=None,
                )
                if largest:
                    size_mb = largest["size_mb"]

            elif display_name == "Audio Only (MP3)":
                best_audio = max(
                    (
                        f
                        for f in formats_list
                        if str(f["resolution"]).lower() == "audio"
                    ),
                    key=lambda x: x["size_mb"]
                    if isinstance(x["size_mb"], (int, float))
                    else 0,
                    default=None,
                )
                if best_audio:
                    size_mb = best_audio["size_mb"]

            # Show dropdown with size
            display_text = (
                f"{display_name} ~{size_mb} MB"
                if size_mb
                else f"{display_name}     ~Unknown"
            )
            self.format_quality_combo.addItem(display_text)

    # ----------------------- GUI Setup -----------------------
    def init_ui(self):
        """Initialize the user interface components."""
        # --------------Main vertical layout-----------------------
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---------------- Custom Title Bar ----------------
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(35)
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(5, 0, 0, 0)
        title_bar_layout.setSpacing(0)

        # Title label
        title_label = QLabel("🎬 YouTube Downloader")
        title_label.setObjectName("titleLabel")
        title_bar_layout.addWidget(title_label)

        title_bar_layout.addStretch()

        # Minimize button
        minimize_btn = QPushButton("─")
        minimize_btn.setObjectName("minimizeBtn")
        minimize_btn.clicked.connect(self.showMinimized)
        title_bar_layout.addWidget(minimize_btn)

        # Maximize button
        self.maximize_btn = QPushButton("☐")
        self.maximize_btn.setObjectName("maximizeBtn")
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        title_bar_layout.addWidget(self.maximize_btn)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        title_bar_layout.addWidget(close_btn)

        layout.addWidget(title_bar)

        # Content area with padding - styled to match title bar
        content = QWidget()
        content.setObjectName("contentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(10)

        # ---------------- URL Input ----------------
        url_content_layout = QHBoxLayout()
        url_content_layout.setSpacing(8)
        content_layout.setContentsMargins(10, 10, 10, 10)
        self.url_input = UrlLineEdit(self)
        self.url_input.setPlaceholderText("Enter YouTube URL Here")
        # Auto-fetch formats when URL is pasted
        self.url_input.url_pasted.connect(self.update_format_dropdown)
        url_content_layout.addWidget(self.url_input)

        paste_button = QPushButton("📋 Paste URL")
        paste_button.setObjectName("blueButton")
        paste_button.setFixedWidth(100)
        paste_button.setToolTip("Paste URL from clipboard")
        paste_button.clicked.connect(self._paste_clipboard)
        url_content_layout.addWidget(paste_button)
        content_layout.addLayout(url_content_layout)

        # ---------------- Output Folder ----------------
        folder_content_layout = QHBoxLayout()
        folder_content_layout.setSpacing(8)
        self.saved_folder_label = QLabel(f"📁 {self.output_folder}")
        self.saved_folder_label.setToolTip("Current download location")
        folder_content_layout.addWidget(self.saved_folder_label)

        self.output_folder_btn = QPushButton("📂 Change Folder")
        self.output_folder_btn.setObjectName("orangeButton")
        self.output_folder_btn.setFixedWidth(140)
        self.output_folder_btn.setToolTip("Select download folder")
        self.output_folder_btn.clicked.connect(self.select_output_folder)
        folder_content_layout.addWidget(self.output_folder_btn)
        content_layout.addLayout(folder_content_layout)

        # ---------------- Format & Quality ----------------
        format_layout = QHBoxLayout()
        format_layout.setSpacing(8)

        self.format_quality_combo = QComboBox()
        self.format_quality_combo.addItem("🎬 Select Format")
        self.format_quality_combo.setToolTip("Choose video quality and format")
        self.format_quality_combo.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        format_layout.addWidget(self.format_quality_combo)

        content_layout.addLayout(format_layout)

        # ---------------- Queue Buttons ----------------
        queue_content_layout = QHBoxLayout()
        queue_content_layout.setSpacing(8)

        self.enqueue_button = QPushButton("➕ Add to Queue")
        self.enqueue_button.setObjectName("greenButton")
        self.enqueue_button.setToolTip("Add the URL to download queue")
        self.enqueue_button.clicked.connect(self.enqueue_download)

        self.download_button = QPushButton("▶ Start Download")
        self.download_button.setObjectName("greenButton")
        self.download_button.setToolTip("Start downloading all queued videos")
        self.download_button.clicked.connect(self.start_queue)

        self.cancel_button = QPushButton("⏹ Cancel")
        self.cancel_button.setObjectName("redButton")
        self.cancel_button.setToolTip("Cancel the current download")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_download)

        queue_content_layout.addWidget(self.enqueue_button)
        queue_content_layout.addWidget(self.download_button)
        queue_content_layout.addWidget(self.cancel_button)
        content_layout.addLayout(queue_content_layout)

        # ---------------- Queue List ----------------
        self.queue_list = QListWidget()
        self.queue_list.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Fix selection colors so text is visible when selected
        self.queue_list.setStyleSheet("""
            QListWidget::item {
                border-bottom: 1px solid #E0E0E0;
                padding: 2px;
            }
            QListWidget::item:selected {
                background-color: #E5F3FF;
                color: black;
                border: 1px solid #99D1FF;
            }
            QListWidget::item:selected:active {
                background-color: #E5F3FF;
                color: black;
            }
            QListWidget::item:selected:!active {
                background-color: #F0F8FF;
                color: black;
            }
            QListWidget::item:hover {
                background-color: #F5F5F5;
                color: black;
            }
        """)
        # Initialize queue manager
        self.queue_manager = QueueManager(self.queue_list, self)

        # Enable right-click context menu
        self.queue_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(
            self.queue_manager.show_context_menu)
        content_layout.addWidget(self.queue_list)

        # ---------------- Progress Bar ---------------------
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setMinimumHeight(22)
        self.progress_bar.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        content_layout.addWidget(self.progress_bar)

        # ---------------- Status Label + Update Button ----------------
        status_layout = QHBoxLayout()
        status_layout.setSpacing(8)
        self.status_label = QLabel("⏸ Ready")
        self.status_label.setContentsMargins(5, 5, 5, 5)
        self.status_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        status_layout.addWidget(self.status_label)

        self.update_button = QPushButton("🔄 Update Downloader")
        self.update_button.setObjectName("blueButton")
        self.update_button.setFixedWidth(170)
        self.update_button.setToolTip("Update yt-dlp to the latest version")
        self.update_button.clicked.connect(self.update_ytdlp)
        status_layout.addWidget(self.update_button)
        content_layout.addLayout(status_layout)

        # ---------------- Drop Shadow Effects ----------------
        def add_shadow(widget, color=QColor(0, 0, 0, 100), blur=15, offset=(0, 5)):
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(blur)
            shadow.setColor(color)
            shadow.setOffset(*offset)
            widget.setGraphicsEffect(shadow)

        add_shadow(self.enqueue_button)
        add_shadow(self.download_button)
        add_shadow(self.cancel_button)

        layout.addWidget(content)
        self.setLayout(layout)  # Finalize the layout

    def toggle_maximize(self):
        """Toggle between maximized and normal window state."""
        if self.isMaximized():
            self.showNormal()
            self.maximize_btn.setText("☐")
        else:
            self.showMaximized()
            self.maximize_btn.setText("❐")

    def mousePressEvent(self, event):  # pylint: disable=invalid-name
        """Handle mouse press for window dragging."""
        if event.button() == Qt.LeftButton and event.pos().y() < 35:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):  # pylint: disable=invalid-name
        """Handle mouse move for window dragging."""
        if event.buttons() == Qt.LeftButton and self.drag_position:
            self.move(event.globalPos() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, _event):  # pylint: disable=invalid-name
        """Handle mouse release to stop dragging."""
        self.drag_position = None

    def _paste_clipboard(self):
        self.url_input.smart_paste()

    # ----------------------- Tray -----------------------
    def init_tray(self):
        """Initialize the system tray icon and menu."""
        self.tray_icon = QSystemTrayIcon(self)
        style = self.style()
        if style:  # type: ignore[reportOptionalMemberAccess]
            self.tray_icon.setIcon(style.standardIcon(
                QStyle.SP_ComputerIcon))  # type: ignore[attr-defined]

        tray_menu = QMenu()
        restore_action = QAction("Restore", self)
        exit_action = QAction("Exit", self)
        restore_action.triggered.connect(
            self.showNormal)  # type: ignore[arg-type]
        exit_action.triggered.connect(self.close)  # type: ignore[arg-type]
        tray_menu.addAction(restore_action)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(
            self.toggle_visibility)  # type: ignore[arg-type]
        self.tray_icon.show()

    def toggle_visibility(self, reason):
        """Toggle window visibility based on tray icon activation."""
        if reason == QSystemTrayIcon.Trigger:  # type: ignore[attr-defined]
            self.setVisible(not self.isVisible())

    # type: ignore[override]  # pylint: disable=invalid-name
    def closeEvent(self, event):
        """Handle window close event, saving settings."""
        self.settings.setValue("output_folder", self.output_folder)
        self.tray_icon.hide()
        if event:
            event.accept()

    # ----------------------- yt-dlp Update -----------------------
    def update_ytdlp(self):
        """Upgrade yt-dlp in the background (same step as Start_YTD.bat)."""
        if self.downloading:
            QMessageBox.information(
                self, "Update", "Please wait until the current download finishes."
            )
            return
        self.update_button.setEnabled(False)
        self.status_label.setText("🔄 Checking for yt-dlp updates...")
        self.update_thread = UpdateThread()
        self.update_thread.update_finished.connect(self.update_finished)
        self.update_thread.start()

    def update_finished(self, success, old_version, new_version, error):
        """Report the result of a yt-dlp update."""
        self.update_button.setEnabled(True)
        if not success:
            logger.error(f"yt-dlp update failed: {error}")
            self.status_label.setText("❌ Update failed")
            QMessageBox.warning(
                self, "Update Failed", f"Could not update yt-dlp:\n{error}"
            )
            return
        if new_version == old_version:
            self.status_label.setText(f"✅ yt-dlp is up to date ({new_version})")
            return
        logger.info(f"yt-dlp updated {old_version} -> {new_version}")
        self.status_label.setText(f"✅ yt-dlp updated to {new_version}")
        QMessageBox.information(
            self,
            "Update Complete",
            f"yt-dlp updated from {old_version} to {new_version}.\n"
            "Please close and reopen the app to use the new version.",
        )

    # ----------------------- Folder Selection -----------------------
    def select_output_folder(self):
        """Open a dialog to select the output folder."""
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_folder = folder
            self.saved_folder_label.setText(f"📁 {self.output_folder}")

    # ----------------------- Queue -----------------------
    def enqueue_download(self):
        """Add the current URL to the download queue."""
        url = self.url_input.text().strip()
        if not url:
            return

        # Validate URL before adding to queue
        if not validate_url(url):
            QMessageBox.warning(
                self,
                "Invalid URL",
                "Please enter a valid YouTube URL.\n"
                "Supported formats:\n"
                "- https://youtube.com/watch?v=...\n"
                "- https://youtu.be/...\n"
                "- https://youtube.com/shorts/...\n"
                "- https://youtube.com/live/...",
            )
            return

        # Check for duplicates
        if self.queue_manager and self.queue_manager.has_duplicate(url):
            QMessageBox.warning(
                self,
                "Duplicate URL",
                "This URL is already in the queue.",
            )
            return

        # Get current format selection
        selected_format = self.format_quality_combo.currentText()
        if selected_format.startswith("🎬"):
            selected_format = ""

        # Create queue item
        queue_item = QueueItem(
            url=url,
            title="Fetching title...",
            format_selection=selected_format,
            status=QueueStatus.WAITING
        )

        # Add to queue via queue manager
        if self.queue_manager:
            self.queue_manager.add_item(queue_item)

        # Clear input
        self.url_input.clear()

    def start_queue(self):
        """Start downloading all items in the queue."""
        if not self.queue_manager or self.queue_manager.is_empty():
            QMessageBox.information(self, "Info", "No URLs in the queue.")
            return

        if not self.downloading:
            self.download_next()  # Start the first download
            self.cancel_button.setEnabled(True)

    def download_next(self):
        """Download the next URL in the queue."""
        if not self.queue_manager:
            return

        queue_item = self.queue_manager.pop_next()
        if not queue_item:
            self.status_label.setText("Status: All downloads complete.")
            self.downloading = False
            return

        url = queue_item.url
        self.status_label.setText(
            f"Status: Downloading {queue_item.title or url}")
        self.downloading = True

        # --- Use new combined dropdown ---
        selected_item = self.format_quality_combo.currentText()
        if "~" in selected_item:
            selected_item = selected_item.split(" ~")[0]  # remove size suffix

        # Lazy-load FFmpeg on first download (speeds up app startup)
        if not self.ffmpeg_path:
            self.ffmpeg_path = find_ffmpeg()

        if not self.ffmpeg_path:
            QMessageBox.warning(
                self,
                "FFmpeg Missing",
                "FFmpeg codec not found.\n"
                "Please click 'Update FFmpeg Codec' to download it.",
            )

        ydl_opts = {
            "outtmpl": os.path.join(self.output_folder, "%(title).200B.%(ext)s"),
            "restrictfilenames": True,  # Sanitize filenames to prevent path traversal
            "quiet": True,
            # progress_hooks still fire; this only stops progress lines in the log
            "noprogress": True,
            # <-- directory only
            "ffmpeg_location": (
                os.path.dirname(self.ffmpeg_path) if self.ffmpeg_path else None
            ),
            "logger": logger,
            "continuedl": True,
            "noplaylist": True,  # watch?v=...&list=... means this video only
            "retries": 10,
            "fragment_retries": 10,
            "max_retries": 3,
            "js_runtimes": {"node": {}},
            "extractor_args": {"youtube": {"player_client": ["default", "mweb"]}},
            "socket_timeout": 30,
        }

        # --- Map selection to yt-dlp format codes ---
        if selected_item == "Super High WebM":
            ydl_opts["format"] = "bestvideo+bestaudio/best"
        elif selected_item == "Audio Only (MP3)":
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        else:
            # Use quality-based selection instead of hardcoded format codes
            if "1080p" in selected_item:
                fmt = "bestvideo[height<=1080]+bestaudio"
                ydl_opts["format"] = f"{fmt}/best[height<=1080]"
            elif "720p" in selected_item:
                fmt = "bestvideo[height<=720]+bestaudio"
                ydl_opts["format"] = f"{fmt}/best[height<=720]"
            else:
                ydl_opts["format"] = "bestvideo+bestaudio/best"

        # Start download thread
        self.download_thread = DownloadThread(url, ydl_opts)
        self.download_thread.progress.connect(self.progress_bar.setValue)
        self.download_thread.status.connect(self.status_label.setText)
        self.download_thread.finished.connect(self.download_finished)
        self.download_thread.start()

    def download_thread_hook(self, d):
        """Hook for yt-dlp progress updates during download."""
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                progress = int(downloaded / total * 100)
                if self.download_thread:
                    self.download_thread.progress.emit(progress)

    def download_finished(self, _success, message, url, title, path, status):
        """Handle download completion and record to history."""
        self.downloading = False
        with DatabaseManager(self.db_path) as db:
            db.record_history(url, title, path, status)
        self.progress_bar.setValue(0)
        self.cancel_button.setEnabled(False)

        # Check if file already existed
        if "has already been downloaded" in message:
            self.status_label.setText("Status: File already downloaded")
        else:
            self.status_label.setText(f"Status: {message}")

        QTimer.singleShot(100, self.download_next)

    def cancel_download(self):
        """Cancel the currently running download."""
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.cancel()
            self.status_label.setText("Status: Cancel requested...")
            self.cancel_button.setEnabled(False)


# ----------------------- Main -----------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    window = YouTubeDownloader()
    window.show()
    sys.exit(app.exec_())
