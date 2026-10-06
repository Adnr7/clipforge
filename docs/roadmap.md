# Roadmap

This is a direction for discussion, not a release schedule or promise. Open an
issue before starting a substantial feature so scope and approach can be agreed.

## Current foundation

- Local video/audio projects, optional transcription, speech/visual highlights,
  and manual cuts.
- Source/manual output formats, captions, video adjustments, and MP4/ZIP export.
- Guided AI Edit, provider profiles, and Dark/Light/Nord themes.
- Shared audience briefs, timed speech scout/critic selection, locally computed
  editorial scores, quality gates, and persisted source evidence.
- Public API/development/testing documentation and reproducible UI screenshots.
- Backend synthetic-media tests and frontend browser workflows.

## Areas to improve

- More robust structured-model response handling and provider compatibility.
- Human-labeled audience relevance evaluations across subjects and local/cloud
  models; stronger semantic duplicate detection and model-quality guidance.
- Native desktop verification and repeatable installers for supported platforms.
- Clearer recovery and cancellation for long-running jobs.
- Accessibility, keyboard workflows, and editor polish.
- Easier local-model setup and performance guidance.

## Potential future features

- Subtitle file export such as SRT/VTT.
- Timeline-based boundary editing.
- Face/active-speaker-aware reframing.
- Resumable processing checkpoints and persistent download jobs.
- Optional platform publishing integrations.

These capabilities are not currently implemented. Musical-beat analysis would
require audio-aware analysis beyond the existing sampled-frame service.
