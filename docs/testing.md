# Testing and verification

[Documentation hub](README.md) · [Development](development.md) · [UI review](ui-review.md)

## What each layer proves

| Layer | Exercises | Does not establish |
| --- | --- | --- |
| Backend unit/contracts | Validators, provider transports, model fixtures, settings and score arithmetic | Real model judgment |
| Backend integration | Real SQLite, claims/rollback/recovery, generated media, actual FFmpeg/FFprobe | Native WebView installation |
| Browser workflows | Built React UI, user actions, request payloads, themes, layouts, mocked jobs | Live cloud inference or real backend rendering |
| Manual live evaluation | A chosen provider/model, representative source, human review | Guaranteed reach or retention |

Cloud keys and local Whisper weights are not needed for automated fixture-based
tests. Media integration coverage needs FFmpeg/FFprobe and fonts on `PATH`.

## Full checks

```bash
# Repository root; virtual environment activated
python -m pip install -r requirements-dev.txt
python -B -m pytest -q --tb=short tests
```

```bash
# frontend/
npm ci
npx playwright install --with-deps chromium
npm run build
npm run lint
npm run test:e2e
```

Build before browser tests: Playwright starts Vite's production preview, not the
HMR development server. The config uses Chromium, one worker, port **5180**, and
a **60-second** per-test timeout including teardown.

## Focused checks

| Work area | Useful starting command |
| --- | --- |
| Audience/timed selection | `python -m pytest -q tests/test_audience_clip_selection.py` |
| Legacy LLM transports/parser | `python -m pytest -q tests/test_llm_service.py` |
| Visual schema/frame sampling | `python -m pytest -q tests/test_visual_analysis_service.py` |
| Pipeline claims and stage errors | `python -m pytest -q tests/test_pipeline_routes.py` |
| Chat and non-mutating advice | `python -m pytest -q tests/test_ai_edit_routes.py` |
| Manual cuts | `python -m pytest -q tests/test_manual_cuts.py` |
| Output/FFmpeg geometry | `python -m pytest -q tests/test_output_aspect_ratio.py tests/test_render_identity.py` |
| AI Edit browser workflows | `npm run test:e2e -- tests/ai-edit.spec.ts` |
| Manual/settings/themes | `npm run test:e2e -- tests/workflows.spec.ts` |
| Output ratio UI | `npm run test:e2e -- tests/output-aspect-ratio.spec.ts` |

Run Python commands at the repository root; npm commands in `frontend/`.
Filter a browser scenario with `--grep`, for example:

```bash
npm run test:e2e -- tests/ai-edit.spec.ts --grep 'audience brief is shared'
```

## Fixtures that catch real regressions

- Use pytest's temporary directory and the existing SQLite/deferred-worker
  fixtures. Assert persisted state after failures, not just HTTP status.
- Generate synthetic media rather than adding personal videos or large binaries.
- Queue controlled scout and critic responses separately. Check the prompt's unit
  IDs, actual quotes, frozen model/endpoint, locally recomputed score, and rejection.
- Include irrelevant-but-exciting moments, partial timing arrays, Unicode/symbol
  quotes, oversized windows, invalid booleans/nonfinite values, and no-good-clip results.
- Mock provider HTTP boundaries; never make a unit test spend real credits.
- Browser mocks should implement the backend shape, including `candidate.selection`
  and stage-specific jobs. Explicitly opt into candidate reuse when a scenario
  needs it; fresh audience analysis is the application default.
- Test returned clip IDs, settings changes, reuse identity, rollback, and partial
  exports. A generic latest-output assertion can miss a wrong artifact.

Meaningful regression tests exercise a failure or contract that can break; avoid
tests that merely repeat implementation details.

## Browser artifacts and UI review

Playwright saves failed screenshots and retained traces in
`frontend/test-results/`. CI uploads failure artifacts for seven days.

```bash
# frontend/; replace with the trace path from the failure output
npx playwright show-trace test-results/<scenario>/trace.zip
```

Runs may replace previous artifacts. Save any shareable evidence elsewhere before
rerunning. The [UI checklist](ui-review.md) covers Dark, Light, Nord, narrow
screens, long text, dialogs, keyboard access, zoom, loading and errors. Automated
no-overflow checks complement visual inspection.

## Documentation screenshots

The README uses actual UI captures with synthetic project/provider responses and
FFmpeg-generated chart footage. No backend is needed and no provider is called.

```bash
# frontend/; FFmpeg with libx264/drawtext and fonts on PATH
npm run build
npx playwright install --with-deps chromium
npm run docs:screenshots
```

The script `frontend/scripts/capture-docs.ts`:

1. Generates demo video/audio and a caption frame under ignored
   `frontend/test-results/docs-capture/`.
2. Starts its own built-interface preview on **5181**.
3. Intercepts API requests with synthetic data, including audience rubric/evidence.
4. Captures four PNGs into `docs/images/`, then closes its browser and server.

`FFMPEG_BIN` can select a specific executable. Review every PNG before committing,
keep alt text current, and document that model responses are fixtures. The banner
is a locally authored SVG, not an app screenshot. Screenshot regeneration changes
intentional public assets; test output and generated demo media remain ignored.

## Live editorial evaluation

Use the [audience-method evaluation checklist](audience-selection.md#evaluate-quality)
for a model change. Record provider/model, source kind, audience brief, expected
relevant ideas, rejected ideas, and actual boundaries. Verify complete setup and
payoff by watching the clip. Offline fixtures cannot establish that a model will
consistently understand a new subject or audience.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes, pull requests, and manual dispatch:

| Job | Environment | Checks |
| --- | --- | --- |
| Backend | Ubuntu 24.04, Python 3.10 and 3.12 | Install FFmpeg/fonts and runtime/dev dependencies; full pytest |
| Frontend | Ubuntu 24.04, Node 22 | `npm ci`, build, Oxlint, Chromium install, full Playwright |

CI does not install Whisper weights or run a paid provider, native GUI, or
packaged installer. Report exact checks and any untested runtime changes in a PR.
