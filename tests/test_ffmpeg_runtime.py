import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

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


def test_ensure_media_runtime_bootstraps_supported_linux_without_shell_path(monkeypatch):
    monkeypatch.setattr(config, '_MEDIA_RUNTIME', {
        'state': 'not_checked', 'platform': '', 'source': None,
        'message': 'not checked',
    })
    monkeypatch.setattr(config.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(config.platform, 'machine', lambda: 'x86_64')
    monkeypatch.delenv('CLIPFORGE_FFMPEG_DIR', raising=False)
    active = Path.home() / '.local' / 'share' / 'clipforge' / 'ffmpeg' / '6.0' / 'bin'
    calls = []

    def configure():
        calls.append(True)
        if len(calls) == 1:
            return False
        config._ACTIVE_FFMPEG_DIR = active
        return True

    monkeypatch.setattr(config, 'configure_ffmpeg_runtime', configure)
    install = Mock()
    monkeypatch.setattr('scripts.setup_ffmpeg_wsl.install', install)

    result = config.ensure_media_runtime()

    assert result['state'] == 'ready'
    assert result['source'] == 'managed'
    install.assert_called_once()
    assert len(calls) == 2


def test_ensure_media_runtime_does_not_download_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(config, '_MEDIA_RUNTIME', {
        'state': 'not_checked', 'platform': '', 'source': None,
        'message': 'not checked',
    })
    monkeypatch.setattr(config.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(config.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(config, 'configure_ffmpeg_runtime', lambda: False)
    install = Mock(side_effect=AssertionError('Linux bootstrap must not run on macOS'))
    monkeypatch.setattr('scripts.setup_ffmpeg_wsl.install', install)

    result = config.ensure_media_runtime()

    assert result['state'] == 'unsupported'
    install.assert_not_called()


def test_ensure_media_runtime_reports_failed_bootstrap_without_blocking_startup(monkeypatch):
    monkeypatch.setattr(config, '_MEDIA_RUNTIME', {
        'state': 'not_checked', 'platform': '', 'source': None,
        'message': 'not checked',
    })
    monkeypatch.setattr(config.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(config.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(config, 'configure_ffmpeg_runtime', lambda: False)
    monkeypatch.delenv('CLIPFORGE_FFMPEG_DIR', raising=False)
    monkeypatch.setattr('scripts.setup_ffmpeg_wsl.install', Mock(side_effect=OSError('offline')))

    result = config.ensure_media_runtime()

    assert result['state'] == 'failed'
    assert 'restart ClipForge' in result['message']


def test_ytdlp_runtime_prefers_the_active_python_environment_script(tmp_path, monkeypatch):
    executable = tmp_path / 'venv' / 'bin' / 'python'
    executable.parent.mkdir(parents=True)
    executable.write_text('')
    ytdlp = executable.parent / 'yt-dlp'
    ytdlp.write_text('#!/bin/sh\n')
    ytdlp.chmod(0o755)
    monkeypatch.setattr(config.sys, 'executable', str(executable))
    monkeypatch.setenv('PATH', '/path-that-does-not-exist')

    assert config.configure_ytdlp_runtime()
    assert os.environ['PATH'].split(os.pathsep)[0] == str(executable.parent)
    assert config.Config.has_ytdlp()


def test_environment_status_does_not_rewrite_path_for_ytdlp_discovery(tmp_path, monkeypatch):
    executable = tmp_path / 'venv' / 'bin' / 'python'
    executable.parent.mkdir(parents=True)
    executable.write_text('')
    ytdlp = executable.parent / 'yt-dlp'
    ytdlp.write_text('#!/bin/sh\n')
    ytdlp.chmod(0o755)
    monkeypatch.setattr(config.sys, 'executable', str(executable))
    monkeypatch.setattr(config.importlib.util, 'find_spec', lambda _: None)
    monkeypatch.setattr(config, '_candidate_ffmpeg_bin_dirs', lambda: [])
    monkeypatch.setenv('PATH', '/path-that-does-not-exist')

    status = config.Config.get_env_status()

    assert status['hasYtdlp']
    assert os.environ['PATH'] == '/path-that-does-not-exist'


def test_ensure_ytdlp_runtime_installs_the_declared_dependency_when_missing(monkeypatch):
    monkeypatch.setattr(config, 'configure_ytdlp_runtime', Mock(side_effect=[False, True]))
    monkeypatch.setattr(config.importlib.util, 'find_spec', Mock(return_value=None))
    run = Mock()
    monkeypatch.setattr(config.subprocess, 'run', run)

    assert config.ensure_ytdlp_runtime()
    assert run.call_args.args[0][0] == config.sys.executable
    assert run.call_args.args[0][1:3] == ['-m', 'pip']
    assert 'yt-dlp>=2024.8' in run.call_args.args[0]
