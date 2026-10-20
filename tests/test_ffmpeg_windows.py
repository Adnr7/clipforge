from pathlib import Path
from unittest.mock import Mock

from backend import config


def _windows_tools(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        (directory / name).write_bytes(b'windows executable fixture')


def test_windows_override_selects_exe_pair_and_reports_configured_source(tmp_path, monkeypatch):
    tools = tmp_path / 'native tools'
    _windows_tools(tools)
    monkeypatch.setattr(config, '_is_windows', lambda: True)
    monkeypatch.setenv('PATH', 'C:\\Windows\\System32')
    monkeypatch.setenv('CLIPFORGE_FFMPEG_DIR', str(tools))
    monkeypatch.setattr(config, '_usable_executable', lambda path, env=None: path.is_file())
    monkeypatch.setattr(config, '_render_capable_ffmpeg', lambda path, env=None: True)

    assert config.configure_ffmpeg_runtime()
    assert config._ACTIVE_FFMPEG_DIR == tools.absolute()
    assert config._runtime_source(config._ACTIVE_FFMPEG_DIR) == 'configured'
    assert str(tools) == config.os.environ['PATH'].split(config.os.pathsep)[0]


def test_windows_candidate_search_includes_local_app_data_runtime(tmp_path, monkeypatch):
    local_app_data = tmp_path / 'Local App Data'
    managed_bin = local_app_data / 'ClipForge' / 'ffmpeg' / '6.0' / 'bin'
    _windows_tools(managed_bin)
    monkeypatch.setattr(config, '_is_windows', lambda: True)
    monkeypatch.setenv('LOCALAPPDATA', str(local_app_data))
    monkeypatch.setenv('PATH', 'C:\\Windows\\System32')
    monkeypatch.delenv('CLIPFORGE_FFMPEG_DIR', raising=False)
    monkeypatch.setattr(config, '_PROJECT_ROOT', tmp_path / 'project')

    candidates = config._candidate_ffmpeg_bin_dirs()

    assert managed_bin in candidates
    assert config._managed_ffmpeg_root() == local_app_data / 'ClipForge' / 'ffmpeg'


def test_windows_missing_runtime_bootstraps_x64_user_install(monkeypatch, tmp_path):
    monkeypatch.setattr(config, '_is_windows', lambda: True)
    monkeypatch.setattr(config.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(config.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Local App Data'))
    monkeypatch.delenv('CLIPFORGE_FFMPEG_DIR', raising=False)
    monkeypatch.setattr(config, '_MEDIA_RUNTIME', {
        'state': 'not_checked', 'platform': '', 'source': None,
        'message': 'not checked',
    })
    calls = []

    def configure():
        calls.append(True)
        if len(calls) == 1:
            return False
        config._ACTIVE_FFMPEG_DIR = config._managed_ffmpeg_root() / '6.0' / 'bin'
        return True

    monkeypatch.setattr(config, 'configure_ffmpeg_runtime', configure)
    monkeypatch.setattr(config.Config, 'has_ffmpeg', staticmethod(lambda: True))
    monkeypatch.setattr(config.Config, 'has_ffprobe', staticmethod(lambda: True))
    install = Mock()
    monkeypatch.setattr('scripts.setup_ffmpeg_windows.install', install)

    result = config.ensure_media_runtime()

    assert result['state'] == 'ready'
    assert result['source'] == 'managed'
    assert result['platform'] == 'windows-amd64'
    install.assert_called_once_with(config._managed_ffmpeg_root())
    assert len(calls) == 2


def test_windows_arm_runtime_has_actionable_message(monkeypatch):
    monkeypatch.setattr(config, '_is_windows', lambda: True)
    monkeypatch.setattr(config.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(config.platform, 'machine', lambda: 'ARM64')
    monkeypatch.delenv('CLIPFORGE_FFMPEG_DIR', raising=False)
    monkeypatch.setattr(config, '_MEDIA_RUNTIME', {
        'state': 'not_checked', 'platform': '', 'source': None,
        'message': 'not checked',
    })
    monkeypatch.setattr(config, 'configure_ffmpeg_runtime', lambda: False)
    install = Mock(side_effect=AssertionError('ARM64 must not download an x64 runtime'))
    monkeypatch.setattr('scripts.setup_ffmpeg_windows.install', install)

    result = config.ensure_media_runtime()

    assert result['state'] == 'unsupported'
    assert 'x86_64 only' in result['message']
    install.assert_not_called()


def test_windows_installer_reuses_a_valid_versioned_install(tmp_path, monkeypatch):
    from scripts import setup_ffmpeg_windows

    bin_dir = tmp_path / '6.0' / 'bin'
    _windows_tools(bin_dir)
    works = Mock(return_value=True)
    monkeypatch.setattr(setup_ffmpeg_windows, '_works', works)

    result = setup_ffmpeg_windows.install(tmp_path)

    assert result == tmp_path / '6.0'
    assert works.call_count == 2
