# Troubleshooting

## The interface does not load

Build the frontend from `frontend/`:

```bash
npm ci
npm run build
```

Start `python main.py --web` from the repository root and open
http://127.0.0.1:5174. Vite's development interface instead runs on port 5173 and
needs the backend running too.

If port 5174 is occupied, stop the other local instance or set `CLIPFORGE_PORT`
before launching. The Vite development proxy also needs updating if you change
the backend port.

## FFmpeg or FFprobe is missing

Run `ffmpeg -version` and `ffprobe -version` in the same terminal used to start
ClipForge. Install both tools and put their directory on `PATH`; on Windows,
open a new terminal after changing `PATH`.

Caption rendering requires a build with `drawtext` and usable fonts. Video
encoding requires `libx264`. On Ubuntu/Debian, install `ffmpeg` and
`fonts-dejavu-core`.

## The desktop window fails to start

Use `python main.py --web` to run in your browser. Native mode requires the
platform-specific WebView dependencies described in the
[pywebview installation guide](https://pywebview.flowrl.com/guide/installation.html).
Install those before attempting desktop mode. Browser tests do not validate the
native desktop runtime.

## A provider is not configured or connection testing fails

Save the profile before activating/testing it. Confirm the effective model and
endpoint in Settings. Blank credential fields retain a saved key; the API does
not return keys to populate the field.

Check the provider's availability, account access, model name, and endpoint.
For Ollama, its service must be running and the requested model installed.
Cloud usage limits and model capabilities depend on the provider.

## Visual analysis says the model cannot inspect images

Select an image-capable analysis model. A provider may offer both text-only and
vision models; activating a provider does not make its text-only models visual.
The defaults `deepseek-chat`, `llama3.2`, and `llama-3.3-70b-versatile` are not
vision models.

## There is no speech to analyze

Use **Video visuals** for a video with an image-capable model, or create a manual
cut. Silence and background music do not produce a speech transcript. Keep
captions off when rendering without speech.

## Whisper is not installed

With the Python virtual environment activated:

```bash
python -m pip install -r requirements-whisper.txt
```

Select a Whisper profile. First use downloads a model and requires network
access; subsequent local inference uses the installed model. Larger models need
more memory and processing time. Whisper provides timestamps but this integration
does not provide multi-speaker diarization.

## Browser tests cannot launch Chromium

From `frontend/`, run:

```bash
npx playwright install --with-deps chromium
```

This installs the browser and supported-platform dependencies. On Linux it may
request system-package installation privileges. Build the frontend before
`npm run test:e2e`; the tests use Vite's production preview on port 5180.

## Settings or projects seem different after switching environments

Windows and WSL/Linux have different home directories. Each defaults to its own
`~/.clipforge`. Check the data directory in Settings and the `DATA_DIR` used by
the process. Set the intended path before starting; ClipForge does not migrate
data automatically when it changes.

For persistent problems, open a bug report with reproduction steps, operating
system, browser/desktop mode, tool versions, and sanitized error details.
