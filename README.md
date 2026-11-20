# Ludivoxa

Offline game OCR, local LLM translation and Vietnamese speech.

[Tiếng Việt](README.vi.md) · [User guide](docs/USER_GUIDE.md) ·
[Changelog](CHANGELOG.md) · [Releases](https://github.com/hungdinhxuan/Ludivoxa/releases)

Ludivoxa extends [Interpreter](https://github.com/bquenin/interpreter), retaining
its capture → OCR → translation → overlay pipeline and Qt interface. This fork
starts at **v1.0.0**; see [attribution and licenses](NOTICE.md).

**Latest stable [v1.4.0](docs/releases/v1.4.0.md)** adds smaller Windows updates
for matching installations. For a new installation, start with the full offline
v1.1.1 package, then apply the app update.

## Features

- Offline English/Japanese PaddleOCR with a fast English profile, automatic
  source detection and a manual language fallback.
- Select multiple dialogue boxes per game. OCR reads the selected pixels in
  one pass. The full-window and exclusion options remain available.
- Reuse of the existing LLM translator with local Ollama or configurable
  OpenAI-compatible/LAN endpoints, prompts and translation context.
- Vietnamese translation by default; other LLM target languages remain available.
- Overlay only, automatic Vietnamese TTS only, or both.
- Eleven VieNeu Nano presets, optional local reference-audio cloning, and the
  existing 67 Piper speaker choices in the full Windows package.
- Assign a voice preset to each speaker name per game. The app reads the name
  in a separate OCR area and uses the default voice for unassigned speakers.
  Use names and optional gender labels to guide Vietnamese forms of address.
  TTS reads the dialogue itself.
- Background OCR/speech, bounded frame/prefetch queues, cached models/results,
  visual duplicate suppression and streaming native audio for Nano speech.
- Private bundled Ollama stops with the last app instance, including forced
  termination of a running app. External/local shared and LAN servers are retained.
- Existing MeikiOCR, Sugoi V4, owocr and capture backends remain available.
- An Updates page with opt-in startup checks,
  verified background downloads and explicit install/restart, with app-only
  update packages that keep local models and settings.

## TTFA benchmarks by version

**TTFA (time to first audio)** runs from a complete saved frame becoming
available to the first matching speech observed in Windows audio loopback.
It includes OCR, text stabilization, translation, synthesis and output delay.
Values below are **medians of three warm trials per case**, in milliseconds;
lower is faster.

| Version / measured profile | Short game dialogue | Two English sentences (synthetic) | Long game paragraph |
| --- | ---: | ---: | ---: |
| v1.0.0 — buffered baseline | 1,413.6 ms | 1,743.6 ms | 4,465.2 ms |
| v1.1.0 — streaming profile¹ | 912.6 ms | 1,013.6 ms | 1,436.9 ms |
| Historical reduction versus v1.0.0 | **35.4%** | **41.9%** | **67.8%** |
| v1.1.0 — released tag² | 896.1 ms | 1,038.6 ms | 1,439.5 ms |
| v1.1.1 — released tag² | 888.0 ms | 971.2 ms | 1,433.6 ms |
| v1.1.2-rc.1 — released tag² | 866.5 ms | 849.3 ms | 1,364.3 ms |
| v1.1.2 stable — released tag² | **867.7 ms** | **966.3 ms** | **1,355.9 ms** |
| v1.1.3 stable | Not measured | Not measured | Not measured |
| v1.2.0 stable | Separate game test below | Not measured | Not measured |
| v1.3.0 stable | Separate frame replay below | Separate frame replay below | Not measured |
| v1.3.1 stable | Synthetic short replay below | **1,014.42 ms** in frame replay | Not measured |
| v1.4.0 stable | Synthetic two-sentence replay | **1,108.41 ms** in frame replay | Not measured |

Measured on Windows 11, Core i7-13700F and RTX 4090 24 GB, using the same saved
dialogue crops, English OCR → Vietnamese, local HauhauCS Qwen3 4B Q4_K_M,
three seeded context pairs, and VieNeu Nano speaker 7 at eight steps/two CPU
threads. No previous speech was queued. The comparison includes the move
from buffered/MME output to streaming/WASAPI output on the same speaker endpoint.

¹ The streaming measurements were collected during v1.1.0 development, before
the final prompt/updater changes; the published installer was not separately
re-benchmarked. These trials exclude model loading, native capture phase,
game typewriter reveal and waiting for earlier speech. They do not measure
live game FPS, acoustic speaker latency or p95 latency.

² Measured on 2026-11-16 from the exact release-tag sources in an active
Windows console session, with unrelated audio paused: **36 valid loopback
trials**, three per input/tag. All used streaming/shared WASAPI at 48 kHz,
the same Qwen/Nano profile, Ollama context 2048 and the per-tag default prompt.
Waveform correlations were 0.958–1.000. These are warm, complete-frame results;
small differences across three trials do not establish a version speed ranking.
The historical percentage row applies only to the first two historical profiles.
See the [tagged-run method](docs/PERFORMANCE.md#tagged-version-loopback-ttfa)
and [individual samples](docs/benchmarks/ttfa.json). Installers were not
independently benchmarked.

**Tagged-version measurements, 2026-11-16 — frame to native PCM callback.**
The three later versions have now been measured with the same saved inputs,
Qwen/Nano settings and WDM-KS output. Each value is a median of three warm
trials. v1.1.0 was rerun on this output path as a control.

| Tagged source / WDM-KS | Short game dialogue | Two English sentences (synthetic) | Long game paragraph |
| --- | ---: | ---: | ---: |
| v1.1.0 — control | 3,914.3 ms | 4,448.3 ms | 15,644.1 ms |
| v1.1.1 | 3,515.8 ms | 4,450.3 ms | 15,404.5 ms |
| v1.1.2-rc.1 | 3,984.6 ms | 4,240.7 ms | 15,543.2 ms |
| v1.1.2 stable | 3,987.0 ms | 4,332.1 ms | 15,585.3 ms |

**These are driver-side observations, not loopback TTFA.** The Windows session
was disconnected and exposed no WASAPI loopback endpoint. Production WDM-KS
playback buffers the complete synthesized utterance, including all streamed
chunks, before output; its roughly 61 ms device buffer is only part of that wait.
The v1.1.0 control shows the same long wait on this path. Do not compare these
numbers directly with the earlier streaming/WASAPI table or infer small version
speed differences from three trials. The active-console loopback results are
now available in the first table; this earlier disconnected-session run is
retained as a separate output-path measurement. See the
[method and play-request timings](docs/PERFORMANCE.md#tagged-version-native-callback-audit)
and [all 36 timing samples](docs/benchmarks/ttfa-native-callback.json).

v1.1.1 fixes repeated speech from OCR word-box jitter, animated dialogue
cursors and streamed translation aliases, and negotiates supported audio
channel formats. A controlled replay with real OCR on ten saved GBA images
reduced speech requests from 10 to 1; it did not measure TTFA or synthesize
audio. Revealing text now waits adaptively, so earlier TTFA results are not
reused as measurements of this version. See the [patch validation](docs/PERFORMANCE.md#v111-live-gba-repeat-reproduction).

See [measurement details and reproduction](docs/PERFORMANCE.md#v110-streaming-profile)
and the [public timing samples](docs/benchmarks/ttfa.json). New versions will
get new rows when comparable measurements are available; different observation
methods are reported separately.

**v1.2.0 game test:** Character voices on, local Gemma 4 E4B Q4_K_M and Nano TTS.
Replaying four complete game lines with the full-window exclusions gave a
median of **1.23 s** over 12 WASAPI loopback measurements. In a separate
six-pair test of the same short line, a selected dialogue box reduced median
paired TTFA by **72 ms (7.5%)**, with a median of **0.82 s** inside the box.
These tests measured the release candidate code on saved game frames. Game
text reveal and earlier queued speech are outside the measurement. See the
[v1.2.0 notes](docs/releases/v1.2.0.md) for the setup and limits.

**v1.3.0 Windows frame replay.** With the same Gemma and VieNeu settings,
median TTFA over four complete-frame pairs was **1.20 s with timer polling**
and **0.95 s with frame notifications**, a median paired saving of **209 ms**.
The replay uses synthetic dialogue and measures first matching speech via
WASAPI. See [the individual results and scope](docs/PERFORMANCE.md#v130-windows-frame-replay).

**v1.3.1 Windows frame replay.** Median TTFA was **897.24 ms** for a short
synthetic dialogue and **1,014.42 ms** for two English sentences, with three
WASAPI trials each. [Individual trials and setup](docs/PERFORMANCE.md#v131-windows-frame-replay)

**v1.4.0 Windows frame replay.** Median TTFA was **1,028.81 ms** for a short
dialogue and **1,108.41 ms** for two sentences. [Trials and test conditions](docs/PERFORMANCE.md#v140-windows-frame-replay)

## Install on Windows

The packaged release targets **Windows x64**. Download its setup EXE and **all
three BIN files** from [the full v1.1.1 release](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.1.1),
place them in one folder and run the EXE. Compare the files with `SHA256SUMS.txt`.
The complete offline download is approximately 4.9 GB, with about 6.4 GB installed.

Python, English/Japanese OCR models, Vietnamese speech, Ollama 0.40.16 and
HauhauCS Qwen3 4B Aggressive Q4_K_M are included. No separate model download is
needed for that profile. The installer preserves an existing `config.yml`.

Then open **Updates** to install v1.4.0 (also supported from v1.1.2rc1).
To download the smaller v1.4.0 patch from an intact v1.3.1 installation, run
the **Delta-from-1.3.1** installer manually. For v1.0.0 or a standard manual
update, run **Ludivoxa-1.4.0-App-Update.exe** from
[v1.4.0](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.4.0) and select the existing
installation directory. This smaller package preserves your models, runtime
and settings. Public release checks and downloads need no GitHub token or sign-in.

GitHub's automatic **Source code** archives do not include models or runtimes.
The Windows build is unsigned. Executable/config compatibility names such as
`Interpreter.exe` and `.interpreter` are retained from upstream.

## First dialogue

1. Open your game and Ludivoxa; select the game window and **Start Capture**.
2. Open **Configure OCR** → **Scan: Only selected box**.
3. Draw the dialogue rectangle and press **OK**. It is saved per source.
4. Choose **Overlay only**, **TTS only** or **Overlay + TTS**.

Use **English** with `en_PP-OCRv5_mobile_rec` for English games. For Japanese or
mixed English/Japanese, select **Japanese/Auto** and `PP-OCRv5_mobile_rec`.
**F8** toggles the overlay without interrupting speech; **Stop reading** cancels
audio. Bundled speech is Vietnamese-only; choose Overlay only for other targets.

The default private Ollama address is `http://127.0.0.1:11435`. For another local
server use its address/model, for example `http://127.0.0.1:11434`; LAN Ollama and
OpenAI-compatible `/v1` endpoints are also supported. The offline profile has no
automatic cloud fallback. Restarting the app reloads its private model after
shutdown. Detailed setup is in the [user guide](docs/USER_GUIDE.md).

## Develop and build

Python 3.11/3.12 and `uv` are used for source development. Copy
`config.example.yml` to `config.yml` and provision its local models **before**
running the offline source profile:

```powershell
uv sync --locked --extra paddle --extra tts --extra clone --group build
uv run ludivoxa
```

The compatibility command `interpreter-v2` still works. The internal Python
distribution/namespace and configuration locations remain unchanged. Legacy
root install scripts install upstream Interpreter from PyPI; use the Ludivoxa
release or [source instructions](docs/DEVELOPMENT.md) for this fork.

- [Development, provisioning and Windows builds](docs/DEVELOPMENT.md)
- [Release process and tagged artifacts](docs/RELEASING.md)
- [Measurements and limitations](docs/PERFORMANCE.md)
- [MIT application license](LICENSE) and [third-party attribution](NOTICE.md)

OCR and synthesis timings depend on text length, font, region size and hardware.
Version 1.1.0 starts Nano speech from completed audio chunks; the
in-place overlay and callback-only driver paths retain buffered playback. Recorded-frame
benchmarks do not establish live game FPS or guarantee latency for every scene.
