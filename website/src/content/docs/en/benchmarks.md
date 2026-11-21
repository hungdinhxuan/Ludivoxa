---
title: Game translation and speech latency benchmarks
description: Windows WASAPI time-to-first-audio results for Ludivoxa, with test conditions and full benchmark data.
---

**TTFA (time to first audio)** measures the time from a complete captured frame
to the first matching speech in Windows audio loopback.

| v1.5.0 input | Trials | Median TTFA |
| --- | ---: | ---: |
| Short synthetic dialogue | 3 | 910.24 ms |
| Two synthetic sentences | 3 | 914.23 ms |

The test replayed frames at four frames per second with warm PaddleOCR on CPU,
local Gemma 4 E4B Q4_K_M and VieNeu Nano. It used a separate WASAPI endpoint.
These figures measure replay latency. Live game FPS, RDP and the time a game
takes to reveal text are outside the measurement.

[Measurement setup and individual trials](https://github.com/hungdinhxuan/Ludivoxa/blob/v1.5.0/docs/PERFORMANCE.md#v150-windows-frame-replay) ·
[Historical benchmarks](https://github.com/hungdinhxuan/Ludivoxa/blob/main/docs/PERFORMANCE.md)
