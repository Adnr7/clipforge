# Contributing to ClipForge

Thanks for helping improve ClipForge. Contributions can include bug reports,
documentation, focused fixes, tests, and features discussed in an issue.

[Development guide](docs/development.md) · [Architecture](docs/architecture.md) ·
[API reference](docs/api.md) · [Testing](docs/testing.md) · [Documentation hub](docs/README.md)

## Find a useful first contribution

| Interest | Good starting work | Read first |
| --- | --- | --- |
| Documentation | Reproduce setup steps, clarify a contract, improve alt text | README and documentation hub |
| UI/accessibility | Keyboard focus, long content, responsive controls | UI review and nearby Playwright scenarios |
| Media | A reproducible preview/output mismatch with synthetic footage | Shared render contract and geometry tests |
| AI quality | Source-grounded fixture exposing irrelevant or incomplete selection | Audience-selection method and evaluation guide |
| Reliability | A regression fixture for a stage error, rollback, or interrupted job | Architecture and pipeline tests |

## Getting started

1. Fork the repository and clone your fork.
2. Follow the [README setup instructions](README.md#getting-started), including
   Python, Node.js, FFmpeg/FFprobe, and the frontend build.
3. Install the backend development dependencies in your virtual environment:

   ```bash
   python -m pip install -r requirements-dev.txt
   ```

4. In `frontend/`, install browser-test prerequisites:

   ```bash
   npm ci
   npx playwright install --with-deps chromium
   ```

5. Create a branch for your change.

The optional `requirements-whisper.txt` installation is needed for real local
Whisper inference, not the controlled-provider test suite.

## Development workflow

Run `python main.py --web` from the repository root. In a second terminal, run
`npm run dev` from `frontend/`. Open http://localhost:5173 for hot reload.

Read the relevant routes, services, components, and nearby tests before editing.
[Architecture](docs/architecture.md) explains the boundaries. Flask owns the
API/persistence; React owns the interface; FFmpeg owns media processing.

Use a separate `DATA_DIR` for experiments that create projects. Prefer generated
synthetic media for tests. Do not include personal media, saved databases,
credentials, model downloads, or generated reports in a pull request.

The [development guide](docs/development.md#run-with-hot-reload) shows an isolated
`.dev-data` setup, ports, environment precedence, and where to find each feature.
Restart the backend after Python edits; the Vite interface hot-reloads separately.

## Checks

From the repository root with the virtual environment activated:

```bash
python -m pytest -q tests
```

FFmpeg and FFprobe must be on `PATH` for media coverage. The tests create their
own temporary files and use controlled model responses; cloud API keys are not
required.

From `frontend/`:

```bash
npm run build
npm run lint
npm run test:e2e
```

Start with focused checks while developing, then run the relevant suites before
opening a pull request. CI runs the full backend and frontend checks. Report
exact commands and outcomes; note any checks you could not run.

[Testing](docs/testing.md) maps feature areas to focused suites, explains fixture
boundaries, and documents traces and reproducible README screenshots. A passing
mock provider test is not evidence that a real model produces relevant clips.

For UI changes, follow [the UI review guide](docs/ui-review.md), including Dark,
Light, and Nord, mobile widths, long content, and loading/error states. Screenshots
or recordings in a pull request should use synthetic or otherwise shareable data.

## Change guidelines

- Keep changes focused and consistent with the existing structure and style.
- Discuss substantial new features or architectural changes in an issue first.
- Include meaningful regression coverage for behavior changes.
- Keep frontend API types and backend contracts aligned.
- Preserve shared preview/render geometry and exact returned artifact tracking.
- Treat model output as untrusted; preserve schema and source-bound validation.
- Keep audience relevance and complete payoff ahead of sensational filler; retain
  locally computed scores, quality gates, and visible evidence.
- Keep API keys write-only and provider errors redacted.
- Update user-facing documentation when setup, configuration, or behavior changes.
- Use `npm ci` with the committed lockfile; update it when changing dependencies.
- Keep generated files and local environments out of the source repository.

## Issues and pull requests

Use the bug-report or feature-request template. A useful bug report includes
reproduction steps, expected/actual behavior, operating system, app mode, and
relevant sanitized logs. For proposals, explain the user problem and intended
scope.

Keep pull requests small enough to review. Describe what changed, why, and how it
was checked. Include screenshots for visible UI changes. Maintainers may request
adjustments before merging.

A useful PR description answers **what user problem**, **what changed**, and
**how it was checked**. Mention API/default/migration changes explicitly, link
the relevant issue, and include before/after images when they clarify a visible
change. Keep screenshots reproducible with shareable data.

Contributions are made under the project's [MIT License](LICENSE). Follow the
[Code of Conduct](CODE_OF_CONDUCT.md), and report vulnerabilities through the
process in [SECURITY.md](SECURITY.md).
