# Validation and performance

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
