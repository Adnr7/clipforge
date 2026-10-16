import os
import subprocess

import pytest

from backend import config


pytestmark = pytest.mark.skipif(os.name == 'nt', reason='Linux/WSL runtime discovery')


def _fake_tool(path, *, capabilities='drawtext libx264', version_name=None, preamble=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '#!/bin/sh\n' + preamble + '\n'
        f'if [ "$1" = "-version" ]; then printf "{version_name or path.name} version test\\n"; '
        f'else printf "{capabilities}\\n"; fi\n'
    )
    path.chmod(0o755)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setenv('PATH', '/path-that-does-not-exist')
    monkeypatch.delenv('LD_LIBRARY_PATH', raising=False)


def test_runtime_exposes_both_native_tools_to_literal_subprocess_calls(tmp_path, monkeypatch):
    _fake_tool(tmp_path / 'ffmpeg')
    _fake_tool(tmp_path / 'ffprobe')
    monkeypatch.setattr(config, '_candidate_ffmpeg_bin_dirs', lambda: [tmp_path])

    assert config.configure_ffmpeg_runtime()
    assert config.Config.has_ffmpeg()
    assert config.Config.has_ffprobe()
    assert subprocess.run(['ffmpeg', '-version'], check=False).returncode == 0
    assert subprocess.run(['ffprobe', '-version'], check=False).returncode == 0
    assert os.environ['PATH'].split(os.pathsep)[0] == str(tmp_path)


@pytest.mark.parametrize('failure', [
    'missing-probe', 'loader-error', 'no-drawtext', 'no-x264', 'wrong-probe',
    'windows-alias', 'windows-header',
])
def test_rejects_incomplete_or_incompatible_tools_and_uses_working_fallback(
        tmp_path, monkeypatch, failure):
    bad, good = tmp_path / 'bad', tmp_path / 'native tools'
    for directory in (bad, good):
        _fake_tool(directory / 'ffmpeg')
        _fake_tool(directory / 'ffprobe')
    if failure == 'missing-probe':
        (bad / 'ffprobe').unlink()
    elif failure == 'loader-error':
        _fake_tool(bad / 'ffmpeg', preamble='exit 127')
    elif failure == 'no-drawtext':
        _fake_tool(bad / 'ffmpeg', capabilities='libx264')
    elif failure == 'no-x264':
        _fake_tool(bad / 'ffmpeg', capabilities='drawtext')
    elif failure == 'wrong-probe':
        _fake_tool(bad / 'ffprobe', version_name='ffmpeg')
    elif failure == 'windows-alias':
        (bad / 'ffmpeg').rename(bad / 'ffmpeg.exe')
        (bad / 'ffmpeg').symlink_to('ffmpeg.exe')
    else:
        (bad / 'ffmpeg').write_bytes(b'MZ\x00\x00')

    monkeypatch.setattr(config, '_candidate_ffmpeg_bin_dirs', lambda: [bad])
    status = config.Config.get_env_status()
    assert not status['hasFfmpeg'] and not status['hasFfprobe']
    assert not config.configure_ffmpeg_runtime()
    assert os.environ['PATH'] == '/path-that-does-not-exist'

    monkeypatch.setattr(config, '_candidate_ffmpeg_bin_dirs', lambda: [bad, good])
    assert config.configure_ffmpeg_runtime()
    status = config.Config.get_env_status()
    assert status['hasFfmpeg'] and status['hasFfprobe']
    assert os.environ['PATH'].split(os.pathsep)[0] == str(good)


def test_private_apt_libraries_and_path_are_idempotent(tmp_path, monkeypatch):
    bin_dir = tmp_path / 'apt-root' / 'usr' / 'bin'
    lib_dir = bin_dir.parent / 'lib' / 'x86_64-linux-gnu'
    lib_dir.mkdir(parents=True)
    for name in ('ffmpeg', 'ffprobe'):
        _fake_tool(bin_dir / name, preamble=f'case "$LD_LIBRARY_PATH" in *"{lib_dir}"*) ;; *) exit 127;; esac')
    monkeypatch.setenv('LD_LIBRARY_PATH', '/existing/lib')

    assert config.configure_ffmpeg_runtime([bin_dir])
    first_path = os.environ['PATH']
    first_libraries = os.environ['LD_LIBRARY_PATH']
    assert '/existing/lib' in first_libraries.split(':')
    assert str(lib_dir) in first_libraries.split(':')
    assert config.configure_ffmpeg_runtime([bin_dir])
    assert os.environ['PATH'] == first_path
    assert os.environ['LD_LIBRARY_PATH'] == first_libraries
    assert subprocess.run(['ffprobe', '-version'], check=False).returncode == 0


def test_default_user_install_is_discovered_without_shell_path(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    bin_dir = home / '.local' / 'share' / 'clipforge' / 'ffmpeg' / '6.0' / 'bin'
    for name in ('ffmpeg', 'ffprobe'):
        _fake_tool(bin_dir / name)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(config, '_PROJECT_ROOT', tmp_path / 'project')
    monkeypatch.delenv('CLIPFORGE_FFMPEG_DIR', raising=False)

    assert config.configure_ffmpeg_runtime()
    assert os.environ['PATH'].split(os.pathsep)[0] == str(bin_dir)


def test_relative_override_stays_usable_after_changing_directory(tmp_path, monkeypatch):
    bin_dir = tmp_path / 'custom tools'
    for name in ('ffmpeg', 'ffprobe'):
        _fake_tool(bin_dir / name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CLIPFORGE_FFMPEG_DIR', 'custom tools')

    assert config.configure_ffmpeg_runtime()
    monkeypatch.chdir(tmp_path.parent)
    assert subprocess.run(['ffmpeg', '-version'], check=False).returncode == 0
