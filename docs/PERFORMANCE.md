# Validation and performance

## v1.5.0 Windows frame replay

On 2026-11-21, the v1.5.0 source replayed two synthetic English dialogue inputs at
four frames per second in TTS-only mode. The run used warm PP-OCRv5 on CPU,
local Gemma 4 E4B Q4_K_M with three seeded context pairs, and VieNeu Nano
preset 7 with speaker metadata. A separate WASAPI endpoint captured the output.
Translation and audio caches were reset for each trial.

| Synthetic input | WASAPI trials | Median TTFA |
| --- | --- | ---: |
| Short dialogue | 914.63, 910.24, 897.54 ms | **910.24 ms** |
| Two sentences | 906.44, 914.23, 975.70 ms | **914.23 ms** |

Timing starts at complete-frame delivery. All six trials matched the first
speech in WASAPI loopback and made one LLM request per utterance. The releases
were measured in separate sessions. Live game FPS and RDP were outside the
benchmark.

## v1.4.0 Windows frame replay

On 2026-11-20, the v1.4.0 source replayed the same two synthetic English inputs
at four frames per second with warm PP-OCRv5 on CPU, local Gemma 4 E4B Q4_K_M
and VieNeu Nano preset 7. The run used speaker metadata, three seeded context
pairs and a separate WASAPI playback endpoint. Translation and audio caches
were reset before each trial.

| Synthetic input | WASAPI trials | Median TTFA |
| --- | --- | ---: |
| Short dialogue | 1,126.76, 1,028.81, 1,023.49 ms | **1,028.81 ms** |
| Two sentences | 1,108.41, 1,138.57, 1,054.41 ms | **1,108.41 ms** |

All six trials matched speech in WASAPI loopback and made one LLM request per
utterance. Timing starts at complete-frame delivery. Installer QA was active
on the same machine. These runs do not establish a performance change since
v1.3.1. Live game FPS and RDP were outside the benchmark.

## v1.3.1 Windows frame replay

On 2026-11-20, the release source replayed two synthetic English dialogue
inputs at four frames per second with the Windows capture notification path.
The run used warm PP-OCRv5 on CPU, local Gemma 4 E4B Q4_K_M with three seeded
context pairs, and VieNeu Nano preset 7. Speaker metadata was enabled. Translation
and audio caches were reset per trial.

| Synthetic input | WASAPI trials | Median TTFA |
| --- | --- | ---: |
| Short dialogue | 925.52, 882.31, 897.24 ms | **897.24 ms** |
| Two sentences | 1,001.66, 1,017.59, 1,014.42 ms | **1,014.42 ms** |

Each trial matched the first speech in WASAPI loopback and generated one
utterance. Timing starts when a complete frame is published. This run uses
a separate playback endpoint and does not establish a speed change versus
v1.3.0. Live rendering, FPS, long passages and RDP were outside the benchmark.

## v1.3.0 Windows frame replay

On 2026-11-19, the release candidate replayed two synthetic English dialogue
inputs at four captured frames per second. The same OCR, LLM and voice ran
with 150 ms frame polling or with the Windows capture notification path.
Both paths used the same warm local Gemma 4 E4B Q4_K_M, three seeded context
pairs, the same speaker metadata and VieNeu Nano preset 7 with eight steps
and two CPU threads. Translation and audio caches were reset between runs.

| Input | Initial poll phase | Timer polling | Frame notifications |
| --- | ---: | ---: | ---: |
| Short dialogue | 25 ms | 912.14 ms | 919.07 ms |
| Short dialogue | 75 ms | 1,180.48 ms | 902.66 ms |
| Two sentences | 25 ms | 1,215.50 ms | 975.16 ms |
| Two sentences | 75 ms | 1,210.92 ms | 1,032.72 ms |
| Median | | **1,195.70 ms** | **947.12 ms** |

The median paired saving was **209.27 ms**. Each trial matched its generated
first audio chunk in Windows WASAPI loopback. Each pair produced the same
translation, exactly one utterance and the same voice preset. OCR checks,
including cache lookups, had a median of 17 with timer polling and 12 with
notifications over the observation window.

Separate replay checks covered a pause during text reveal, disappearing text
and a static valid frame with no further capture events. The new path spoke
the completed line once, stayed silent when the text disappeared and recovered
the static line after about 800 ms. The latter case reached first audio in
1,374.22 ms versus 941.62 ms with timer polling.

The clock starts when the complete frame is published for the four timing
pairs. This replay excludes game rendering, a preceding spoken line, native
acquisition delays and RDP transport. The small sample does not establish
live FPS, overall resource usage or p95 results.

## Tagged-version loopback TTFA

On **2026-11-16**, after restoring an active Windows console and pausing
unrelated audio, **36 valid trials** measured the exact v1.1.0, v1.1.1,
v1.1.2-rc.1 and v1.1.2 source tags. Each tag ran in a separate process with
three warm trials per input. The previous incomplete/noisy attempts were
discarded. Every accepted trial matched its actual generated first audio
chunk in Windows WASAPI loopback; correlations ranged from **0.958 to 1.000**,
with no loopback input status flags. The acceptance threshold stayed at 0.4.

| Exact tag | Short game dialogue | Two synthetic English sentences | Long game paragraph |
| --- | ---: | ---: | ---: |
| v1.1.0 | 896.1 ms | 1,038.6 ms | 1,439.5 ms |
| v1.1.1 | 888.0 ms | 971.2 ms | 1,433.6 ms |
| v1.1.2-rc.1 | 866.5 ms | 849.3 ms | 1,364.3 ms |
| v1.1.2 | **867.7 ms** | **966.3 ms** | **1,355.9 ms** |

Each value is a median of the measured **complete saved frame available →
first matching speech observed in loopback** interval, not a sum of component
medians or a timestamp of the first play request. The public
[TTFA data](benchmarks/ttfa.json) retain individual samples, correlations, exact
source commits, report hashes and the new measurement cohort. The earlier
v1.1.0 development-profile data and their historical reductions are retained
separately; they are not substituted for the newly measured release tag.

Conditions: Windows 11, i7-13700F, RTX 4090 24 GB, English Paddle mobile OCR
(two CPU threads/max-side 640), Vietnamese output, local Qwen3 HauhauCS 4B
Q4_K_M, Ollama context 2048, three seeded context pairs, per-tag default prompt,
temperature 0/seed 42, Nano speaker 7/eight steps/two CPU threads/rate 1.0.
Production output selected shared WASAPI on the same speaker, stereo 48 kHz,
requested buffer 60 ms, application volume 0.25; loopback callbacks were 10 ms.
Frame polling was 150 ms with initial phases 25/75/125 ms. Caches were reset
between trials, with confirmed preview reuse within a trial preserved. Only
loopback inference connections occurred; installed configuration was retained
and the temporary managed Ollama lease was released after each run.

The helper uses up to 650 ms from the **actual first generated audio chunk**
as its reference. Concatenating later chunks would invent continuous audio
across real streaming gaps. Near-silent candidate windows are excluded from
correlation to avoid numerical false peaks; unrelated audio is never treated
as a successful first-speech match. Playback still stops 2.2 seconds after the
play request, so these trials do not establish complete-paragraph continuity.

The execution order was v1.1.1, RC, stable, then v1.1.0 as control. Three trials
in fixed order do not establish small inter-version speed differences or p95.
Model loading, native window capture, typewriter reveal, prior speech and live
game contention are excluded. This is loopback observation rather than acoustic
speaker latency. These are Qwen results, not Gemma measurements, and packaged
installers were not independently benchmarked.

Reproduce using the existing audit with the exact tag on `PYTHONPATH`, the same
local config/models/saved frames, a running local Ollama and PyAudioWPatch:

```powershell
$env:PYTHONPATH = 'C:/path/to/exported-v1.1.2/src'
.venv/Scripts/python.exe packaging/audit_latency.py --config C:/path/to/benchmark-config.yml --streaming --selected-box --audio-observation loopback --repeats 3 --report .test-results/v1.1.2-loopback.json
Remove-Item Env:PYTHONPATH
```

The older disconnected-session WDM-KS observations below use another metric
and remain separate. They are not the current active-console TTFA results.

## Tagged-version native callback audit

On **2026-11-16**, the exact source tags v1.1.1, v1.1.2-rc.1 and v1.1.2 were
measured in separate processes. v1.1.0 was rerun as a control. Each tag used
the same three saved inputs and three warm trials per input: **36 valid trials**.
The source commit, raw report SHA-256, individual samples and medians are in
[the public data](benchmarks/ttfa-native-callback.json); dialogue, translations,
user paths and device identifiers are excluded.

**This run could not measure loopback TTFA.** The current Windows user session
was disconnected, Audio services were running, and PortAudio exposed only
WDM-KS devices. WASAPI/MME/DirectSound had no endpoints; PyAudioWPatch exposed
zero devices and its default-loopback query returned `-9996`. No existing
loopback value has been copied into the unmeasured tags.

The measured metric is **complete saved frame available → first real native
driver callback containing voiced PCM**, with absolute sample threshold 1e-5.
It includes polling phase, OCR, stability waiting, translation and synthesis.
It stops before DAC/mixer/speaker output and is not acoustic or loopback TTFA.
On WDM-KS the production player consumes the entire audio stream before
playback. Waiting after the first play request therefore includes remaining
synthesis, not just driver/device latency. The requested buffer was 60 ms;
the native stream reported 61.0 ms.

| Tag | Short frame → PCM callback | Two-sentence frame → PCM callback | Long frame → PCM callback |
| --- | ---: | ---: | ---: |
| v1.1.0 control | 3,914.3 ms | 4,448.3 ms | 15,644.1 ms |
| v1.1.1 | 3,515.8 ms | 4,450.3 ms | 15,404.5 ms |
| v1.1.2-rc.1 | 3,984.6 ms | 4,240.7 ms | 15,543.2 ms |
| v1.1.2 | 3,987.0 ms | 4,332.1 ms | 15,585.3 ms |

For comparison within this run, the first play-request medians were:

| Tag | Short frame → play request | Two-sentence frame → play request | Long frame → play request |
| --- | ---: | ---: | ---: |
| v1.1.0 control | 1,814.4 ms | 1,721.5 ms | 2,366.8 ms |
| v1.1.1 | 1,408.0 ms | 1,702.5 ms | 2,410.8 ms |
| v1.1.2-rc.1 | 1,913.1 ms | 1,634.1 ms | 2,274.3 ms |
| v1.1.2 | 1,926.9 ms | 1,607.0 ms | 2,484.0 ms |

**A play request is not first audio.** In the stable long-paragraph trials,
the median frame-to-request time was 2.48 seconds, but the first voiced callback
was at 15.59 seconds. The v1.1.0 control also needed 15.64 seconds on WDM-KS;
the difference from the historical streaming/WASAPI results is not evidence
of a 10× code regression between releases. Three trials with fixed version
order also cannot establish small speed differences or production p95.

Conditions: Windows 11, i7-13700F, RTX 4090 24 GB, local Qwen3 HauhauCS 4B
Q4_K_M, Ollama context 2048, three seeded history pairs, per-tag default system
prompt, temperature 0/seed 42, English Paddle mobile OCR (two CPU threads,
max-side 640), selected dialogue crop, Nano speaker 7/eight steps/two CPU
threads/rate 1.0, stereo 44.1 kHz WDM-KS. Frame polling used 150 ms, with
25/75/125 ms initial phases. OCR/translation/audio caches were cleared between
trials; confirmed preview translation could still be reused within a trial.
All inference connections were loopback-only. The managed runtime stopped
afterward and the installed app configuration stayed unchanged.

The old audit's 2.2-second recording cutoff from the play request can expire
before buffered WDM-KS playback begins. Native observation instead stops
500 ms after its first voiced callback, with a 45-second safety deadline.
The early incomplete run was discarded. Native status flags were empty in
all 36 valid trials. This does not establish full-utterance audio continuity.

To reproduce with local models and an already running local Ollama, prepare
an isolated benchmark config matching the public conditions and import each
tag's source while using the current audit helper:

```powershell
$env:PYTHONPATH = 'C:/path/to/exported-v1.1.2/src'
.venv/Scripts/python.exe packaging/audit_latency.py --config C:/path/to/benchmark-config.yml --streaming --selected-box --audio-observation native-callback --repeats 3 --report .test-results/v1.1.2-native-callback.json
Remove-Item Env:PYTHONPATH
```

Provision the audit's saved input paths locally as described in the script.
For true loopback TTFA, reconnect to an active Windows desktop with a WASAPI
loopback endpoint and run `--audio-observation loopback` with PyAudioWPatch.
Do not combine the two observation methods in one comparison. Models are warm,
no prior speech is queued, and model loading, native capture, typewriter
reveal and live game contention are excluded. Packaged installers were not
independently benchmarked in this run.

The audit-helper change passes seven focused tests and the full Windows source
suite passes **656 tests, one platform skip** (69.06 seconds). No application
pipeline code or release artifacts changed for this documentation update.

A later active-console check restored WASAPI loopback, but unrelated output
audio prevented reliable waveform matching in the attempted run. Those
incomplete trials are not published as TTFA results. The loopback helper now
matches the actual first generated chunk (up to 650 ms), since concatenating
later streamed chunks can invent continuous audio across real playback gaps.
It also excludes effectively silent correlation windows whose near-zero
variance can produce numerical false peaks. The minimum match correlation
remains 0.4. The follow-up passes **eight focused tests** and **657 full-suite
tests, one platform skip** (68.64 seconds). The subsequent clean loopback run
is documented above; the incomplete attempts remain excluded.

## v1.1.2 stable qualification

The original stable qualification did not measure end-to-end TTFA or game FPS;
the subsequent tagged-source TTFA measurements are documented above.
The stable qualification used synthetic dialogue frames with real English
PaddleOCR, local Ollama translation and Nano speech synthesis/native playback
(muted), through the production Qt worker and speech controller.

- Gemma E4B Q4_K_M: 40 scenes in 186.7 seconds; exactly 40 playbacks and 40
  confirmed scene resets, including identical consecutive dialogue scenes.
- Qwen3 4B Q4_K_M: 20 scenes in 95.6 seconds; exactly 20 playbacks and 20 resets.
- Cases included typewriter reveal, changed numbers/negation and all speech
  output modes. Deliberate 150 ms UI stalls occurred every two seconds; maximum
  observed Qt timer gaps were 168.6/167.6 ms. This is not a gameplay lag metric.
- Parent-process RSS growth after six warm-up scenes was 8.2/4.0 MiB. These
  short runs do not establish a long-term memory bound.
- Model requests used only loopback. Windows' real disconnected-session state
  was verified to defer native capture startup and permit clean shutdown.
- Regression tests distinguish real scene breaks from brief blank frames,
  unpositioned OCR and OCR misses over unchanged visible text, including while
  the first utterance is still playing.

No fresh live game/phone RDP session was available for this stable run. The
earlier RC's live RDP check below remains useful, but does not cover every
game, font, driver, endpoint or user-reported scene.

## v1.1.2-rc.1 speech starvation and RDP recovery

The pending first-read candidate now reuses the existing strict glyph-image
comparison; OCR variation alone cannot keep restarting the settle timer while
the visible text is unchanged. White completion arrows are checked by geometry,
and real numeric/negative text changes remain covered. A replay of ten saved
GBA states with controlled fast OCR alternation changed from zero speech
requests to one; stable-first then alternating input still produced one.
This replay used simulated translations/audio and is not a live error rate or
TTFA benchmark. The later tagged-source loopback run above measures this RC
separately from that regression replay.

Twenty-four offline Nano preset-4 waveform trials produced non-silent audio
(RMS 0.0994–0.1886). Native audio diagnostics record the endpoint, volume,
peak and RMS. WDM-KS/ASIO retain their existing buffered callback path; the
tests require complete playback without underruns for each supported host.

Windows capture was exercised with real WGC/PrintWindow windows, including
gameplay over RDP. The scene from the latest intermittent report was unavailable
for a fresh check after the fix.

## v1.1.1 live GBA repeat reproduction

A read-only capture collected 112 selected-box frames while the user operated
the game. It used real local OCR and simulated translation, without playing
additional audio. The initial adaptive typewriter patch and the deployed build
both accepted four sources in that session: adaptive waiting alone did not fix
the static-dialogue repeat.

Inspection identified two image-guard failures. Word detections split/merged
between frames, and y-coordinate jitter changed the recognized word order.
Individual remembered word patches could not contain newly merged boxes.
Separately, a bouncing continuation arrow left one scanline of gray shadow
outside its mask. The guard now stores horizontal line patches, orders words
left to right within each line and includes the arrow's vertical shadow.
Predominantly vertical detections retain engine order.

Replaying ten saved states of the same completed dialogue, with their actual
pixels, box positions and captured source variations, produced **10 translation/
speech events before the fix and 1 after it**. Replay timing was controlled;
translations were simulated and no audio was synthesized. This establishes
the captured image-guard regression, not an audible live-game success rate or
an end-to-end TTFA measurement. Tests also retain genuine changes such as
HP 10 to HP 20, new text and vertical Japanese column order.

A second replay reran the real offline OCR engine on those ten saved images,
including its per-word text, instead of injecting the previously captured
source strings. It reproduced the same **10 to 1** reduction in speech events.
The source suite passes **590 tests, one platform skip** after these changes.
The rebuilt Windows EXE also passes its real offline OCR/Ollama/TTS diagnostic
(**591 passed, one skipped** across both checks), including playback under UI
stalls and loopback-only network destinations. Fifteen embedded modules match
the source. The patched local app was reopened with its configuration retained;
these fixes are not in the published v1.1.0 installer.

## v1.1.1 typewriter dialogue handling

Live debug logs recorded accepted sources of 8, 13 and 20 characters within
about two seconds. Those were different translations, so identical-utterance
suppression alone could not stop the successive reads. Character counts are
diagnostic evidence of a possible reveal sequence, not a recording of every
intermediate source string. The log also showed an identical utterance after
opening the OCR editor, which previously cleared the completed-speech guard.

The worker now tracks growing text and newly appearing high-contrast ink in
flat dialogue regions. Static text keeps the 350 ms settle window. Observed
reveals use 800–1500 ms of quiet, adapted to the observed interval between
updates; a detected continuation cursor permits the 350 ms path. Once a game
has shown such a cursor, cue-less candidates have at least a 1200 ms fallback.
Additional visual-only waiting is bounded for persistent shader noise.
Cursor-backed trailing T/V/Y OCR artifacts are removed before debounce, so a
blinking GBA arrow does not restart the source timer.

In TTS-only mode, exact overlap of at least three words/twelve word characters
from completed prior dialogue is excluded from the next translation. This
handles appended sentences and carried-over lines, using the existing LLM
context. The fuzzy translation cache cannot substitute an earlier prefix for
newly extended text. Tests cover word-render pauses longer than 350 ms,
unchanged OCR while visible ink grows, cursor learning/blinking and fallback,
source overlap, changed content and unchanged OCR-editor exits. These are
controlled tests; no new live FPS or end-to-end TTFA result is claimed.

## v1.1.1 streaming translation repeat guard

The streaming controller previously keyed requests only by OCR source text.
The translation cache can map small source variations to the same Vietnamese
utterance; different source keys could therefore play that utterance twice.
The controller now compares against the last actually played translation.
It buffers only a matching prefix, suppresses a fully identical utterance and
releases the complete new text as soon as it differs. Numbers, new sentences,
shorter translations and explicit capture/voice resets remain supported.

Regression tests cover duplicates after playback, prefetched duplicates,
different chunk/sentence boundaries, buffered/streamed transitions and the
real LLM translation-cache interface. The latter exercises three alternating
OCR keys with one synthesis and one playback, without the image guard. The
fuzzy cache also excludes matches with changed numbers or negation markers.

A live 56-frame selected-box sample produced one simulated speech request;
that sample did not reproduce the user's audible repeat. Separately, the local
LLM/cache mapped the observed one-letter OCR variants to identical translations.
These are diagnostic observations, not a new end-to-end latency benchmark.
Debug logs now identify accepted streaming sources, generated translations and
playback starts with hashes, without adding dialogue text to those events.

Validation: **570 passing full-suite source tests, one platform skip**, plus
one additional passing intentional-repetition regression and one passing
rebuilt-EXE diagnostic (**572 passed, one skipped** across those checks).
These were intermediate development checks with debug logging before the
final v1.1.1 validation above; they were not in the v1.1.0 installer.

## v1.1.1 pixel-font repeat guard

Controlled OCR variants were replayed over a recorded GBA frame cropped to a
1500 × 320 dialogue selection, with source origin (950, 1400). Across panel
shading, a one-pixel translation and animated box padding, v1.1.0 produced four
translation/speech events; the updated guard produced one per case. OCR text
variants were injected for this comparison; it does not measure recognition
accuracy, live gameplay or synthesis latency.

The guard compares exact binary ink geometry on eligible flat panels, with
bounded translation and antialias/background checks. It retains four hashes
of proven image states for the current accepted dialogue. Twenty subsequent
checks per case had medians of about 3.7–3.8 ms on that crop; first-time variant
validation is more expensive. This timing is not OCR latency or TTFA. Tests
also retain genuine changes such as HP 10 → HP 20, added negation, new dialogue,
and reset behavior across scene/capture changes. No new end-to-end TTFA number
is claimed for this patch.

The combined audio-format and pixel-font patches pass **551 source tests,
one platform skip**, plus a passing rebuilt Windows EXE diagnostic (**552
passed, one skipped** overall). Fifteen embedded modules match the source.
These were intermediate development checks before the final v1.1.1
validation above; the v1.1.0 installer did not include these patches.

## v1.1.0 updater validation

The public updater source suite passes **522 tests, one platform skip**.
Pre-release anonymous requests read the public v1.0.0 release and its 593-byte
checksum asset successfully, with no token or Git sign-in. The existing
pipeline's loopback-only inference guard still passes with update checks
disabled. Credential storage and lookup have been removed from the updater.
The rebuilt EXE passes one additional real OCR/Ollama/TTS diagnostic
(**523 passed, one skipped** overall). Fifteen embedded modules match the
source, and the removed credential module is absent from the bundle.

Native Inno tests compile the production app-only file rules into an isolated
fixture without production uninstall registration or shortcuts. They verify
EXE/library replacement, unchanged config/model/runtime data, explicit restart
and rejection of an empty destination.
Application version metadata from older installations is removed before
copying the new package; unrelated dependency metadata is retained. This
upgrade regression is covered by the native installer fixture.

Mock HTTP tests cover corrupt/truncated
downloads, cancellation, missing releases and rate limits, anonymous CDN
redirects and a second checksum verification before launch. UI tests confirm
that no credential input is present and checks run in a background thread.

App-only installers exclude local model weights and the Ollama runtime.
The initial GitHub validation downloaded release metadata and its small
checksum asset. Native installer tests use an isolated fixture; they do not
upgrade a production installation or establish live game performance.

## v1.1.0 streaming profile

The following is an A/B comparison against the tracked v1.0.0 source, using
the same local model, three seeded context pairs, English OCR, Nano speaker 7,
eight steps and two CPU threads. Both runs use the same saved frames cropped
to their detected dialogue areas. Each case has **three warm trials**; no
previous speech is queued. These are Windows WASAPI loopback measurements,
not estimated sums of independent component medians.

The streaming measurements were taken during v1.1.0 development, before the
final prompt/updater changes. They describe that streaming profile, not a
separate benchmark of the published v1.1.0 installer. Only the versions and
profiles listed here have comparable A/B data. A future release needs a new
measurement row; it must not reuse the previous version's timing results.

The [public timing samples](benchmarks/ttfa.json) retain each valid trial's
`ttfa_loopback_observed_ms`, rounded medians and reductions. They omit source
dialogue, translations, user paths and audio device identifiers. Hardware:
Windows 11, Core i7-13700F, RTX 4090 24 GB. Short/long cases use saved game
dialogue crops; the two-sentence case uses synthetic English input.

| Saved input | v1.0.0 median TTFA | Streaming median TTFA | Reduction |
| --- | ---: | ---: | ---: |
| Short game dialogue | 1,413.6 ms | 912.6 ms | 35.4% |
| Two synthetic English sentences | 1,743.6 ms | 1,013.6 ms | 41.9% |
| Long game paragraph | 4,465.2 ms | 1,436.9 ms | 67.8% |

The new path keeps the 350 ms source-stability guard. Short punctuated
TTS-only candidates can be translated while that guard is running, at most
once per 750 ms; speculative results are committed only when the same source
and context are confirmed. Longer sources use completed-sentence translation
streaming. The first Nano segment uses a 40-character chunk limit, later
segments 80, with at most two queued audio chunks and one synthesis worker.

On this machine, WASAPI uniquely identifies the same physical speaker endpoint
as the default MME device. Its measured play-request-to-first-audio time is
about **60 ms**, versus **252–276 ms** on the old MME/high-buffer path. The
requested native buffer is 60 ms. Smaller synthesis chunks start sooner but
can increase total synthesis work and alter phrasing; inference stays at two
CPU threads/eight steps. ASIO/WDM-KS retain buffered callback playback.

TTFA trials intentionally stop playback 2.2 seconds after requesting audio;
they establish first-audio latency, not full-paragraph playback continuity.
Separate native/frozen diagnostics drain complete streaming utterances under
Qt stalls and check the native underrun counter. Matching uses the actual
generated audio, not a second synthesis; correlations in this A/B run exceed
0.99. No human voice-similarity or naturalness evaluation was performed.

The streaming profile was validated with **485 source tests, one platform skip**, including 20 new
streaming tests. The rebuilt EXE adds one passing diagnostic (**486 passed,
one skipped** overall); its embedded modules were compared with the source.
Real Nano streaming playback drained **8.8 seconds** in **8.797 seconds** under
350 ms Qt stalls, with **zero native underruns**, using the same speaker's
WASAPI endpoint (48 kHz, reported device latency 70 ms). The EXE's only recorded
inference destination was `127.0.0.1:11435`, and its private runtime stopped
after exit. Release v1.0.0's validation and earlier measurements follow.

To reproduce, run each profile in a separate process after provisioning local
models and the audit-only PyAudioWPatch dependency. Run the extended audit
helper from the development checkout, importing the v1.0.0 source for the
buffered baseline:

```powershell
$env:PYTHONPATH = 'C:/path/to/Ludivoxa-v1.0.0/src'
.venv/Scripts/python.exe packaging/audit_latency.py --selected-box --managed-runtime --repeats 3 --report .test-results/buffered-ttfa.json
Remove-Item Env:PYTHONPATH
.venv/Scripts/python.exe packaging/audit_latency.py --streaming --selected-box --managed-runtime --repeats 3 --report .test-results/streaming-ttfa.json
```

The saved frames and loopback tooling are local opt-in inputs, excluded from
Git; see the audit script for their paths. The extended audit helper can be
used with a v1.0.0 source directory on `PYTHONPATH` for the baseline. Its
default-device high-buffer probe is legacy metadata; the actual native
interface and underruns are recorded by full-playback diagnostics. The test
socket guard permits loopback inference only, and the owned Ollama lease is
released at the end of the audit.

These trials exclude model loading, native capture phase, game typewriter
reveal, queueing behind a current spoken line and live gameplay resource
contention. Three trials do not establish a production p95 latency guarantee.

## v1.0.0 validation baseline

Release **1.0.0** was verified with **465 source tests passing, one Linux X11
test skipped**, plus a passing frozen-EXE test with Python removed from PATH
(**466 passed, one skipped** overall). Ruff passed, and both the Python source
distribution and wheel built successfully. The EXE reports Ludivoxa 1.0.0 in
its native Windows version resource; its embedded pipeline modules were
compared with the release source. The real EXE exercised local OCR/Ollama/TTS, synthetic
reference enrollment, native audio under UI stalls, selected-area coordinates,
duplicate suppression and private runtime cleanup. Inference connections were
restricted to loopback, and the private Ollama listener stopped after exit.
The source suite completed in 62.57 seconds and the EXE diagnostic in 64.26
seconds on the release validation machine.

Measurements below were collected on Windows 11, Core i7-13700F, RTX 4090 24 GB.
They are examples, not latency guarantees for other systems or all game scenes.

| Operation | Observation |
| --- | ---: |
| Recorded 3840 × 2160 input, warmed uncached OCR | 158.99 ms median |
| Same dialogue in a 1556 × 314 selected box | 72.79 ms median |
| Alternating known cursor-blink frames with OCR cache | 25.60 ms median |
| Short Vietnamese Nano synthesis, eight steps | 472.99 ms median |
| Long Vietnamese Nano synthesis, eight steps | 2,502.62 ms median |
| Actual idle/ready GUI close → all watched children exited | 726.59 ms |

The selected-box comparison used five uncached scans per shape after warmup;
the saved full-frame input already had exclusion masks. Two CPU threads and
detector max-side 640 were used. Cache timing is not fresh OCR inference.
Eight steps can sound rougher than sixteen; long audio remains fully buffered.

An earlier loopback audit measured first speech at approximately **1.63 s** for
a short line and **1.89 s** for two sentences, three valid trials each. These
start when a complete recorded frame is available, with models resident and
no previous audio queued. Native capture phase, typewriter reveal and waiting
for existing speech add time. A long trial did not meet the waveform-match
threshold and is not reported as a valid TTFA measurement.

No live gameplay FPS or physical mouse latency was established by those saved-
frame tests. A separate LAN GPU host and non-Windows desktop capture were not
available for release validation. The existing cross-platform backends are
retained, while Windows x64 is the packaged and locally verified target.
