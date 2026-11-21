# Changelog

All notable Ludivoxa changes are recorded here using version tags such as
`v1.0.0`.

## [2.0.0] - 2026-11-22

- Use Ludivoxa for the Python package, CLI, desktop entry, Windows executable,
  user-data directory and build tools.
- Upgrade earlier Windows installations through the standard installer.
  Existing settings, models and Ollama are carried over.

## [1.7.0] - 2026-11-21

- Add translation target presets for English, Simplified and Traditional
  Chinese, Japanese and Korean. Keep Vietnamese as the default and continue
  to accept custom target names.
- Use subtitles for non-Vietnamese LLM targets and restore the saved output
  mode when switching back to Vietnamese.
- Clear the previous subtitles and speech when the target changes during capture.

## [1.6.0] - 2026-11-21

- Add offline PaddleOCR profiles for Simplified and Traditional Chinese and
  Korean, selectable globally or per game. Only the selected recognizer runs.
- Bundle the Korean recognizer in Windows app updates. The Chinese profile
  reuses the existing PP-OCRv5 multilingual model.

## [1.5.0] - 2026-11-21

- Review, replay, copy and retranslate recent dialogue from History, including
  a floating History view for Windows game windows.
- Set per-game source language, prompt and ignored OCR phrases. Inspect capture,
  OCR, translation and audio state in Diagnostics.
- Optionally clear an old overlay when dialogue changes while keeping completed
  translations in History. Adjust speech volume independently for automatic
  playback, voice previews and History replay.

## [1.4.0] - 2026-11-20

- Download smaller Windows updates when a release supplies a delta for the
  installed version and its app files match. Offer the standard installer when
  the delta is unavailable or fails verification.

## [1.3.1] - 2026-11-20

- Keep already spoken dialogue from replaying when name OCR alternates among
  unconfigured speaker labels. Two configured characters can still speak the
  same words using their own voices.
- Use the speaker label only for voice routing and translation context. Strip
  a matching label echoed at the start of an LLM reply before displaying or
  speaking it, including when the label spans streaming packets. Keep names
  that occur inside the dialogue.

## [1.3.0] - 2026-11-19

- Select and edit multiple OCR boxes per source. OCR reads the selected pixels
  in one pass and retains the original positions for overlays.
- Label the bundled Nano voice presets. Allow per-character gender overrides
  and use the recognized speaker and gender for translation context, including
  in overlay-only mode. Keep translations and speculative previews scoped to
  the speaker so similar lines retain the right forms of address.
- Dispatch Windows window frames to OCR as soon as capture delivers them.
  Confirm speech on a fresh frame after the text stabilization window and
  retain a fallback for static windows. Skip image comparisons when the
  recognized dialogue already matches the last accepted line.

## [1.2.0] - 2026-11-18

- Add experimental per-game character voices: select a speaker-name area,
  assign existing presets once, and recognize the name offline on the OCR
  worker. Unknown names/narration keep the default voice or clone. Bind each
  utterance to its preset without interrupting speech, reloading the model,
  or adding an LLM request. Name OCR uses its own crop even
  outside the dialogue box/exclusions; its text stays out of translation.
  The editor follows the active capture or the selected source when idle.
- Recognize small detached completion arrows by their actual triangle pixels,
  including when OCR calls them X, I or a digit. Do not add/remove that fake
  character from the spoken source and replay the whole dialogue; retain real
  letters and numbers that are not cursor shapes.
- Keep outlined dialogue from being spoken again when the game background
  animates and OCR changes a character. Compare exact foreground geometry
  independently of the dim backdrop, while retaining strict checks for new
  words, numbers and real glyph changes. Ignore standalone OCR punctuation
  when remembering the dialogue image.
- Record hashed source/translation identities, preset IDs and playback starts at normal
  log level so repeat reports can be traced without logging dialogue text.

## [1.1.3] - 2026-11-17

Stable app-only patch for Remote Desktop audio and OCR configuration controls.

- Keep Configure OCR in the main header and its Clear/OK/Cancel actions at the top
  of a screen-sized, scrollable dialog so they remain reachable on small RDP desktops.
- Use a bounded native callback for WASAPI speech instead of blocking writes,
  which could hang in an RDP session and fill the synthesis queue indefinitely.
  Preserve incremental playback, detect stopped callbacks, retain responsive
  cancellation and invalidate the endpoint after a driver failure.
- Follow Windows default audio output changes between console and Remote
  Desktop. Refresh PortAudio's cached devices on the audio writer before the
  next utterance, retaining the current line and avoiding per-line refreshes.
  Serialize refresh with native stream cleanup and report refresh failures
  instead of playing through a stale physical speaker.

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
- Post-release validation: publish 36 tagged-source WASAPI loopback trials.
  The v1.1.2 Qwen/Nano warm medians are 867.7/966.3/1,355.9 ms for short,
  two-sentence and long inputs. Release notes link the samples and limitations;
  the application tag, installer and checksums remain unchanged.

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

- Suppress repeated speech from one-letter pixel-font OCR variants
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

Initial Ludivoxa release.

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

- MeikiOCR, Sugoi V4, owocr and the existing capture sources remained available.
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
[1.6.0]: https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.6.0
[2.0.0]: https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v2.0.0
[1.7.0]: https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.7.0
