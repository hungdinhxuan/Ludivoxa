# Changelog

All notable Ludivoxa changes are recorded here. This project uses version tags
such as `v1.0.0`; numbering is independent of the upstream Interpreter project.

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
