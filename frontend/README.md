# ClipForge frontend

This directory contains the React 19 + TypeScript UI served by the Flask
backend. The application is built with Vite and uses Lucide for interface
icons. The production build is emitted to `dist/`, which the backend serves as
its local web UI.

## Local commands

```bash
npm ci
npm run dev       # Vite development server with HMR
npm run build     # Type-check and create the backend-served build
npm run lint
npm run test:e2e  # Playwright against the production preview
npm run docs:screenshots # Generate public UI screenshots with synthetic fixtures
```

Keep API behavior in `src/api/` aligned with Flask routes under `backend/`.
Install browser prerequisites with `npx playwright install --with-deps chromium`
before running the browser suite. Build first: the tests use the production
preview rather than the development server.

See the [root README](../README.md) for application setup,
[Contributing](../CONTRIBUTING.md) for development guidance, and
[UI review](../docs/ui-review.md) for screenshot expectations.

The [development guide](../docs/development.md) explains ports and state ownership.
Keep audience state in Workspace via `src/api/audience.ts`; AI options/chat live
in the project session draft. `src/api/aiEdit.ts` owns reviewed browser sequencing,
not backend cancellation. Preview/download UI must follow the exact returned
artifact ID and its recorded settings.

See [Testing](../docs/testing.md#documentation-screenshots) for the screenshot
script's FFmpeg/Chromium requirements and generated output paths.
