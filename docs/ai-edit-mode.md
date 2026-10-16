# AI Edit: combined audio-and-visual editing

[Documentation hub](README.md) · [Audience selection](audience-selection.md) · [API reference](api.md)

## Workspace integration

`frontend/src/components/AiEditMode.tsx` receives the current project, shared
audience brief, and editor settings. Workspace keeps the component mounted when
switching Manual / AI Edit, so an approved automation run can continue while
the user reviews the source or rendered clips.

The AI Edit brief contains:

- intended audience and viewer takeaway;
- topics, tone, and exclusions;
- destination, clip count, preferred length, and caption preference.

Changing these fields only updates the project draft. A model request starts
only when the user asks for a recommendation or approves the automated plan.

## Recommendation API

`POST /api/projects/:id/ai-edit/recommendations` accepts `{brief,
audienceBrief}` and uses the active analysis profile. If a usable transcript is
available, the server first runs the bounded timed-transcript scout and critic.
The final image-capable request receives those verified spoken candidates,
their source text, the editing brief, and timestamped sampled frames. It makes
one final audio-and-visual decision about the clips, framing, captions, and
look. The raw audio and full video are never sent to the model endpoint.

If the active connection cannot inspect images, verified transcript analysis
still supplies clip recommendations and automatic analysis results. The card
labels that result as audio-transcript evidence and keeps neutral source
framing. A video-only request reports that the configured model needs image
capability.

The browser creates speech context before requesting a recommendation when
source audio is available. This also happens with captions off; clip selection
needs the spoken story independently of caption rendering.

Recommendations include the actual model, audience, rationale, source
metadata, sampled frame times, context notice, and versioned brief provenance.
They are previews: they do not mutate candidates or editor settings.

## Reviewed automation

The normal analysis mode is `auto`:

1. Create timed speech context when audio is available and the plan needs fresh
   analysis or captions.
2. Send the full timed transcript through the source-bound scout/critic.
3. If video is available, inspect sampled frames and let the AI select from the
   verified audio candidates using both evidence channels. With no speech, use
   conservative visual selection. With an audio-only source, use the reviewed
   transcript candidates.
4. Save the returned candidate selection and wait for that write to succeed.
5. Queue renders with the reviewed caption, filter, and output snapshot.

The plan requires explicit approval. Reanalysis of existing candidates also
requires confirmation because it replaces their selections and rendered
artifacts. **Use AI recommended settings** is separately opt-in; requesting a
recommendation never silently changes the project look.

The browser owns sequencing and polling. Stopping automation prevents future
requests but cannot cancel a server job that was already queued. Failed
analysis preserves the previous candidates and exposes a retry action.

## Connection failures

Provider/model negotiation keeps the selected profile fixed while retrying
compatible request formats. Unsupported strict JSON schema falls back to JSON
object mode; supported model, endpoint, and credentials do not change. Errors
are bounded and actionable, for example authentication, quota, endpoint, model,
image-capability, or timeout guidance. Provider response bodies, source paths,
image data, and credentials are not returned to the browser.

## Storage and verification

The shared audience brief is stored per project in local storage. The latest
recommendation is persisted with the project only after a successful analysis
job; its provenance fingerprint prevents stale settings from being applied
after the brief changes.

Backend tests cover transcript evidence, multimodal transport negotiation,
visual validation, rollback, provider redaction, and profile selection.
Frontend tests cover the combined recommendation payload, absence of the old
project-chat surface, reviewed automation, selection/render sequencing, and
responsive AI Edit layouts.
