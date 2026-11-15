# Ludivoxa

Offline game OCR, local LLM translation and Vietnamese speech.

[Tiếng Việt](README.vi.md) · [User guide](docs/USER_GUIDE.md) ·
[Changelog](CHANGELOG.md) · [Releases](https://github.com/hungdinhxuan/Ludivoxa/releases)

Ludivoxa extends [Interpreter](https://github.com/bquenin/interpreter), retaining
its capture → OCR → translation → overlay pipeline and Qt interface. This fork
starts at **v1.0.0**; see [attribution and licenses](NOTICE.md).

## Features

- Offline English/Japanese PaddleOCR with a fast English profile, automatic
  source detection and a manual language fallback.
- Select one dialogue box per game, move/resize it, or keep the existing
  full-window/exclusion workflow. OCR processes the selected crop.
- Reuse of the existing LLM translator with local Ollama or configurable
  OpenAI-compatible/LAN endpoints, prompts and translation context.
- Vietnamese translation by default; other LLM target languages remain available.
- Overlay only, automatic Vietnamese TTS only, or both.
- Eleven VieNeu Nano presets, optional local reference-audio cloning, and the
  existing 67 Piper speaker choices in the full Windows package.
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
| Reduction versus v1.0.0 | **35.4%** | **41.9%** | **67.8%** |

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

See [measurement details and reproduction](docs/PERFORMANCE.md#v110-streaming-profile)
and the [public timing samples](docs/benchmarks/ttfa.json). New versions will
get new rows when comparable measurements are available; unmeasured versions
will not inherit earlier results.

## Install on Windows

The packaged release targets **Windows x64**. Download its setup EXE and **all
three BIN files** from [Releases](https://github.com/hungdinhxuan/Ludivoxa/releases),
place them in one folder and run the EXE. Compare the files with `SHA256SUMS.txt`.
The complete offline download is approximately 4.9 GB, with about 6.4 GB installed.

Python, English/Japanese OCR models, Vietnamese speech, Ollama 0.40.16 and
HauhauCS Qwen3 4B Aggressive Q4_K_M are included. No separate model download is
needed for that profile. The installer preserves an existing `config.yml`.

Already using v1.0.0? Run **Ludivoxa-1.1.0-App-Update.exe** and select the
existing installation directory. This smaller package preserves your models,
runtime and settings. Version 1.1.0 adds the Updates page for future releases;
public release checks and downloads need no GitHub token or sign-in.

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
