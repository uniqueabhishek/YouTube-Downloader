"""Background thread that updates the app's libraries and its FFmpeg build.

1. Back up uv.lock, upgrade every package within pyproject.toml's limits
   (PyQt5 stays pinned), sync, then import-test the result in a fresh
   process. Any failure restores the backup, so a bad release never
   leaves the app unable to start.
2. static-ffmpeg downloads FFmpeg once and never refreshes it (it only
   checks for an ``installed.crumb`` file). When the package version
   changes, its old binaries are deleted and the new build is fetched.
"""
import os
import shutil
import subprocess

from PyQt5.QtCore import QThread, pyqtSignal  # type: ignore

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(PROJECT_DIR, "uv.lock")
# Packages whose versions are reported back to the user
REPORTED = ["yt-dlp", "yt-dlp-ejs", "static-ffmpeg", "requests", "certifi"]

# Run in the freshly synced environment, not in this (old) process
_SMOKE_TEST = (
    "import yt_dlp, static_ffmpeg.run, loguru, requests, PyQt5.QtWidgets"
)
_VERSIONS = (
    "from importlib.metadata import version as v, PackageNotFoundError\n"
    "for p in {pkgs!r}:\n"
    "    try: print(p, v(p))\n"
    "    except PackageNotFoundError: pass"
)
_FETCH_FFMPEG = (
    "import shutil, static_ffmpeg.run as r\n"
    "shutil.rmtree(r.get_platform_dir(), ignore_errors=True)\n"
    "exe, _ = r.get_or_fetch_platform_executables_else_raise()\n"
    "print(exe)"
)
_FFMPEG_VERSION = (
    "import subprocess, static_ffmpeg.run as r\n"
    "exe, _ = r.get_or_fetch_platform_executables_else_raise()\n"
    "out = subprocess.run([exe, '-version'], capture_output=True, text=True)\n"
    "print(out.stdout.split()[2] if out.stdout else '?')"
)


class UpdateThread(QThread):
    """Upgrade libraries and FFmpeg without blocking the UI."""

    # (success, summary of what changed, error_message)
    update_finished = pyqtSignal(bool, str, str)
    status = pyqtSignal(str)

    def _run(self, *args: str, timeout: int = 300) -> str:
        result = subprocess.run(
            list(args),
            cwd=PROJECT_DIR,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "UV_LINK_MODE": "copy"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.strip()

    def _python(self, uv: str, code: str, timeout: int = 300) -> str:
        return self._run(uv, "run", "--no-sync", "python", "-c", code, timeout=timeout)

    def _versions(self, uv: str) -> dict[str, str]:
        out = self._python(uv, _VERSIONS.format(pkgs=REPORTED))
        return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)

    def run(self):
        """Upgrade, verify (or roll back), then refresh FFmpeg if needed."""
        uv = shutil.which("uv")
        if not uv:
            self.update_finished.emit(False, "", "uv was not found")
            return

        backup = LOCK_FILE + ".bak"
        try:
            before = self._versions(uv)
            old_ffmpeg = self._python(uv, _FFMPEG_VERSION)
            shutil.copy2(LOCK_FILE, backup)

            self.status.emit("🔄 Updating libraries...")
            try:
                self._run(uv, "lock", "--upgrade", "--quiet")
                self._run(uv, "sync", "--quiet")
                self._python(uv, _SMOKE_TEST)
            except Exception:
                # Put the last working set back before reporting the error
                shutil.copy2(backup, LOCK_FILE)
                self._run(uv, "sync", "--quiet")
                raise

            after = self._versions(uv)
            if before.get("static-ffmpeg") != after.get("static-ffmpeg"):
                self.status.emit("🔄 Downloading the new FFmpeg build...")
                self._python(uv, _FETCH_FFMPEG, timeout=900)
            new_ffmpeg = self._python(uv, _FFMPEG_VERSION)

        except subprocess.CalledProcessError as e:
            error = (e.stderr or e.stdout or str(e)).strip()
            self.update_finished.emit(False, "", error)
            return
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.update_finished.emit(False, "", str(e))
            return
        finally:
            if os.path.exists(backup):
                os.remove(backup)

        changes = [
            f"{pkg}: {before.get(pkg, '-')} → {new}"
            for pkg, new in after.items()
            if before.get(pkg) != new
        ]
        if old_ffmpeg != new_ffmpeg:
            changes.append(f"FFmpeg: {old_ffmpeg} → {new_ffmpeg}")
        self.update_finished.emit(True, "\n".join(changes), "")
