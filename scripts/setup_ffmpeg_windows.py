#!/usr/bin/env python3
"""Install verified native FFmpeg tools into a per-user Windows directory.

The installer never needs administrator permissions and never changes the
machine-wide PATH. It downloads pinned Windows x64 executables, verifies their
SHA-256 checksums, validates the ClipForge capabilities, and publishes them
under the user's local application data directory.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from urllib.request import Request, urlopen


VERSION = '6.0'
_RELEASE_BASE = 'https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0'
FFMPEG_ARCHIVE_URL = f'{_RELEASE_BASE}/ffmpeg-win32-x64.gz'
FFMPEG_ARCHIVE_SHA256 = '450d66226c79405c724e821f291cab0911e934bfa9fa2231adcab587f3e07b50'
FFPROBE_ARCHIVE_URL = f'{_RELEASE_BASE}/ffprobe-win32-x64.gz'
FFPROBE_ARCHIVE_SHA256 = '3f33adbad509f324e6f72449a5e8428fd655fb2b2eebf1d782c7ef23c44eed6a'


def _default_prefix() -> Path:
    local_app_data = os.environ.get('LOCALAPPDATA')
    if local_app_data:
        return Path(local_app_data) / 'ClipForge' / 'ffmpeg'
    return Path.home() / 'AppData' / 'Local' / 'ClipForge' / 'ffmpeg'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={'User-Agent': 'ClipForge/ffmpeg-runtime'})
    with urlopen(request, timeout=120) as response, destination.open('wb') as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def _download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    last_error = None
    for _ in range(3):
        try:
            _download(url, destination)
            if _sha256(destination) != expected_sha256:
                raise RuntimeError(f'Checksum mismatch for {url}')
            return
        except Exception as exc:  # Retry transient Windows/network failures.
            last_error = exc
            destination.unlink(missing_ok=True)
    raise RuntimeError(f'Could not download {url}: {last_error}') from last_error


def _extract_gzip(archive: Path, destination: Path) -> None:
    with gzip.open(archive, 'rb') as source, destination.open('wb') as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)


def _works(path: Path) -> bool:
    try:
        result = subprocess.run(
            [str(path), '-version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    name = path.stem.casefold()
    if result.returncode != 0 or not result.stdout.casefold().startswith(f'{name} version '):
        return False
    if name != 'ffmpeg':
        return True
    try:
        filters = subprocess.run(
            [str(path), '-hide_banner', '-filters'],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        encoders = subprocess.run(
            [str(path), '-hide_banner', '-encoders'],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        filters.returncode == 0
        and 'drawtext' in filters.stdout
        and encoders.returncode == 0
        and 'libx264' in encoders.stdout
    )


def install(base_dir: Path) -> Path:
    """Install and return the versioned directory containing ``bin``."""
    base_dir = base_dir.expanduser().resolve()
    install_dir = base_dir / VERSION
    bin_dir = install_dir / 'bin'
    ffmpeg = bin_dir / 'ffmpeg.exe'
    ffprobe = bin_dir / 'ffprobe.exe'
    if _works(ffmpeg) and _works(ffprobe):
        return install_dir

    base_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'.{VERSION}.', dir=base_dir) as temporary:
        staging = Path(temporary)
        ffmpeg_archive = staging / 'ffmpeg.exe.gz'
        ffprobe_archive = staging / 'ffprobe.exe.gz'
        _download_verified(FFMPEG_ARCHIVE_URL, ffmpeg_archive, FFMPEG_ARCHIVE_SHA256)
        _download_verified(FFPROBE_ARCHIVE_URL, ffprobe_archive, FFPROBE_ARCHIVE_SHA256)

        staged_bin = staging / 'bin'
        staged_bin.mkdir()
        _extract_gzip(ffmpeg_archive, staged_bin / 'ffmpeg.exe')
        _extract_gzip(ffprobe_archive, staged_bin / 'ffprobe.exe')
        if not (_works(staged_bin / 'ffmpeg.exe') and _works(staged_bin / 'ffprobe.exe')):
            raise RuntimeError('Downloaded Windows FFmpeg tools failed their capability check')

        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ('ffmpeg.exe', 'ffprobe.exe'):
            os.replace(staged_bin / name, bin_dir / name)
    return install_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--prefix',
        type=Path,
        default=_default_prefix(),
        help='user-space install base (the version directory is added automatically)',
    )
    args = parser.parse_args()
    if platform.system().lower() != 'windows' or platform.machine().lower() not in {'x86_64', 'amd64'}:
        parser.error('this setup helper currently supports native x86_64 Windows only')
    install_dir = install(args.prefix)
    print(f'Installed FFmpeg and FFprobe under {install_dir / "bin"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
