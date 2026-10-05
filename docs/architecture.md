# Architecture

ClipForge is a local-first desktop/browser application with a single Python
backend. The browser and pywebview window use the same React interface and REST
API.

## Components

```text
main.py
  └── Waitress / Flask on 127.0.0.1
        ├── React production build (frontend/dist)
        ├── /api routes
        │     ├── project and provider services → SQLite / local files
        │     ├── media and caption services → FFmpeg / FFprobe
        │     └── analysis / transcription services → selected provider
        └── optional pywebview desktop window
```

| Location | Responsibility |
| --- | --- |
| `main.py` | Browser/desktop startup and local server |
| `backend/app.py` | Flask factory, routes, local-origin checks, frontend serving |
| `backend/database.py` | SQLite schema, additive migrations, connections |
| `backend/routes/` | HTTP validation, job dispatch, persistence, file responses |
| `backend/services/` | Provider calls, media/captions, geometry, job claims |
| `frontend/src/api/` | Typed API requests and automation orchestration |
| `frontend/src/components/` | Project, editor, settings, and AI Edit interface |
| `tests/` | Backend contracts and synthetic-media integration coverage |
| `frontend/tests/` | Playwright workflows against the built interface |

The backend uses Python's `sqlite3`, not an ORM. FFmpeg/FFprobe run as subprocesses.
The frontend build is served by Flask; Vite proxies `/api` during development.

## Project lifecycle

1. Import/upload media and probe its duration, streams, and display geometry.
2. Optionally extract/transcribe speech with Deepgram or local Whisper.
3. Analyze speech text or sampled video frames using a selected model, or create
   manual clip candidates.
4. Review candidates, persist selection, and choose output/caption/filter settings.
5. Render through FFmpeg, track the returned artifact ID, and serve previews or
   completed-file downloads.

Transcript analysis requires usable speech. Visual analysis requires a video
stream and image-capable model, but no transcript. Audio-only renders use a black
canvas. Captions require suitable transcript content.

## Persistence and jobs

`DATA_DIR` defaults to `~/.clipforge` and contains the SQLite database, saved
settings/profiles, and managed media. Root `.env` values are initial defaults;
saved `DATA_DIR/.env` values take precedence at startup. Profile credentials are
write-only through the API and stored locally, not encrypted at rest.

Transcription and analysis use independently attributed project jobs and claims
to prevent stale workers from overwriting newer results. Candidate replacement
is atomic. Rendering uses a shared two-worker executor and deduplicates matching
active/completed work.

Interrupted jobs become retryable errors after restart rather than resuming
checkpoints. YouTube download status is in memory. Stopping AI Edit halts future
browser orchestration; an accepted backend job may still complete.

## Shared rendering contract

`GET /api/render-settings` exposes the authoritative caption/filter/output
contract. Contract version 3 includes source-preserving output defaults for new
projects and legacy portrait defaults for existing projects.

Output settings are passed unchanged through save, exact frame preview, and
render requests. Effective geometry accounts for rotation/sample aspect ratio
and even H.264 dimensions. Fit preserves the full frame; center-crop trims edges;
neither stretches it.

Render identity includes output geometry, filters, captions, and the relevant
transcript content. Changing the look enables a new render; completed artifacts
retain their recorded appearance. UI downloads follow exact returned clip IDs,
not an assumed latest candidate output.

## AI boundaries

Provider profiles select the model, endpoint, and credentials. Model results are
schema-validated before persistence. Invalid timing, scores, or frame evidence
must not become clip records.

Visual analysis normally sends ten timestamped bounded JPEG samples through
OpenAI-compatible multimodal requests or native Claude image blocks. It does
not send audio or provide full-video/beat understanding.

Project chat uses allowlisted technical metadata and optional speech context.
Chat and visual recommendations do not mutate candidates or editor settings.
Analysis jobs persist candidates; AI Edit applies recommendations only through
the user's reviewed workflow choice.

See [AI Edit integration](ai-edit-mode.md) for detailed contracts.
