# Attribution and third-party components

Ludivoxa is derived from [Interpreter](https://github.com/bquenin/interpreter)
by qube / bquenin. The starting source snapshot identified itself as Interpreter
2.23.3. This fork starts its own release sequence at **Ludivoxa 1.0.0**.

The original capture, OCR, translation, overlay and Qt interfaces are retained.
Ludivoxa adds the offline English/Japanese PaddleOCR profile, selected-area OCR,
local LLM/Vietnamese speech integration, performance work, runtime lifecycle
management and Windows offline distribution described in the changelog.

Original copyright notices remain in [LICENSE](LICENSE). Ludivoxa application
source is distributed under the MIT license. Bundled dependencies, runtimes and
model weights retain their own licenses; the application license does not
replace those terms.

| Component | License / source |
| --- | --- |
| Interpreter application foundation | MIT; [upstream](https://github.com/bquenin/interpreter) |
| Ollama 0.40.16 | MIT; [sources](https://github.com/ollama/ollama/tree/v0.40.16) |
| HauhauCS Qwen3 4B Aggressive Q4_K_M | Apache-2.0; [model](https://huggingface.co/HauhauCS/Qwen3-4B-2507-Instruct-Uncensored-HauhauCS-Aggressive) |
| PaddleOCR / PaddlePaddle | Apache-2.0; [sources](https://github.com/PaddlePaddle/PaddleOCR) |
| VieNeu 3.8.4 / Nano | Apache-2.0; [SDK](https://github.com/pnnbao97/VieNeu-TTS), [model](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Nano) |
| Optional Piper runtime | GPL-3.0; [sources](https://github.com/OHF-Voice/piper1-gpl) |
| Piper voice weights | Per-model terms in the bundled `MODEL_CARD` files |
| Python, Qt, native/GPU libraries and other dependencies | Their bundled license files and distribution metadata |

See [bundled third-party notices](packaging/THIRD_PARTY_NOTICES.txt) and the
license/model-card files distributed with the Windows app. Git contains source
code and public configuration examples; model weights and binary dependencies
are distributed separately in release assets.
