# Development guide

[Documentation hub](README.md) · [Contributing](../CONTRIBUTING.md) · [Testing](testing.md)

## Prerequisites and first setup

Follow the [root installation guide](../README.md#getting-started) for Python
3.10+, Node 22.12+, FFmpeg/FFprobe, a virtual environment, and the frontend build.
Then install development tools:

```bash
# Repository root; Python environment activated
python -m pip install -r requirements-dev.txt

# Frontend directory
npm ci
npx playwright install --with-deps chromium
```

| Dependency file | Purpose |
| --- | --- |
| `requirements.txt` | Application runtime |
| `requirements-dev.txt` | Runtime plus pytest |
| `requirements-whisper.txt` | Optional local Whisper; not needed for fixture-based tests |
| `frontend/package-lock.json` | Reproducible JavaScript dependency installation with `npm ci` |

## Run with hot reload

Use two terminals. Set an isolated data directory before starting the backend:

```bash
# Terminal 1 — repository root, macOS/Linux
export DATA_DIR="$PWD/.dev-data"
python main.py --web

# Terminal 2 — frontend/
npm run dev
```

Windows PowerShell equivalent for terminal 1:

```powershell
$env:DATA_DIR = Join-Path (Get-Location) '.dev-data'
python main.py --web
```

Open **http://localhost:5173** for Vite hot reload. Vite proxies `/api` to the
backend at **127.0.0.1:5174**. The backend serves `frontend/dist` at port 5174;
rebuild to update that production interface. The launcher does not reload Python
modules automatically; restart it after backend edits, once active jobs finish.

`CLIPFORGE_PORT` changes the backend port. If you change it, update the Vite proxy
in `frontend/vite.config.ts` too. Browser tests use a separate preview on **5180**;
documentation captures use **5181**.

## Code map

| Area | Start here | Responsibility |
| --- | --- | --- |
| Startup | `main.py`, `backend/app.py`, `backend/config.py` | Local server, desktop shell, environment loading |
| Persistence | `backend/database.py` | sqlite3 connections, schema, additive migrations |
| HTTP surface | `backend/routes/` | Validate requests, dispatch jobs, persist results, serve files |
| Speech | `transcription_service.py`, `routes/transcription.py` | Deepgram/Whisper, timestamped transcript, stage errors |
| Editorial selection | `clip_selection_service.py`, `llm_service.py` | Timed scout/critic, rubric, source checks, transport |
| Visual analysis | `visual_analysis_service.py` | Frame sampling, image-capable transports, strict recommendations |
| Provider profiles | `provider_service.py`, `routes/system.py` | Saved connections, active profiles, write-only keys |
| Media/output | `media_service.py`, `render_settings.py`, `caption_service.py` | Probe, geometry, FFmpeg, captions, shared contract |
| Render/export | `routes/rendering.py` | Artifact identity, shared executor, MP4/ZIP files |
| UI state | `Workspace.tsx`, `AiEditMode.tsx`, `RenderControls.tsx` | Manual/AI integration, reviewed settings and jobs |
| Client contracts | `frontend/src/api/client.ts`, `aiEdit.ts`, `audience.ts` | Types, requests, brief persistence, browser sequencing |
| Appearance | `frontend/src/styles/`, `hooks/useTheme.ts` | Semantic theme tokens and responsive layouts |

Service/component filenames in the table are relative to `backend/services/`
and `frontend/src/components/`, respectively. See [Architecture](architecture.md)
for the complete data flow.

## Provider connections

Use **Settings → Providers** for normal development. Profiles have `kind: llm`
or `transcription`; each kind has its own active selection.

| Connection | Transport / behavior |
| --- | --- |
| DeepSeek, OpenAI, Groq, Gemini, OpenRouter, Ollama, custom | OpenAI-compatible `/chat/completions`, JSON responses |
| Claude | Native Anthropic `/v1/messages`; image requests use native image blocks |
| Deepgram | Fixed listen endpoint, `nova-2`, extracted audio |
| Whisper | In-process optional local inference, selected supported model |

For OpenAI-compatible profiles, `baseUrl` is the API **base** (typically ending
in `/v1`), not the full `/chat/completions` URL; transport appends that path.
Claude expects its native messages endpoint. Custom LLM profiles require both
`baseUrl` and `model`. Check the returned `effectiveModel` and `effectiveBaseUrl`
when debugging selection.

Ollama normally uses `http://localhost:11434/v1`. Start its service and pull the
configured model separately. Text support does not imply image support;
`deepseek-chat`, `llama3.2`, and `llama-3.3-70b-versatile` are known text-only
defaults. Smaller local models may not satisfy complex JSON/source constraints.

Keys are accepted as `apiKey` on profile writes; public responses expose only
`hasApiKey`. Blank/omitted keys on update retain the existing secret. Kind and
provider are immutable on an existing profile. Never use real keys in fixtures.

Root `.env` supplies optional initial defaults. Saved `DATA_DIR/.env` overrides
them at startup. Profile credentials are stored in SQLite; dotenv credentials
and profiles are not encrypted at rest. [API profile reference](api.md#providers-and-settings).

## Storage and migration changes

`DATA_DIR` contains `clipforge.db`, saved `.env`, and managed project/download
files. SQLite uses WAL and per-request connections with foreign keys enabled.
Audience drafts live in project-scoped browser storage; candidate explanations
are stored as `candidates.selection_json` and exposed as `candidate.selection`.

To extend storage:

1. Add an idempotent migration in `backend/database.py`, alongside the existing
   column checks; account for both new and legacy databases.
2. Update serialization and frontend types together.
3. Test a pre-migration database, empty/legacy values, and preservation of outputs.
4. Avoid holding a write transaction during a provider call or FFmpeg work.

Existing projects keep their legacy portrait settings. New projects explicitly
write source-preserving defaults. Do not change old render records to make them
look like newly selected settings.

## Extend a feature end to end

| Change | Update together |
| --- | --- |
| API field | Route validation, service contract, client types/request, route/browser tests, API docs |
| Model response | Prompt/schema, parser, evidence bounds, provider fixtures, audience-method docs |
| Render control | Authoritative contract, defaults, preview/render geometry, identity, UI, FFmpeg tests |
| Async stage | Atomic claim/availability, completion/failure, restart recovery, polling, interruption tests |
| Theme/layout | Shared tokens, three themes, small screens, dialogs, long content, UI review |

For AI changes, keep source-derived time boundaries and local score arithmetic.
For rendering changes, track exact returned artifact IDs and use the same
settings for save, preview, and render. Chat/advice must not silently start jobs.

## Debugging

- Inspect browser Network requests and the backend terminal; sanitize provider
  errors and source paths before sharing logs.
- Inspect `GET /api/projects/<id>` for independently attributed `jobs`, selected
  candidates, recorded clip settings, and `selection` evidence.
- Use an exact frame preview when CSS approximations differ from FFmpeg output.
- Run the [smallest relevant regression suite](testing.md#focused-checks) first.
- On Windows/WSL, verify the intended `DATA_DIR`; the two home directories differ.

For backup, finish jobs and stop the app before copying the entire data directory
(SQLite may have WAL sidecars). External source paths are references: back up
those original files separately. Uploaded sources are project-managed copies.
Project deletion removes owned managed files, not external originals.

## Preparing a pull request

Use [Contributing](../CONTRIBUTING.md) for scope and review guidance. Include the
reason for the change, relevant commands/results, and synthetic screenshots for
visible UI changes. Update documentation in the same PR as behavior changes.
