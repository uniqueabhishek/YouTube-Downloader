"""Download presets: what each dropdown choice asks yt-dlp for.

Default is AV1 video + Opus audio, taken as YouTube serves them (no
re-encode), so the file already has the best size-to-quality ratio.
The container is left to yt-dlp (usually .webm, .mkv if streams mix).
MP4 presets fetch H.264 + AAC for editors and older players.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# "Premium" streams (e.g. 616) carry a much higher bitrate for the same
# resolution, so they hurt the size ratio. "?" keeps formats with no note.
_NO_PREMIUM = "[format_note!*=?Premium]"


@dataclass(frozen=True)
class Preset:
    """One dropdown choice."""

    kind: str  # "av1", "mp4", "opus" or "mp3"
    max_height: int | None = None
    extra_opts: dict = field(default_factory=dict)


# Labels name the file extension a user will get; codecs stay in brackets.
PRESETS: dict[str, Preset] = {
    "Video 1080p  |  .webm  |  small file, best quality (AV1)": Preset("av1", 1080),
    "Video 720p  |  .webm  |  smallest file (AV1)": Preset("av1", 720),
    "Video up to 4K  |  .webm  |  highest quality (AV1)": Preset("av1"),
    "Video 1080p  |  .mp4  |  plays everywhere, for editing (H.264)": Preset(
        "mp4", 1080
    ),
    "Video 720p  |  .mp4  |  plays everywhere, for editing (H.264)": Preset(
        "mp4", 720
    ),
    "Audio only  |  .opus  |  best quality, smallest": Preset("opus"),
    "Audio only  |  .mp3  |  plays on any device": Preset("mp3"),
}
DEFAULT_PRESET = next(iter(PRESETS))


def _cap(max_height: int | None) -> str:
    return f"[height<={max_height}]" if max_height else ""


def build_format_opts(name: str) -> dict:
    """Return the yt-dlp options (format, postprocessors...) for a preset.

    Unknown names fall back to the default preset.
    """
    preset = PRESETS.get(name) or PRESETS[DEFAULT_PRESET]
    cap = _cap(preset.max_height)

    if preset.kind == "av1":
        # AV1 first; VP9 only when a video has no AV1; then anything.
        fmt = (
            f"bv[vcodec^=av01]{cap}{_NO_PREMIUM}+ba[acodec=opus]"
            f"/bv[vcodec~='^vp0?9']{cap}{_NO_PREMIUM}+ba[acodec=opus]"
            f"/bv{cap}+ba/b{cap}/b"
        )
        return {"format": fmt}

    if preset.kind == "mp4":
        # Stereo AAC (140, 128k): the 5.1 track (258) is 3x the size.
        fmt = (
            f"bv[vcodec^=avc1]{cap}+ba[acodec^=mp4a][audio_channels<=?2]"
            f"/bv[vcodec^=avc1]{cap}+ba[acodec^=mp4a]"
            f"/b[ext=mp4]{cap}/bv{cap}+ba/b"
        )
        return {"format": fmt, "merge_output_format": "mp4"}

    if preset.kind == "opus":
        # Source is already Opus, so ffmpeg only copies it into .opus.
        return {
            "format": "ba[acodec=opus]/ba/b",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "opus"}
            ],
        }

    return {
        "format": "ba/b",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }


def _size(f: dict, duration: float | None) -> float:
    size = f.get("filesize") or f.get("filesize_approx")
    if not size and duration and f.get("tbr"):
        size = duration * f["tbr"] * 1000 / 8  # kbps * sec -> bytes
    return float(size or 0)


def _pick_video(formats: list[dict], codec: str, max_height: int | None):
    candidates = [
        f
        for f in formats
        if (f.get("vcodec") or "none") != "none"
        and (f.get("acodec") or "none") == "none"
        and str(f.get("vcodec", "")).startswith(codec)
        and f.get("height")
        and (not max_height or f["height"] <= max_height)
        and "premium" not in str(f.get("format_note") or "").lower()
    ]
    return max(
        candidates, key=lambda f: (f["height"], f.get("tbr") or 0), default=None
    )


def _pick_audio(formats: list[dict], codec: str):
    candidates = [
        f
        for f in formats
        if (f.get("vcodec") or "none") == "none"
        and str(f.get("acodec", "")).startswith(codec)
        and (f.get("audio_channels") or 2) <= 2
    ]
    return max(candidates, key=lambda f: f.get("abr") or 0, default=None)


def estimate_size_mb(
    name: str, formats: list[dict], duration: float | None
) -> float:
    """Approximate download size in MB for a preset (0 if unknown)."""
    preset = PRESETS.get(name)
    if not preset or not formats:
        return 0

    if preset.kind == "mp3":
        # Re-encoded at 192 kbps, so size depends only on duration.
        return round((duration or 0) * 192 * 1000 / 8 / (1024 * 1024), 1)
    if preset.kind == "opus":
        audio = _pick_audio(formats, "opus") or _pick_audio(formats, "")
        video = None
    elif preset.kind == "av1":
        video = _pick_video(formats, "av01", preset.max_height) or _pick_video(
            formats, "vp", preset.max_height
        )
        audio = _pick_audio(formats, "opus")
    else:
        video = _pick_video(formats, "avc1", preset.max_height)
        audio = _pick_audio(formats, "mp4a")

    total = sum(_size(f, duration) for f in (video, audio) if f)
    return round(total / (1024 * 1024), 1)
