# AI Edit: project chat and visual editing

## Workspace integration

`frontend/src/components/AiEditMode.tsx` receives the current project and editor
settings. Workspace retains its mounted tree when switching Manual / AI Edit, so
automation and the conversation survive mode switches.

```tsx
<AiEditMode
  detail={detail}
  active={editMode === 'ai'}
  busy={processing || rendering || !!pending || selectionBusy || savingControls || controlsOpen || suggestionsLoading}
  captionSettings={captionSettings}
  videoFilters={videoFilters}
  outputSettings={outputSettings}
  onBusyChange={setAiBusy}
  onRefresh={refresh}
  onSelectionSaved={() => setSelectionOverride(null)}
  onRenderQueued={(candidateId, clipId) => { /* track this render artifact */ }}
  onManual={() => setEditMode('manual')}
  onOpenControls={() => setControlsOpen(true)}
/>
```

**`outputSettings: OutputSettings` is required.** Pass the full current Workspace
snapshot: `{mode: 'source' | 'manual' | 'ai', aspectRatio, fit: 'contain' | 'crop',
maxDimension}`. There is no `supportsOutputSettings` prop. Source-preserve is the
default supplied by Workspace; AI Edit does not impose a portrait crop or infer
an output ratio from the destination selector.

`onBusyChange` locks conflicting Manual mutations during the automated pipeline.
`onRenderQueued` tracks the exact returned artifact ID, including server-reused
completed renders. `onSelectionSaved` clears Workspace's optimistic selection;
`onRefresh` reloads project state and returns `Promise<boolean>`. Mount per
project (or key the component by project ID).

## API contracts

AI Edit HTTP functions are in `frontend/src/api/aiEdit.ts`; shared media/render
requests are in `frontend/src/api/client.ts`:

- **Project chat:** `POST /api/projects/:id/ai-edit/chat` with
  `{message, messages: [{role: 'user' | 'guide' | 'model', text}], brief}`.
  `message` is the current turn; `messages` is preceding conversation history.
  The response is `{message, model, basis: 'metadata' | 'transcript'}`.
- **Visual recommendation preview:**
  `POST /api/projects/:id/ai-edit/recommendations` with `{brief}`. Returns
  `VisualRecommendation`: `aspectRatio: {mode: 'preserve' | 'crop' | 'pad', ratio}`,
  partial `videoFilters` and `captionSettings`, suggested `candidates`,
  `rationale`, `analysisBasis: 'sampled-frames'`, source dimensions/duration and
  audio/video availability, `sampledFrameTimes`, `contextNotice`, and `model`.
- **Clip analysis job:** `POST /api/projects/:id/analyze` with
  `{mode: 'visual' | 'transcript', brief, provider?}`. The runner uses the active
  configured provider. Successful visual analysis persists its recommendation
  under `GET /api/projects/:id` → `project.visualAnalysis`, and replaces the
  candidate records. The runner selects those persisted candidates, rather than
  trying to render the preview recommendation's raw time ranges.

Chat sends a shared formatted brief containing audience, takeaway, notes, all
destination/count/duration/caption choices, analysis mode, and reuse preference.
Audience and goal each allow 1,000 characters; notes allow 2,000. Chat,
recommendations, and both analysis modes share a 5,000-character combined brief
limit, including the formatted labels and editing options. Overflowing chat
additions show a correction message and retain the unsent turn and current
brief. Accepted briefs are passed to the model intact.
The starter greeting and question labels are local and explicitly identified as
the **starter guide**. Send calls the project-chat endpoint. Only endpoint
responses become **Model reply · model-name** entries, with their returned
context basis. Failures appear as errors and do not generate synthetic replies.
Chat context is metadata/transcript; sampled-frame context is shown separately
on the visual recommendation card.

HTTP errors accept JSON `error`, `message`, or string `detail`, handle plain-text
errors, and convert gateway HTML to a concise status message. Displayed errors
are plain text and capped at 600 characters. Request signals support stopping
chat and visual recommendation requests.

## Reviewed automation

The default clip-analysis mode is **Visual · sampled video frames**. The optional
Transcript choice ranks spoken moments with the same brief.

1. **Optional transcription.** Visual video analysis with captions off does not request transcription.
   Audio-only sources use speech analysis and need transcription even with captions off.
   With captions requested and no transcript/audio exclusion, create a speech
   transcript. Existing speech is reused. Empty speech results or failed
   transcription on video continue to visual analysis with captions off; the
   activity timeline reports the actual no-speech/provider result. A monitoring
   failure does not advance while a transcription job is still active. An
   existing silent transcript is not repeatedly retranscribed.
2. **Analyze or reuse.** Reuse existing candidates by default. Otherwise queue
   visual or transcript analysis with the brief and await completion. If
   Transcript was chosen but speech is unavailable, use video-frame analysis.
   Visual recommendations work with music, silent video, and no transcript.
3. **Select.** Prefer candidates matching the requested duration, then score and
   rank; select up to the requested limit. Persist the complete selection and
   await success before rendering.
4. **Render.** Queue each selected candidate with the reviewed settings snapshot,
   then track the returned clip IDs until complete. Speech availability is
   checked before burning captions. Completed and partially successful exports
   remain previewable/downloadable.

### AI settings are an explicit plan choice

The compact review dialog contains **Use AI recommended settings**, unchecked
by default. Opening a recommendation or running visual analysis does not apply
it. Without this choice, render requests retain the full reviewed Workspace
caption/filter/output snapshot, including a source or manually selected ratio.

With this choice, a preview recommendation can be reviewed before starting;
completed visual analysis can then supply the latest persisted recommendation.
Both paths merge partial caption/filter choices into the full current settings
snapshot, retaining font, outline, shadow, background and chunking fields.
Captions off in the brief and missing speech take precedence over an AI-enabled
caption suggestion. Framing maps as follows, retaining reviewed `maxDimension`:

| Visual recommendation | Render output settings |
| --- | --- |
| `preserve` | `mode: source`, `aspectRatio: source`, `fit: contain` |
| `crop` | `mode: ai`, recommended ratio, `fit: crop` |
| `pad` | `mode: ai`, recommended ratio, `fit: contain` |

The recommendation card shows actual model name, rationale, sample count/times,
suggested moments, framing and context notice. A fetched recommendation is marked
stale after brief changes. Last persisted visual analysis remains visible after
reopening the project. Recommendations do not overwrite saved editor settings.

Reanalysis of existing candidates has the additional destructive acknowledgment
for replacing candidates, selections and rendered outputs. The plan also
requires approval of the selection update. Starting verifies the source,
transcript and candidate snapshot and rejects active jobs or a stale review.

## Lifecycle and verification

Briefs and the latest 20 conversation entries are saved in project-scoped
`sessionStorage` under `clipforge.ai-edit.v2.<projectId>`. Version 2 avoids
relabeling the MVP's simulated replies as connected-model responses. Plans and
automation are not automatically resumed after a reload.

**Stop automation** or leaving the project aborts browser sequencing and future
requests. Switching editing modes retains the run. Accepted backend jobs may
still finish. Project polling has a stage-status fallback for older aggregate
responses, two transient retries, and a 30-minute monitoring limit.

`frontend/tests/ai-edit.spec.ts` mocks the chat, recommendation, analysis and
render routes. Coverage includes endpoint-backed replies and complete briefs,
project isolation/Manual preservation, music/no-transcript visual automation,
no-speech and failed-transcription fallback, optional transcript mode, reviewed
ratio pass-through, opt-in latest visual settings, full caption merges, HTML and
bounded errors, replacement approval, selection/render failures, stop/unmount,
stale review, saved visual results, legacy status reads, completed artifact reuse,
and responsive light/dark layouts. These checks exercise frontend integration;
the mock suite does not invoke a live model or FFmpeg.

### Verification

Backend route tests cover project-scoped context, exact active profiles,
non-mutating advice, visual jobs without speech, bad response rollback, and
bounded request capacity. Actual model inference is separate from these
controlled transport tests. See [Contributing](../CONTRIBUTING.md) for the full
verification workflow.

Run from `frontend/`:

```sh
npm run build
npm run lint
npm run test:e2e -- tests/ai-edit.spec.ts
```

Normal browser prerequisites are installed with `npx playwright install --with-deps chromium`.
