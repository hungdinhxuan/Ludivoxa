# Changelog

All notable Ludivoxa changes are recorded here. This project uses version tags
such as `v1.0.0`; numbering is independent of the upstream Interpreter project.

## [1.1.2] - 2026-11-16

Stable app-only update, including the RC's speech and Remote Desktop fixes.

- Reset the audio repeat guard once a positioned dialogue box has genuinely
  cleared for a sustained scene break. Identical dialogue in a new scene can
  play again without interrupting current speech. Brief blanks, OCR misses and
  unpositioned OCR results retain duplicate suppression.
- Wait while the Windows session is disconnected before starting native
  Graphics Capture, and resume when the session becomes active.
- Qualify the complete OCR/local translation/native speech pipeline with
  60 controlled scenes across Gemma and Qwen. These checks do not constitute
  a new TTFA benchmark or exhaustive live gameplay validation.
- Publish a stable update for v1.1.1 and v1.1.2-rc.1 installations, preserving
  existing models, runtime and configuration.

## [1.1.2-rc.1] - 2026-11-16

This prerelease is an app-only update for an existing v1.1.1 installation.
Install it manually; the stable updater does not offer release candidates.

- Confirm stable visible glyphs before the first speech request as well as
  after playback, so oscillating/missing OCR cannot indefinitely reset TTS.
  Recognize white completion arrows without ignoring real changed text;
  add output endpoint and waveform-level diagnostics for intermittent silence.
- Recover Windows capture across console/Remote Desktop session changes using
  window-only PrintWindow rendering in RDP; recreate Graphics Capture on return.
  Run initialization/recovery off the UI thread, stop native capture explicitly,
  discard stale callbacks, and report missing window frames while retrying.
- Support RC build/version metadata and upgrading an RC installation to its
  corresponding stable release; remote prereleases remain excluded from the updater.

## [1.1.1] - 2026-11-15

- Order horizontal OCR words by line and x position, and remember line-wide
  pixel snapshots so detector split/merge jitter cannot replay unchanged text.
  Include the vertical shadow of bouncing GBA continuation arrows in masking;
  keep genuine glyph/number changes and vertical Japanese reading order.
- Wait adaptively for typewriter dialogue and visible glyph growth, even when
  OCR still returns the same prefix. Learn GBA completion cursors, normalize
  cursor-backed trailing T/V/Y artifacts and retain a bounded cue-less fallback.
  In TTS-only mode, translate only new text after a completed carried-over line.
- Keep completed speech remembered when the OCR editor is cancelled or saved
  unchanged; interrupted speech and actual setting changes can be read anew.
- Exclude strict source-word extensions/truncations from fuzzy translation
  cache reuse so newly rendered words do not inherit an older partial translation.

- Restore translated-utterance duplicate suppression in streaming speech. OCR
  aliases that reuse the same cached translation no longer replay it, including
  while queued and across different sentence boundaries or buffered playback.
  Preserve divergent/shorter translations and explicit capture/voice resets.
- Prevent fuzzy translation-cache matches when numbers or negation markers
  differ, so meaningful dialogue changes are translated again.

- Suppress repeated speech from pixel-font OCR variants such as Pokemon/Pokamon
  when the displayed glyph geometry is unchanged. Ignore animated box margins,
  tolerate flat-panel shading, antialiasing and up to two pixels of translation,
  and cache four verified image states. Preserve real glyph/number changes,
  additional dialogue, typewriter settling and selected-box capture boundaries.

- Validate native speech output formats, enable shared WASAPI conversion and
  support mono/surround channel layouts to avoid `Invalid number of channels`
  (`PaErrorCode -9998`). If an automatically selected WASAPI format is unsupported,
  retain the user's original output endpoint. Surround/LFE channels stay silent.

## [1.1.0] - 2026-11-15

- Add an Updates page with opt-in startup checks, release notes, background
  downloads/progress/cancel and explicit verified installation/restart.
- Check and download public GitHub releases without a token or Git sign-in.
- Prefer app-only update installers that preserve configuration and local
  models/runtime; retain verified full offline installer fallback for older releases.
- Remove obsolete application version metadata during upgrades so the UI and
  updater report the installed version correctly.
- Preserve the source's tone, emotion, politeness, profanity and level of
  explicitness in the default translation prompt, without softening or intensifying it.
- Deliver complete translated sentences from Ollama NDJSON/OpenAI-compatible SSE
  to speech before the full response finishes. Cache/context commit only after
  successful completion; truncated or malformed streams do not trigger fallback.
- Play Nano audio incrementally through one native output stream with two queued
  audio chunks, one synthesis worker and the existing two-thread CPU limit.
- Prepare short, punctuated TTS-only candidates during the existing settle
  window, without speaking or committing speculative OCR to translation context.
- Prefer WASAPI when it uniquely matches the same Windows output endpoint;
  request a 60 ms device buffer. Preserve buffered callback playback for ASIO/WDM-KS.
- Preserve source stability, duplicate suppression, current-line completion,
  latest-pending dialogue and capture-generation cancellation.
- Add streaming/context/cancellation/device tests and full native streaming
  playback under Qt stalls. See [measurements](docs/PERFORMANCE.md).

## [1.0.0] - 2026-11-14

Initial Ludivoxa release, based on Interpreter 2.23.3.

### Added

- Offline English/Japanese PaddleOCR with local models, automatic source-language
  detection and manual selection. The English profile prioritizes game speed.
- **Only selected box** mode: draw, move or resize one translation area per
  source, use percentage fields or a bottom-dialogue preset, and save it per game.
- Existing `LLMTranslator` integration with local Ollama and configurable
  OpenAI-compatible/LAN endpoints; Vietnamese is the default target language.
- Configurable prompts and translation context, without automatic cloud fallback.
- Vietnamese automatic speech with Piper and VieNeu Nano presets, optional local
  reference-audio cloning, and Overlay only / TTS only / Overlay + TTS modes.
- Full offline Windows x64 installer with OCR/TTS models, Ollama and HauhauCS
  Qwen3 4B Aggressive Q4_K_M weights.
- Managed private Ollama: the final app instance closing stops its server and
  runners. Windows process ownership also covers forced app termination.
- Ludivoxa branding, version 1.0.0, the `ludivoxa` CLI alias, user/build/release
  documentation, checksummed release parts and preserved upstream attribution.

### Improved

- CPU OCR runs in a persistent isolated process with bounded CPU threads.
- Official ONNX exports are used when provisioned; recent identical frames are
  cached and unchanged areas avoid unnecessary recognition work.
- The capture worker keeps only the latest pending frame. Preview painting is
  limited independently of processing, and selected-box mode crops before OCR.
- Source settling, cursor handling and visual comparisons suppress duplicate
  translation/speech caused by OCR spelling/layout jitter and minor panel shading.
- Buffered native audio playback, voice/model caches, bounded prefetch and
  eight-step Nano inference reduce speech preparation work.
- Obsolete Nano synthesis is cancelled between chunks without playing partial
  audio. Reversions to the translation already playing are not queued again.

### Fixed

- Duplicate reads when OCR alternated between variants of the same dialogue.
- Detection-box changes between crop and full scans causing false new dialogue.
- Decorative symbols being pronounced as unwanted Vietnamese words.
- Audio stalls tied to UI work and unsupported blocking audio backends.
- Private Ollama/model processes surviving application exit.
- Developer API keys, prompts, game profiles and device names leaking into fresh
  installer defaults; release packages now use a public configuration template.
- Redundant runtime ZIP archives and stale PID files entering installer payloads.

### Compatibility and limitations

- MeikiOCR, Sugoi V4, owocr, capture sources and existing configuration locations
  remain available. `interpreter-v2` and `Interpreter.exe` remain compatibility
  names; the product and release name is Ludivoxa.
- This Windows release focuses on English/Japanese OCR and Vietnamese speech.
  Other LLM target languages can be displayed; bundled TTS is Vietnamese-only.
- Recognition quality and latency depend on font, image size, hardware and text
  length. Long paragraphs are still fully synthesized before playback; pauses
  in typewriter animation can still appear to be separate dialogue.
- External/LAN servers are not shut down by the app. A private model reloads
  after restarting the app because its runtime is stopped on exit.
- Release builds are unsigned. Live game FPS, a separate LAN GPU host and
  non-Windows GUI/capture behavior were not verified for this release.

[1.0.0]: https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.0.0
[1.1.0]: https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.1.0
[1.1.1]: https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.1.1
