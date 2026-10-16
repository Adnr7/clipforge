#!/usr/bin/env python3
"""Install native static FFmpeg tools into user space for WSL.

This never invokes dpkg or writes to a system directory.  The two archives
are downloaded into a temporary directory, verified, unpacked into a versioned
directory, and checked before they become visible to ClipForge's runtime
discovery in ``backend.config``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
from urllib.request import Request, urlopen


# The newer b6.1.1 asset is actually FFmpeg 7.0.2 without drawtext. Pin the
# verified 6.0 build and the native FFprobe N-66595 build used for probing.
VERSION = '6.0'
FFMPEG_ARCHIVE_URL = (
    'https://github.com/eugeneware/ffmpeg-static/releases/download/'
    'b6.0/ffmpeg-linux-x64.gz'
)
FFMPEG_ARCHIVE_SHA256 = '17c1ae10b52ac499180679fe6ba77e17642390c4eedb0f1e3b0ac045da55128f'
FFPROBE_ARCHIVE_URL = (
    'https://registry.npmjs.org/@ffprobe-installer/linux-x64/-/'
    'linux-x64-5.2.0.tgz'
)
FFPROBE_ARCHIVE_SHA256 = '8b6b2e34bad5ea7cfbe1914185145f514f620f24203a1a421e8d00a698bf8e61'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={'User-Agent': 'ClipForge/ffmpeg-runtime'})
    with urlopen(request, timeout=60) as response, destination.open('wb') as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def _download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    last_error = None
    for _ in range(3):
        try:
            _download(url, destination)
            if _sha256(destination) != expected_sha256:
                raise RuntimeError(f'Checksum mismatch for {url}')
            return
        except Exception as exc:  # Retry transient WSL/network failures.
            last_error = exc
            destination.unlink(missing_ok=True)
    raise RuntimeError(f'Could not download {url}: {last_error}') from last_error


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract_ffmpeg(archive: Path, destination: Path) -> None:
    with gzip.open(archive, 'rb') as source, destination.open('wb') as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)
    _make_executable(destination)


def _extract_ffprobe(archive: Path, destination: Path) -> None:
    member_name = 'package/ffprobe'
    with tarfile.open(archive, 'r:gz') as package:
        member = package.getmember(member_name)
        if not member.isfile():
            raise RuntimeError(f'{member_name} is not a regular file')
        source = package.extractfile(member)
        if source is None:
            raise RuntimeError(f'Could not read {member_name}')
        with source, destination.open('wb') as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
    _make_executable(destination)


def _works(path: Path) -> bool:
    try:
        result = subprocess.run(
            [str(path), '-version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0 or not result.stdout.startswith(f'{path.name} version '):
        return False
    if path.name != 'ffmpeg':
        return True
    try:
        filters = subprocess.run(
            [str(path), '-hide_banner', '-filters'],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        encoders = subprocess.run(
            [str(path), '-hide_banner', '-encoders'],
            capture_output=True,
            text=True,
            timeout=10,
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
    ffmpeg = bin_dir / 'ffmpeg'
    ffprobe = bin_dir / 'ffprobe'
    if _works(ffmpeg) and _works(ffprobe):
        return install_dir

    base_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'.{VERSION}.', dir=base_dir) as temporary:
        staging = Path(temporary)
        ffmpeg_archive = staging / 'ffmpeg.gz'
        ffprobe_archive = staging / 'ffprobe.tgz'
        _download_verified(FFMPEG_ARCHIVE_URL, ffmpeg_archive, FFMPEG_ARCHIVE_SHA256)
        _download_verified(FFPROBE_ARCHIVE_URL, ffprobe_archive, FFPROBE_ARCHIVE_SHA256)

        staged_bin = staging / 'bin'
        staged_bin.mkdir()
        _extract_ffmpeg(ffmpeg_archive, staged_bin / 'ffmpeg')
        _extract_ffprobe(ffprobe_archive, staged_bin / 'ffprobe')
        if not (_works(staged_bin / 'ffmpeg') and _works(staged_bin / 'ffprobe')):
            raise RuntimeError('Downloaded FFmpeg tools failed their startup check')

        # Publish only the files we own; preserve any other files in the prefix.
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ('ffmpeg', 'ffprobe'):
            os.replace(staged_bin / name, bin_dir / name)
    return install_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--prefix',
        type=Path,
        default=Path.home() / '.local' / 'share' / 'clipforge' / 'ffmpeg',
        help='user-space install base (the version directory is added automatically)',
    )
    args = parser.parse_args()
    if os.name == 'nt' or os.uname().machine not in ('x86_64', 'amd64'):
        parser.error('this setup helper currently supports x86_64 Linux/WSL only')
    install_dir = install(args.prefix)
    print(f'Installed FFmpeg and FFprobe under {install_dir / "bin"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
