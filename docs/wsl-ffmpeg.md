# Native FFmpeg for WSL without sudo

ClipForge's WSL backend needs Linux `ffmpeg` and `ffprobe` to read WSL paths
such as `/home/...` and `/tmp/...`. Windows `.exe` files on the inherited
`PATH` do not provide that runtime. An apt package extracted without all of its
shared-library dependencies may be discoverable yet fail to execute.

From the repository root, install the native, static tools in user space:

```bash
.venv/bin/python scripts/setup_ffmpeg_wsl.py
.venv/bin/python main.py --web
```

The helper supports **x86_64 Linux/WSL**, downloads pinned archives with
SHA-256 verification, and checks executable identity, `drawtext`, and `libx264`
before publishing them to:

```text
~/.local/share/clipforge/ffmpeg/6.0/bin/ffmpeg
~/.local/share/clipforge/ffmpeg/6.0/bin/ffprobe
```

The FFmpeg asset is `ffmpeg-static` release `b6.0` (FFmpeg 6.0); FFprobe is the
native `@ffprobe-installer/linux-x64` 5.2.0 asset (N-66595). Both are static
Linux executables. Running the helper again reuses a working installation.
Neither apt nor npm is needed by the helper.

`backend/config.py` discovers this install even if the venv is invoked directly
without activation and `~/.local/bin` is absent from `PATH`. Startup prepends
the working directory to the backend process's `PATH`, so existing literal
`subprocess` commands and their children resolve both tools. `main.py` resolves
again after dotenv loading. Config status checks that executables actually run;
FFmpeg also needs the caption filter and H.264 encoder.

## Custom installations

Use an existing native installation by setting its **bin directory**:

```bash
CLIPFORGE_FFMPEG_DIR="/path/to/native tools/bin" .venv/bin/python main.py --web
```

The override can also be set in the project's `.env`. Discovery considers the
override, existing `PATH`, project-local `.runtime/ffmpeg/bin` and
`runtime/ffmpeg/bin`, then user-space locations. Incomplete or incompatible
candidates are skipped. For an extracted apt tree with `usr/bin` and
`usr/lib/<architecture>`, its private libraries are added to the subprocess
`LD_LIBRARY_PATH`; extraction alone must still supply all missing dependencies.

Install elsewhere with `--prefix`, then set the versioned bin directory:

```bash
.venv/bin/python scripts/setup_ffmpeg_wsl.py --prefix /path/to/runtime
CLIPFORGE_FFMPEG_DIR=/path/to/runtime/6.0/bin .venv/bin/python main.py --web
```

## Verify the backend environment

```bash
env PATH=/usr/bin:/bin .venv/bin/python -c "from backend.config import Config; import shutil, subprocess; print(shutil.which('ffmpeg'), shutil.which('ffprobe')); s = Config.get_env_status(); print(s['hasFfmpeg'], s['hasFfprobe']); subprocess.run(['ffmpeg', '-version'], check=True); subprocess.run(['ffprobe', '-version'], check=True)"
.venv/bin/python -m pytest -q tests/test_ffmpeg_runtime.py tests/test_media_service.py
```

Both status flags should be `True`. The runtime environment belongs to the
backend process; it does not alter your interactive shell's `PATH`. Captions
also use fonts installed in WSL or supplied through your fontconfig setup.
