import os
import platform
import shutil
import subprocess
import logging
import importlib.util
import sys
from pathlib import Path
from flask import current_app, has_app_context


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FFMPEG_TOOL_NAMES = ('ffmpeg', 'ffprobe')
_ACTIVE_FFMPEG_DIR = None
_MEDIA_RUNTIME = {
    'state': 'not_checked',
    'platform': f'{platform.system().lower()}-{platform.machine().lower()}',
    'source': None,
    'message': 'Media runtime setup has not run yet.',
}
_LOGGER = logging.getLogger(__name__)


def _candidate_ffmpeg_bin_dirs():
    """Return native Linux FFmpeg locations, ordered from most specific.

    Windows directories can be present in WSL's inherited PATH.  They are
    deliberately not considered here: a Windows executable cannot reliably
    consume paths in the WSL filesystem.  The setup helper installs static
    Linux tools under the user's application-tools directory. These locations also
    make existing user-space apt extractions usable when their libraries are
    available.
    """
    candidates = []

    def add(path):
        path = Path(path).expanduser().absolute()
        if path not in candidates:
            candidates.append(path)

    configured = os.environ.get('CLIPFORGE_FFMPEG_DIR')
    if configured:
        add(configured)

    # Keep a normal installation on PATH first.  If it is a user-space
    # extraction, _library_dirs_for() below supplies its private libraries.
    for name in _FFMPEG_TOOL_NAMES:
        found = shutil.which(name)
        if found and not found.lower().endswith('.exe'):
            add(Path(found).parent)

    for path in (
        _PROJECT_ROOT / '.runtime' / 'ffmpeg' / 'bin',
        _PROJECT_ROOT / 'runtime' / 'ffmpeg' / 'bin',
        Path.home() / '.local' / 'share' / 'clipforge' / 'ffmpeg' / 'bin',
        Path.home() / '.local' / 'bin',
        Path.home() / '.local' / 'ffmpeg-root' / 'usr' / 'bin',
        Path.home() / '.local' / 'ffmpeg' / 'usr' / 'bin',
    ):
        add(path)

    # setup_ffmpeg_wsl.py keeps versions side-by-side so an update can be
    # atomic.  Include all such installs, newest path first by name.
    versioned = Path.home() / '.local' / 'share' / 'clipforge' / 'ffmpeg'
    try:
        for path in sorted(versioned.glob('*/bin'), reverse=True):
            add(path)
    except OSError:
        pass
    return candidates


def _library_dirs_for(bin_dir):
    """Find private library directories next to a user-space extraction."""
    bin_dir = Path(bin_dir).expanduser().resolve()
    if bin_dir in (Path('/usr/bin'), Path('/bin').resolve(), Path('/usr/local/bin')):
        return []  # The loader already searches system libraries.
    roots = [bin_dir.parent, bin_dir.parent.parent]
    candidates = []
    for root in roots:
        for path in (root / 'lib', root / 'lib64', root / 'lib' / 'x86_64-linux-gnu'):
            if path.is_dir() and path not in candidates:
                candidates.append(path)
    return candidates


def _runtime_environment(bin_dir):
    env = os.environ.copy()
    library_dirs = _library_dirs_for(bin_dir)
    if library_dirs:
        existing = [item for item in env.get('LD_LIBRARY_PATH', '').split(':') if item]
        entries = dict.fromkeys([*(str(path) for path in library_dirs), *existing])
        env['LD_LIBRARY_PATH'] = ':'.join(entries)
    return env


def _usable_executable(path, env=None):
    path = Path(path)
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    try:
        if os.name != 'nt':
            if path.resolve().suffix.lower() == '.exe':
                return False
            with path.open('rb') as executable:
                if executable.read(2) == b'MZ':
                    return False
        result = subprocess.run(
            [str(path), '-version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors='replace',
            env=env,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    name = path.name.removesuffix('.exe')
    return result.returncode == 0 and result.stdout.startswith(f'{name} version ')


def _render_capable_ffmpeg(path, env=None):
    """Require the filters and encoder used by ClipForge's render path."""
    try:
        filters = subprocess.run(
            [str(path), '-hide_banner', '-filters'],
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
            check=False,
        )
        encoders = subprocess.run(
            [str(path), '-hide_banner', '-encoders'],
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
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


def configure_ffmpeg_runtime(candidate_dirs=None):
    """Expose a working native FFmpeg pair to literal subprocess commands.

    Media services intentionally invoke ``ffmpeg`` and ``ffprobe`` by name so
    their commands remain portable.  This function makes that contract hold
    for WSL by selecting one directory containing both *working* Linux
    executables and prepending it to this process's environment.  It also
    supplies LD_LIBRARY_PATH for user-space apt extractions.  No Windows
    ``.exe`` is ever selected.

    Returns True when both commands can execute, otherwise False.  Failure is
    non-fatal so the status endpoint can explain an incomplete setup.
    """
    global _ACTIVE_FFMPEG_DIR
    if os.name == 'nt':
        _ACTIVE_FFMPEG_DIR = None
        return all(shutil.which(name) for name in _FFMPEG_TOOL_NAMES)

    directories = list(candidate_dirs) if candidate_dirs is not None else _candidate_ffmpeg_bin_dirs()
    for directory in directories:
        directory = Path(directory).expanduser().absolute()
        ffmpeg = directory / 'ffmpeg'
        ffprobe = directory / 'ffprobe'
        env = _runtime_environment(directory)
        if not (_usable_executable(ffmpeg, env)
                and _render_capable_ffmpeg(ffmpeg, env)
                and _usable_executable(ffprobe, env)):
            continue

        path_entries = [str(directory)]
        path_entries.extend(item for item in os.environ.get('PATH', '').split(os.pathsep)
                            if item and Path(item).expanduser() != directory)
        os.environ['PATH'] = os.pathsep.join(path_entries)
        if 'LD_LIBRARY_PATH' in env:
            os.environ['LD_LIBRARY_PATH'] = env['LD_LIBRARY_PATH']
        _ACTIVE_FFMPEG_DIR = directory
        return True
    _ACTIVE_FFMPEG_DIR = None
    return False


def _runtime_source(directory):
    if directory is None:
        return None
    directory = Path(directory).expanduser().absolute()
    configured = os.environ.get('CLIPFORGE_FFMPEG_DIR')
    if configured and directory == Path(configured).expanduser().absolute():
        return 'configured'
    managed_root = Path.home() / '.local' / 'share' / 'clipforge' / 'ffmpeg'
    if directory == managed_root or managed_root in directory.parents:
        return 'managed'
    return 'system'


def _set_media_runtime(state, message, source=None):
    _MEDIA_RUNTIME.update({
        'state': state,
        'platform': f'{platform.system().lower()}-{platform.machine().lower()}',
        'source': source,
        'message': message,
    })


def _runtime_is_ready():
    if not configure_ffmpeg_runtime():
        return False
    # POSIX discovery validates drawtext/libx264; the Windows branch preserves
    # the existing PATH contract, so run the full capability check there too.
    return not (os.name == 'nt') or (Config.has_ffmpeg() and Config.has_ffprobe())


def ensure_media_runtime():
    """Make media tools ready before the server accepts user work.

    Existing native tools are always preferred. On x86_64 Linux/WSL, a missing
    runtime is filled with the repository's pinned, checksum-verified user-space
    installer. Other platforms remain usable for manually installed native
    tools, but never receive an incompatible Linux download.
    """
    if _runtime_is_ready():
        source = _runtime_source(_ACTIVE_FFMPEG_DIR)
        _set_media_runtime('ready', 'FFmpeg and FFprobe are ready.', source)
        return get_media_runtime_status()

    system = platform.system().lower()
    machine = platform.machine().lower()
    if os.environ.get('CLIPFORGE_FFMPEG_DIR'):
        _set_media_runtime(
            'failed',
            'CLIPFORGE_FFMPEG_DIR is set, but it does not contain a working native '
            'FFmpeg/FFprobe pair with drawtext and libx264.',
            'configured',
        )
        return get_media_runtime_status()
    if system != 'linux' or machine not in {'x86_64', 'amd64'}:
        _set_media_runtime(
            'unsupported',
            'Automatic media setup is available for x86_64 Linux/WSL. Install '
            'native FFmpeg and FFprobe with drawtext, libx264, and usable fonts '
            'then restart ClipForge.',
        )
        return get_media_runtime_status()

    try:
        from scripts.setup_ffmpeg_wsl import install

        install(Path.home() / '.local' / 'share' / 'clipforge' / 'ffmpeg')
        if _runtime_is_ready():
            _set_media_runtime(
                'ready',
                'ClipForge installed a verified FFmpeg/FFprobe runtime in your user directory.',
                'managed',
            )
            return get_media_runtime_status()
        raise RuntimeError('The installed tools did not pass the runtime capability check.')
    except Exception:
        _set_media_runtime(
            'failed',
            'Automatic media setup could not finish. Check your network and permissions, '
            'or install native FFmpeg/FFprobe manually, then restart ClipForge.',
        )
        # Keep the detailed cause in the backend log without exposing filesystem
        # details through the public status response.
        _LOGGER.exception('Automatic media runtime setup failed')
        return get_media_runtime_status()


def get_media_runtime_status():
    return dict(_MEDIA_RUNTIME)


def configure_ytdlp_runtime():
    """Make a venv-installed yt-dlp executable visible to child processes."""
    candidate = _ytdlp_executable()
    if candidate is not None:
        executable_dir = candidate.parent
        entries = [str(executable_dir)]
        entries.extend(item for item in os.environ.get('PATH', '').split(os.pathsep)
                       if item and Path(item).expanduser() != executable_dir)
        os.environ['PATH'] = os.pathsep.join(entries)
        return True
    return bool(shutil.which('yt-dlp'))


def _ytdlp_executable():
    executable_dir = Path(sys.executable).resolve().parent
    candidate = executable_dir / ('yt-dlp.exe' if os.name == 'nt' else 'yt-dlp')
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def ensure_ytdlp_runtime():
    """Install the declared Python yt-dlp dependency if a clone lacks it."""
    if configure_ytdlp_runtime() or importlib.util.find_spec('yt_dlp') is not None:
        return True
    try:
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', 'yt-dlp>=2024.8'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return configure_ytdlp_runtime() or importlib.util.find_spec('yt_dlp') is not None


class Config:
    @staticmethod
    def get_data_dir():
        if has_app_context() and 'DATA_DIR' in current_app.config:
            return current_app.config['DATA_DIR']
        return Config.get_environment_data_dir()

    @staticmethod
    def get_environment_data_dir():
        return os.path.abspath(os.path.expanduser(os.environ.get('DATA_DIR') or '~/.clipforge'))

    @staticmethod
    def get_llm_provider():
        return os.environ.get('LLM_PROVIDER', 'deepseek').lower()

    @staticmethod
    def has_ffmpeg():
        configure_ffmpeg_runtime()
        path = shutil.which('ffmpeg')
        env = os.environ.copy()
        return bool(path and _usable_executable(path, env) and _render_capable_ffmpeg(path, env))

    @staticmethod
    def has_ffprobe():
        configure_ffmpeg_runtime()
        path = shutil.which('ffprobe')
        return bool(path and _usable_executable(path, os.environ.copy()))

    @staticmethod
    def has_ytdlp():
        # Status checks must not mutate the process environment.  Startup calls
        # ensure_ytdlp_runtime(), which performs the separate PATH setup needed
        # by child processes before the server accepts work.
        return bool(_ytdlp_executable() or shutil.which('yt-dlp')
                    or importlib.util.find_spec('yt_dlp') is not None)

    @staticmethod
    def get_env_status():
        has_ffmpeg = Config.has_ffmpeg()
        has_ffprobe = Config.has_ffprobe()
        runtime = get_media_runtime_status()
        if has_ffmpeg and has_ffprobe and runtime['state'] == 'not_checked':
            runtime = {
                **runtime,
                'state': 'ready',
                'source': _runtime_source(_ACTIVE_FFMPEG_DIR),
                'message': 'FFmpeg and FFprobe are ready.',
            }
        return {
            'dataDir': Config.get_data_dir(),
            'hasFfmpeg': has_ffmpeg,
            'hasFfprobe': has_ffprobe,
            'mediaRuntime': runtime,
            'hasDeepgramKey': bool(os.environ.get('DEEPGRAM_API_KEY', '').strip()),
            'hasDeepseekKey': bool(os.environ.get('DEEPSEEK_API_KEY', '').strip()),
            'hasAnthropicKey': bool(os.environ.get('ANTHROPIC_API_KEY', '').strip()),
            'hasGeminiKey': bool(os.environ.get('GEMINI_API_KEY', '').strip()),
            'hasOpenaiKey': bool(os.environ.get('OPENAI_API_KEY', '').strip()),
            'hasOpenrouterKey': bool(os.environ.get('OPENROUTER_API_KEY', '').strip()),
            'hasGroqKey': bool(os.environ.get('GROQ_API_KEY', '').strip()),
            'hasCustomProvider': bool(os.environ.get('CUSTOM_BASE_URL', '').strip()) and \
                                 bool(os.environ.get('CUSTOM_API_KEY', '').strip()),
            'llmProvider': Config.get_llm_provider(),
            'hasYtdlp': Config.has_ytdlp(),
        }


# Configure as soon as backend modules are imported.  main.py calls this once
# more explicitly before creating the Flask app, while alternate Flask entry
# points and test clients get the same subprocess environment.
configure_ffmpeg_runtime()
