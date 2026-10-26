"""Background thread for updating yt-dlp to the latest version."""
import os
import shutil
import subprocess
from importlib.metadata import version

from PyQt5.QtCore import QThread, pyqtSignal  # type: ignore

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGES = ["yt-dlp", "yt-dlp-ejs"]


class UpdateThread(QThread):
    """Thread to upgrade yt-dlp (and its YouTube helper) without blocking UI."""

    # Signal emitted when done: (success, old_version, new_version, error_message)
    update_finished = pyqtSignal(bool, str, str, str)

    def run(self):
        """Upgrade the locked packages, then sync the environment."""
        old_version = version("yt-dlp")
        uv = shutil.which("uv")
        if not uv:
            self.update_finished.emit(False, old_version, "", "uv was not found")
            return

        upgrade_args = [arg for pkg in PACKAGES for arg in ("--upgrade-package", pkg)]
        commands = [[uv, "lock", *upgrade_args, "--quiet"], [uv, "sync", "--quiet"]]
        try:
            for cmd in commands:
                subprocess.run(
                    cmd,
                    cwd=PROJECT_DIR,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except subprocess.CalledProcessError as e:
            error = (e.stderr or e.stdout or str(e)).strip()
            self.update_finished.emit(False, old_version, "", error)
            return
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.update_finished.emit(False, old_version, "", str(e))
            return

        self.update_finished.emit(True, old_version, version("yt-dlp"), "")
