# ClipForge

<p align="center">
  <img src="docs/images/clipforge-banner.svg" alt="ClipForge — your footage, your audience, your edit" width="1280" />
</p>

<p align="center">
  <strong>Turn long recordings into short, audience-relevant stories.</strong><br />
  Import. Find the idea. Shape the edit. Render on your own machine.
</p>

<p align="center">
  <a href="https://github.com/Adnr7/clipforge/actions/workflows/ci.yml"><img src="https://github.com/Adnr7/clipforge/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-88c0d0" alt="MIT license" /></a>
  <a href="docs/development.md"><img src="https://img.shields.io/badge/Python-3.10%2B-74a6be" alt="Python 3.10 or newer" /></a>
  <a href="frontend/package.json"><img src="https://img.shields.io/badge/React-19-b8d7e8" alt="React 19" /></a>
</p>

<p align="center">
  <a href="#getting-started">Get started</a> ·
  <a href="#see-the-studio">See the studio</a> ·
  <a href="#how-it-works">Workflow</a> ·
  <a href="docs/README.md">Documentation</a> ·
  <a href="CONTRIBUTING.md">Contribute</a>
</p>

---

ClipForge is a **local-first video and audio clipping studio** for desktop or
browser. Make a manual cut, or give your configured model an audience and a
takeaway. Review the source evidence, choose framing and captions, and export
H.264 MP4s or a ZIP of completed clips.

**No ClipForge account or subscription.** Manual editing needs no API key.
Cloud providers may charge for usage; local AI needs installed models and
appropriate hardware. The app is an early-stage **source distribution**;
browser mode is the easiest starting point.

## See the studio

### Source, transcript, and evidence — together

![Dark workspace with source video, timed transcript, and an audience-scored candidate showing opening and payoff evidence](docs/images/workspace-dark.png)

<table>
  <tr>
    <td width="50%"><strong>Shape the story in AI Edit</strong><br /><img src="docs/images/ai-edit-dark.png" alt="AI Edit with an audience brief, audio-and-visual recommendation, and reviewed automation" /></td>
    <td width="50%"><strong>Style the output in Light</strong><br /><img src="docs/images/caption-studio-light.png" alt="Light caption studio with source framing and eight caption presets" /></td>
  </tr>
</table>

<details>
<summary><strong>Explore the project library in Nord</strong></summary>

![Nord project library with imported, transcribed, and analyzed projects](docs/images/library-nord.png)

</details>

<sub>Screenshots show the actual React interface with generated media and synthetic
project/model responses. [Reproduce the screenshots](docs/testing.md#documentation-screenshots).</sub>

## What you can do

| Capability | In the studio |
| --- | --- |
| **Bring your recordings** | Local video/audio upload, external source path, or a single YouTube video via yt-dlp |
| **Cut without AI** | Create and render exact manual time ranges, including silent video |
| **Find spoken ideas** | Deepgram or local Whisper → timed evidence → audience-aware scout and critic |
| **Inspect visual moments** | Bounded timestamped stills with an image-capable model; no transcript required |
| **Guide an edit** | Shared audience/goal, audio-and-visual advice, and reviewed automation |
| **Preserve or reframe** | Source, 16:9, 9:16, 1:1, 4:5, 4:3, 3:2; fit or center-crop |
| **Make it readable** | Eight caption presets; font, outline, shadow, background, placement, and word grouping |
| **Adjust the look** | Brightness, contrast, saturation, blur, sharpening, and exact rendered-frame previews |
| **Export locally** | H.264 MP4, original source audio when available, and branded ZIP filenames |
| **Use your connection** | Saved provider profiles, custom models/endpoints, and Dark / Light / Nord themes |

### Choose your editing path

| | Manual | Combined AI analysis | Visual-only fallback |
| --- | --- | --- | --- |
| Best for | A time range you already know | Any source where the AI should judge the whole story | Demos, silent footage, music videos |
| Needs transcription | No; only for captions | When audio context is available | No |
| Needs a model | No | Text-capable; vision improves video decisions | Image-capable |
| Boundary basis | Your start/end values | Verified transcript candidates, checked against frames | Approximate intervals backed by sampled frames |
| Review | Framing and final export | Audio payoff, visible evidence, relevance, timing | Visible evidence, framing, timing |

## How it works

```mermaid
flowchart LR
  A[Import video or audio] --> B{Choose a path}
  B --> C[Manual time range]
  B --> D[Full timed speech + sampled frames + audience]
  D --> F[Scout → critic → final audio/visual decision]
  C --> H[Review clips and settings]
  F --> H
  H --> I[Render locally with FFmpeg]
  I --> J[Preview · MP4 · ZIP]
```

### Audience first, source grounded

Tell ClipForge **who should care** and **what they should take away**. The shared
audience brief reaches Manual analysis, AI Edit, combined audio-and-visual
selection, and editing-look suggestions. Changing fields alone does not call a
model.

AI Edit uses one bounded brief contract across recommendations and analysis:
audience and takeaway allow 1,000 characters each, notes allow 2,000, and the
complete formatted brief sent to a provider is limited to 5,000 characters.

For speech selection, the model scouts timestamped transcript units, then a
separate critic call reviews the shortlisted text. Code resolves timestamps,
validates opening/closing quotes, computes scores, and removes overlaps and
duplicate topic labels. It returns fewer clips when ideas do not meet the gate.

| Editorial dimension | Weight | Question |
| --- | ---: | --- |
| **Audience fit** | **35%** | Does this specific viewer have a reason to care? |
| Hook | 20% | Does the actual opening earn attention? |
| Payoff | 20% | Does the clip deliver its promise? |
| Standalone clarity | 15% | Does it make sense without missing context? |
| Shareability | 10% | Why might this viewer save or share it? |

Each dimension is scored 0–5; audience fit, payoff, and clarity must each reach
3. The displayed 0–100 score is an **editorial heuristic**, not a prediction of
views or retention. The critic uses the same selected model in a separate call.
Visual analysis has its own single-pass sampled-frame contract.
[Read the method, limits, and evaluation guide →](docs/audience-selection.md)

## Getting started

### Reproducible Linux setup

The repository pins Python in `.python-version`, Node in `.nvmrc`, Python
dependencies in `requirements.lock`/`requirements-dev.lock`, and JavaScript
dependencies in `frontend/package-lock.json`. On a clean Linux machine with
Python 3.12, Node 22.12, and `make`:

```bash
git clone https://github.com/Adnr7/clipforge.git
cd clipforge
make setup
make build
make test
make run
```

Open **http://127.0.0.1:5174**. On x86_64 Linux/WSL, the first startup also
bootstraps a verified user-space FFmpeg/FFprobe runtime when native tools are not
already available. `make setup` installs the declared Python and frontend
dependencies locally; it does not install anything globally.

### Reproducible Windows PowerShell setup

The `Makefile` uses POSIX paths such as `.venv/bin/python` and is not a native
Windows command. On Windows, use the included PowerShell entrypoint instead:

```powershell
git clone https://github.com/Adnr7/clipforge.git
Set-Location .\clipforge

# Only needed if this PowerShell session blocks local scripts.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\scripts\setup_windows.ps1 -Task setup
.\scripts\setup_windows.ps1 -Task build
.\scripts\setup_windows.ps1 -Task test
.\scripts\setup_windows.ps1 -Task run
```

Or run the complete setup, build, test, and server flow with:

```powershell
.\scripts\setup_windows.ps1 -Task all
```

The Windows script expects the Python Launcher with Python **3.12**, Node.js/npm,
and PowerShell. Setup downloads/installs all **project** dependencies locally:

1. Creates `.venv` and installs the locked Python packages from
   `requirements-dev.lock`.
2. Runs `npm --prefix frontend ci` using `frontend/package-lock.json`.
3. Downloads both pinned native Windows `ffmpeg.exe` and `ffprobe.exe`, verifies
   their SHA-256 checksums, validates `drawtext` and `libx264`, and stores them
   under `%LOCALAPPDATA%\ClipForge\ffmpeg\6.0\bin`.

Python 3.12 and Node.js/npm are host prerequisites rather than project
dependencies. If the Python Launcher is installed but Python 3.12 is missing,
the setup script first asks the launcher to install the official Python 3.12
package through `winget`. If that is unavailable, install it manually:

```powershell
winget install --id Python.Python.3.12 --exact --scope user
```

Reopen PowerShell after installing Python or Node.js. The project script does
not install system-wide runtimes without your package-manager approval and does
not require administrator access for its own FFmpeg/FFprobe installation.

The same project can run in Docker without a host Python or Node installation:

```bash
docker build -t clipforge:local .
docker run --rm -p 5174:5174 -v clipforge-data:/data clipforge:local
```

The container includes FFmpeg, FFprobe, fonts, the locked Python runtime, and
the production frontend. It stores application data in the named `clipforge-data`
volume. For deployment, build and publish the image from a trusted registry, then
run it with a persistent `/data` volume and a separately managed `.env` file; do
not put credentials in the image or repository.

### Prerequisites

| Dependency | Requirement | Used for |
| --- | --- | --- |
| Python | **3.12** for the pinned clean-build path | Backend and desktop launcher |
| Node.js | **22.12.0** for the pinned clean-build path | Building the interface and browser tests |
| FFmpeg + FFprobe | Automatically bootstrapped on x86_64 Linux/WSL and native x86_64 Windows; native tools required on macOS/other architectures | Probe, previews, captions, and export |
| Native WebView | Optional, platform-specific | Desktop window; browser mode does not need it |

The `make setup` flow above is the preferred clean-machine path. Manual setup is
also supported when you need to control the virtual environment or frontend
installation separately.

<details>
<summary><strong>Media runtime setup</strong></summary>

On **x86_64 Linux or WSL**, ClipForge automatically downloads a pinned,
SHA-256-verified user-space FFmpeg/FFprobe runtime the first time you start the
backend. It is installed under `~/.local/share/clipforge/ffmpeg/` and reused on
future launches. No `sudo` or interactive package-manager step is required.

On **native x86_64 Windows**, ClipForge performs the same *type* of verified
setup as Linux/WSL, but it uses separate native Windows `.exe` assets. The files are stored under
`%LOCALAPPDATA%\ClipForge\ffmpeg\` and are used by the backend without changing
the machine-wide PATH or requiring administrator access. The first launch needs
network access; restart ClipForge after a failed or interrupted download.

To preinstall the Windows runtime before opening the app, run this from
PowerShell at the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\setup_ffmpeg_windows.py
```

The runtime includes the `drawtext` filter and `libx264`. Windows supplies the
caption fonts through its normal Fonts directories; WSL/Linux needs usable fonts
installed inside the Linux environment.

The backend also checks the declared `yt-dlp` Python dependency at startup and
installs it into the active virtual environment when it is missing. This enables
YouTube import without requiring a separate system executable. Ollama and
Whisper model weights remain optional because they are large, user-selected
services rather than required by manual editing.

If automatic setup fails, you can retry by restarting ClipForge or install the
tools manually:

```bash
# Ubuntu / Debian
sudo apt install ffmpeg fonts-dejavu-core python3-venv

# macOS with Homebrew
brew install ffmpeg
```

On Windows, the automatic setup above is preferred. If your machine is offline,
uses Windows ARM, or your organization blocks application downloads, install an
FFmpeg build containing FFprobe, `drawtext`, and `libx264`, then either add its
`bin` directory to `PATH` or set `CLIPFORGE_FFMPEG_DIR` to that directory before
starting ClipForge. Verify both `ffmpeg -version` and `ffprobe -version`.

macOS, ARM Linux, Windows ARM, and other unsupported platforms require a native
FFmpeg/FFprobe installation. A Git clone cannot install system binaries or
bypass OS permissions on every platform; ClipForge detects native tools and
shows platform-specific guidance when they are unavailable.

</details>

### 1 · Install the backend

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock
```

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.lock
```

### 2 · Build the interface

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

### 3 · Open the studio

```bash
python main.py --web
```

Open **http://127.0.0.1:5174**. For the native window, use `python main.py` and
follow [pywebview's platform installation guide](https://pywebview.flowrl.com/guide/installation.html).

### 4 · Add AI when you need it

Open **Settings → Providers**, save a connection, and activate it.

| Task | What to configure |
| --- | --- |
| Manual cuts / captionless rendering | Nothing — no AI key needed |
| Cloud transcription | Deepgram API key |
| Local transcription | `python -m pip install -r requirements-whisper.txt`, then a Whisper profile |
| Speech selection | DeepSeek, Claude, Gemini, OpenAI, OpenRouter, Groq, Ollama, or custom OpenAI-compatible model |
| Visual selection / recommendations | An image-capable model on a supported connection |

Whisper downloads its selected model on first use. Ollama runs as a separate
service. A provider can offer both text-only and vision models; choose the
actual capability you need. [Provider setup and transport details](docs/development.md#provider-connections).

## Make your first clip

### Without AI

1. Create a project and import media.
2. Choose **Create manual cut**, set start/end times, and save.
3. Open **Caption Controls & filters**; choose framing and the look. Keep captions
   off when no speech transcript exists.
4. Select the clip and choose **Render selected**.
5. Preview the completed artifact, then download its MP4 or the selected-clips ZIP.

### With an audience brief

1. Set **Audience & takeaway** in Manual, or the shared fields in **AI Edit**.
2. Let AI create timed speech context when audio is available.
3. Run analysis; AI combines the full transcript with sampled frames, then review boundaries, audience benefit, rubric, and source evidence.
4. Choose the clips and settings, render, and inspect the result.

AI Edit uses **combined audio-and-visual analysis** when video and speech are
available, and falls back to the strongest available evidence. Review the plan
before starting.
**Use AI recommended settings** is opt-in; reusing existing candidates does not
rerank them for a new audience. [Detailed AI Edit behavior](docs/ai-edit-mode.md).

New projects preserve source framing without upscaling. **Fit** keeps the whole
image and may add bars; **center-crop** trims edges. Neither stretches footage.
Audio-only clips use a black video canvas. Completed MP4s retain their rendered
look; changing controls requires a new render.

## Local data and processing

| Setting | Default | Purpose |
| --- | --- | --- |
| `DATA_DIR` | `~/.clipforge` | SQLite database, saved settings/profiles, managed media and renders |
| `CLIPFORGE_PORT` | `5174` | Local backend and built interface |

API keys can be saved in Settings; a root `.env` is optional.
[`.env.example`](.env.example) documents defaults. Saved `DATA_DIR/.env` values
take precedence over root `.env` at startup. Set `DATA_DIR` before launching to
use another storage location.

| Feature | Processing / provider payload |
| --- | --- |
| Manual cuts, previews, renders, ZIPs | Local files and FFmpeg |
| Deepgram transcription | Extracted audio sent to Deepgram |
| Cloud speech selection | Timed transcript windows, shortlisted clip text, and editing brief |
| Cloud audio-and-visual analysis | Full timed transcript, bounded timestamped JPEGs, and editing brief |
| Local Whisper / local Ollama | Local inference after installing models |

The app has no application telemetry and binds to localhost. Credentials are
stored locally, **not encrypted at rest**, and are write-only through the API.
YouTube import contacts YouTube through yt-dlp.
[Data architecture](docs/architecture.md) · [Security and private reporting](SECURITY.md).

## Project scope

| Available today | Future work |
| --- | --- |
| Browser/pywebview interface, source installation | Standalone installers and native distribution verification |
| Sampled-still visual analysis | Full-video, music/beat, or continuous-motion understanding |
| Fit / center-crop reframing | Face or active-speaker tracking |
| Manual ranges and burned-in captions | Timeline editor and SRT/VTT export |
| Local MP4 / ZIP downloads | Direct publishing to social platforms |

See the [roadmap](docs/roadmap.md) for proposed improvements.

## Build with us

```bash
make setup
make build
make test
npm --prefix frontend run lint
# First time: install Chromium and supported-platform dependencies
npm --prefix frontend exec -- playwright install --with-deps chromium
npm --prefix frontend run test:e2e
```

Backend checks include real FFmpeg processing of synthetic media. Browser checks
use controlled API responses. CI runs Python 3.10/3.12 and Node 22; live model
quality and the native desktop runtime need separate evaluation.

| Start here | What you will find |
| --- | --- |
| [Documentation hub](docs/README.md) | Guides organized by task |
| [Contributing](CONTRIBUTING.md) | First contribution, change guidelines, PR workflow |
| [Development](docs/development.md) | Setup, directory map, providers, migrations, debugging |
| [Architecture](docs/architecture.md) | System diagram, persistence, jobs, render identity |
| [API reference](docs/api.md) | Endpoints, payloads, job polling, render and export examples |
| [Audience selection](docs/audience-selection.md) | Scout/critic method, scoring, evidence, limits, evaluation |
| [Testing](docs/testing.md) | Focused/full checks, fixtures, screenshots, CI |
| [Troubleshooting](docs/troubleshooting.md) | Common setup, model, media, and job problems |
| [UI review](docs/ui-review.md) | Themes, responsive layouts, accessibility review |

Bug reports, focused fixes, and documentation improvements are welcome.
[Open an issue](https://github.com/Adnr7/clipforge/issues/new/choose), read
[CONTRIBUTING.md](CONTRIBUTING.md), and follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Maintainers can use the [publishing guide](docs/publishing.md).

---

**MIT licensed.** [License](LICENSE) · [Credits and dependencies](docs/credits.md).
Third-party tools, services, and model weights retain their own licenses.
