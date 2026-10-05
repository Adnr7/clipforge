# ClipForge

**A local-first studio for turning long videos and audio into short clips.**

[![CI](https://github.com/Adnr7/clipforge/actions/workflows/ci.yml/badge.svg)](https://github.com/Adnr7/clipforge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

ClipForge combines media import, optional transcription, AI-assisted highlight
selection, manual cuts, captions, and MP4 export in one desktop or browser
workspace. Keep the source framing or choose an output ratio, review the edit,
and render on your own machine.

[Getting started](#getting-started) · [Usage](#usage) ·
[Documentation](#documentation) · [Contributing](#contributing) ·
[MIT license](LICENSE)

## Features

- **Import local video or audio**, or a single YouTube video through yt-dlp.
- **Make manual cuts without AI or transcription**, including silent video.
- **Find spoken highlights** using Deepgram or local Whisper transcripts and
  your selected analysis model.
- **Find visual highlights without speech** using timestamped frame samples
  and an image-capable model.
- **Use AI Edit** for project chat, an editing brief, visual recommendations,
  and a reviewed transcribe/analyze/select/render workflow.
- **Control output framing**: source, 16:9, 9:16, 1:1, 4:5, 4:3, or 3:2; fit
  or center-crop; configurable maximum output edge.
- **Style captions and video** with eight caption presets, typography and
  placement controls, brightness, contrast, saturation, blur, and sharpening.
- **Preview and export** H.264 MP4 clips, or download completed selections as
  a ZIP. Output retains source audio when available.
- **Manage provider profiles** for DeepSeek, Claude, Gemini, OpenAI,
  OpenRouter, Groq, Ollama, Deepgram, or custom OpenAI-compatible endpoints.
- **Choose Dark, Light, or Nord** with a persistent theme preference.

ClipForge does not require an account or subscription. Cloud providers may
charge for API usage; manual editing needs no API key. Local AI requires
appropriate models and hardware.

## Project status

ClipForge is an early-stage application distributed as source. Browser mode is
the simplest starting point; desktop mode uses pywebview and requires the
appropriate native WebView runtime. Standalone installers are not provided.

AI results are suggestions to review. Visual analysis samples still frames; it
does not hear music, detect musical beats, or inspect every frame. Reframing is
fit/center-crop, not face or active-speaker tracking. Timeline editing, subtitle
file export, and publishing directly to social platforms are not implemented.

## Getting started

Clone the repository and open its root directory:

```bash
git clone https://github.com/Adnr7/clipforge.git
cd clipforge
```

You can also download the source ZIP from GitHub and extract it locally.

### Prerequisites

| Dependency | Requirement |
| --- | --- |
| Python | 3.10 or newer |
| Node.js | 22.12 or newer, for the frontend build |
| FFmpeg and FFprobe | On `PATH`, with `libx264`, `drawtext`, and font support |

Install FFmpeg using your operating system's package manager. For example:

```bash
# Ubuntu / Debian
sudo apt install ffmpeg fonts-dejavu-core python3-venv

# macOS with Homebrew
brew install ffmpeg
```

On Windows, install an FFmpeg build containing FFprobe, add its `bin` directory
to `PATH`, and open a new terminal. Check that `ffmpeg -version` and
`ffprobe -version` both work.

### 1. Install Python dependencies

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For optional **local Whisper transcription**, install its additional dependencies
in the same environment:

```bash
python -m pip install -r requirements-whisper.txt
```

Whisper downloads the selected model on first use. Ollama runs as a separate
service; install and pull a suitable model if you want local analysis.

### 2. Build the interface

```bash
cd frontend
npm ci
npm run build
cd ..
```

### 3. Start ClipForge

With the Python environment activated:

```bash
python main.py --web
```

Open **http://127.0.0.1:5174** in your browser.

For the native desktop window, run `python main.py` instead. See
[pywebview's installation guide](https://pywebview.flowrl.com/guide/installation.html)
for your operating system's WebView requirements.

### 4. Configure optional AI providers

Open **Settings → Providers**, add a connection, save it, and activate it.

| Task | Configuration |
| --- | --- |
| Manual cuts and captionless rendering | No AI provider needed |
| Cloud speech transcription | Deepgram API key |
| Local speech transcription | Optional Whisper dependencies and model |
| Speech analysis or project chat | Text-capable analysis model |
| Visual highlights or recommendations | Image-capable analysis model |

Provider availability does not guarantee that every model supports images.
Select a vision model for visual analysis; text-only models cannot inspect the
sampled frames. API keys can be configured through Settings, so a root `.env`
file is not required for normal use.

## Usage

### Make your first clip without AI

1. Create a project and import a local video or audio file.
2. Choose **Create manual cut**, enter start/end times, and save the cut.
3. Open **Caption Controls & filters** to choose output framing and adjustments.
   Keep captions off if the project has no transcript.
4. Select the clip and choose **Render selected**.
5. Preview the completed clip, then download its MP4 or the selected clips ZIP.

### Find highlights with AI

1. Import media and configure an analysis provider.
2. Choose **Speech** and transcribe before analysis, or choose **Video visuals**
   with an image-capable model. Visual analysis does not require a transcript.
3. Choose **Find clip candidates** and review their boundaries and rationale.
4. Select candidates, adjust the look, render, and download.

For guided editing, switch to **AI Edit**, describe your audience and goal,
request recommendations if useful, and review the plan before starting. AI
settings are applied only when you choose **Use AI recommended settings**.
Stopping automation prevents future steps; jobs already accepted by the server
may finish.

Audience and goal each allow 1,000 characters; notes allow 2,000. The complete
brief, including destination and editing options, reaches chat and analysis
without truncation. If a chat answer would overflow notes, ClipForge keeps the
unsent message and asks you to shorten the notes before sending.

New projects preserve source framing without upscaling by default. **Fit** keeps
the full image and may add bars; **center-crop** trims edges. Neither stretches
the video. Audio-only clips render onto a black video canvas.

## Configuration and data

| Setting | Default | Purpose |
| --- | --- | --- |
| `DATA_DIR` | `~/.clipforge` | Database, saved provider configuration, and managed media |
| `CLIPFORGE_PORT` | `5174` | Local server port |

[`.env.example`](.env.example) lists optional environment-based provider settings.
If you use it, copy it to `.env` and replace the empty values locally. Settings
saved in `DATA_DIR/.env` take precedence over root `.env` defaults on startup.
Set `DATA_DIR` before launching if you want another storage location.

## Privacy and processing

Imports, the project database, and rendered artifacts live on your machine.
What is sent to a provider depends on the feature you choose:

| Feature | Provider receives |
| --- | --- |
| Deepgram transcription | Extracted audio |
| Cloud speech analysis | Transcript and editing brief |
| Cloud project chat | Project metadata, brief, chat history, and optional speech context |
| Cloud visual analysis | Bounded timestamped JPEG samples and optional text context |
| Local Whisper / local Ollama | Processing stays on the local server after model installation |

Credentials are stored locally in dotenv settings and provider profiles; this
is not an encrypted secrets vault. Settings/status APIs do not return saved
keys. The app has no application telemetry and binds to localhost. YouTube
import contacts YouTube through yt-dlp; its license metadata is informational.

## Development and testing

Install backend development dependencies:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

Run the backend with `python main.py --web` and the frontend in another terminal
with `cd frontend` followed by `npm run dev`. The development interface is at
http://localhost:5173 and proxies API requests to the backend on port 5174.

Frontend checks:

```bash
cd frontend
npm ci
npx playwright install --with-deps chromium
npm run build
npm run lint
npm run test:e2e
```

Backend tests generate synthetic media and include real FFmpeg checks. Provider
responses are controlled fixtures; passing them does not verify a live model or
native installer. CI runs backend tests, frontend build/lint, and browser tests.
See [Contributing](CONTRIBUTING.md) for the development workflow.

## Documentation

- [Architecture](docs/architecture.md)
- [AI Edit integration and API contracts](docs/ai-edit-mode.md)
- [Troubleshooting](docs/troubleshooting.md)
- [UI review](docs/ui-review.md)
- [Roadmap](docs/roadmap.md)
- [Credits and dependencies](docs/credits.md)

## Contributing

Bug reports, focused fixes, documentation improvements, and feature proposals
are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), use the issue templates,
and open a pull request with a clear description and relevant verification.
Please follow our [Code of Conduct](CODE_OF_CONDUCT.md).

For vulnerability reporting, see [SECURITY.md](SECURITY.md).

## License

ClipForge is released under the [MIT License](LICENSE). Third-party dependencies,
external tools, and model weights retain their respective licenses.
