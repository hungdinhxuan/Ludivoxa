# Validation and performance

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
the same local model, three seeded context pairs, English OCR, Nano speaker 4,
eight steps and two CPU threads. Both runs use the same saved frames cropped
to their detected dialogue areas. Each case has **three warm trials**; no
previous speech is queued. These are Windows WASAPI loopback measurements,
not estimated sums of independent component medians.

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
