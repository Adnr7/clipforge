# Credits and dependencies

## Core technology

ClipForge uses these projects rather than bundling their source or binaries:

- [Python](https://www.python.org/) and its standard-library SQLite interface.
- [Flask](https://flask.palletsprojects.com/),
  [Flask-CORS](https://github.com/corydolphin/flask-cors),
  [Waitress](https://github.com/Pylons/waitress),
  [Requests](https://requests.readthedocs.io/), and
  [python-dotenv](https://github.com/theskumar/python-dotenv).
- [pywebview](https://pywebview.flowrl.com/) for the optional native window.
- [React](https://react.dev/), [TypeScript](https://www.typescriptlang.org/),
  [Vite](https://vite.dev/), and [Lucide](https://lucide.dev/).
- [FFmpeg](https://ffmpeg.org/) and FFprobe for media processing.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) for YouTube imports.
- [OpenAI Whisper](https://github.com/openai/whisper) for optional local speech
  transcription.
- [pytest](https://pytest.org/), [Playwright](https://playwright.dev/), and
  [Oxlint](https://oxc.rs/docs/guide/usage/linter) for development checks.

ClipForge's MIT license applies to its own code. Dependencies, FFmpeg builds,
external services, and model weights have their own licenses and terms. A future
binary distribution must include the notices required by what it bundles.

## Product and design references

Public workflows from [AutoShorts](https://github.com/JayWebtech/autoshorts),
[ClippyMe](https://github.com/fralapo/clippyme),
[ClipMint](https://github.com/kleZ799/clipmint), and
[OpenShorts](https://github.com/mutonby/openshorts) informed product research.
They are references, not ClipForge runtime dependencies.

The palette work consulted
[Radix's semantic color scales](https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale)
and the [Nord palette](https://www.nordtheme.com/docs/colors-and-palettes/).
