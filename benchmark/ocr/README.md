# OCR benchmark

This benchmark compares OCR models before the application dependency is changed. It exercises the exact production path in src/ludivoxa/ocr.py, including BGRA-to-RGB conversion, MeikiOCR inference, confidence filtering, deduplication, spatial clustering, and the worker's space-joined region output.

The current corpus manifest has 105 entries, all captured from real games or game projects:

| Group | Images | Ground truth today | Purpose |
| --- | ---: | --- | --- |
| Historical benchmark branch | 13 | single review | Directional regression smoke test; one game only |
| Project EGG retro-PC pack | 68 | single review | 41 PC-88, 19 PC-98, and 8 Sharp X1 title, dialogue, menu, battle, and HUD frames |
| Curated SFC/Mega Drive sources | 16 | 15 single review, 1 unscored | Six games from archived reading corpora and publisher pages |
| Other external internet sources | 8 | 4 single review, 1 draft, 3 unscored | Visual-novel, PC-88, non-Japanese, and vendor-demo diagnostics |

The 100 scoreable samples are all real screenshots. This is enough for a substantially broader directional comparison, but not enough to approve a model replacement by itself: all 100 references still have only one review, and there are currently zero independently verified real evaluation samples.

## Quick start

From the repository root on Windows:

~~~powershell
.\.venv\Scripts\python.exe benchmark\ocr\benchmark.py inventory
.\.venv\Scripts\python.exe benchmark\ocr\benchmark.py prepare --suite legacy-smoke --suite retro-real --suite retro-pc
.\.venv\Scripts\python.exe benchmark\ocr\benchmark.py matrix
~~~

The default matrix covers 96 scoreable game screenshots from the historical, curated-console, and retro-PC suites. It runs the installed baseline directly from .venv, forces its Hugging Face cache offline by default, then runs meikiocr==0.3.4 in a separate uv isolated/no-project environment and a separate model cache. It does not alter the application environment or dependency lock. Results go to the ignored benchmark/ocr/results directory.

Test another API-compatible package/version with:

~~~powershell
.\.venv\Scripts\python.exe benchmark\ocr\benchmark.py matrix --candidate "meikiocr==0.3.3"
~~~

To run the outside internet diagnostics too:

~~~powershell
.\.venv\Scripts\python.exe benchmark\ocr\benchmark.py prepare
.\.venv\Scripts\python.exe benchmark\ocr\benchmark.py matrix --suite internet --include-unscored
~~~

Draft and unscored images are timed and their predictions/regions are recorded, but they are never included in CER.
The four scoreable internet diagnostics can be added with `--suite internet`, but the vendor demo and diagnostic-only samples must not decide promotion.

Ground-truth transcription must be blind to benchmark output. A reference is frozen from the exact downloaded image before either the baseline or candidate is run. Reviewers may use lossless nearest-neighbor zoom to inspect the same pixels, but must not consult either model's prediction, a translation overlay, a walkthrough, or source text to fill uncertain or hidden characters. If the visible pixels do not determine the target text or a mixed layout has no defensible linear reading order, the image stays unscored.

## What is measured

- Corpus-level (micro) character error rate: substitutions + deletions + insertions divided by reference characters.
- Macro CER, exact-match rate, and the three edit types separately.
- Empty-image false positives.
- Median, mean, and p95 warm-model latency; model loading is timed separately.
- Determinism across repeated runs.
- Per-image predictions, regions, timings, source tags, and per-suite summaries.
- Paired baseline/candidate wins, losses, and ties.
- A paired bootstrap 95% confidence interval for candidate CER minus baseline CER.

Text is normalized with Unicode NFKC and whitespace is ignored. Content and punctuation remain significant. This handles full-width ASCII and the fact that the application joins detected regions with spaces without forgiving real OCR mistakes.

Every report records the Ludivoxa OCR source hash, Git commit/dirty state, package versions, ONNX provider, platform, manifest fingerprint, every local image hash, and the resolved model snapshot and ONNX SHA-256 where available.

Comparison fails closed unless both reports have the same manifest, local-file, and OCR-source hashes, workload-defining configuration, and ordered sample IDs. This keeps paired CER, exact match, latency, errors, and promotion-gate counts on one identical workload.

## Promotion rule

The comparison distinguishes a directional statistical outcome from a release decision. A candidate is not promotion-ready unless all of these hold:

1. The upper bound of the paired CER-delta 95% confidence interval is below zero.
2. Exact-match rate does not regress.
3. Candidate p95 stays under 500 ms and does not regress by more than 25%.
4. There are no inference errors.
5. At least 100 independently verified real evaluation/holdout images from at least five games are present.

The limits are CLI options, but lowering the corpus requirements simply to obtain a passing result defeats the benchmark.

## Building the real corpus

The source registry is corpus.json plus its project-egg.json source pack. The prepare command downloads or extracts images into the ignored data directory, verifies fixed SHA-256 hashes and dimensions, and writes data/corpus.lock.json. Source packs provide shared metadata while expanding to ordinary manifest samples before validation, selection, locking, or fingerprinting.

Synthetic images are deliberately excluded. The manifest validator accepts only HTTPS downloads and screenshots extracted from repository history; the benchmark has no image generator. Font rendering, artificial backgrounds, and constructed layouts do not represent the capture, scaling, compression, or typography distribution this application sees.

Third-party game screenshots are deliberately not committed. Their manifest entries retain the exact file URL, provenance page, host or publisher, hash, dimensions, and redistribution status. Even public-domain Wikimedia files use the same local-fetch path so the repository contains no mixed-origin image bundle.

The retro-PC source pack uses official [Project EGG PC-88](https://www.amusement-center.com/project/egg/console/hard.php?hard=PC-8801), [PC-98](https://www.amusement-center.com/project/egg/landing/hard.php?h=PC-9801), and [Sharp X1](https://www.amusement-center.com/project/egg/landing/hard.php?h=X1) pages. Frames with Project EGG command overlays, clipped text, or characters that could not be determined from the image pixels were rejected before references were frozen. The retained set spans 28 games and preserves each exact screenshot URL, catalog page, SHA-256, and dimensions.

The retro outside batch includes 12 commit-pinned Super Famicom captures from two games in Jo-Mako's archived reading corpus. Six transcriptions from the corpus were checked against the selected images. The other six were transcribed directly from the frames because their transcript index does not reliably match the image numbering. They remain single-review. Four additional Mega Drive captures come from official SEGA pages.

The other outside batch includes two pinned screenshots from the [Light.vn repository](https://github.com/hsdk123/Light.vn), four individually marked public-domain PC-88 text screenshots from [Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:Japanese-language_video_game_screenshots), a public-domain non-Japanese menu negative, and MeikiOCR's own [demo image](https://github.com/rtr46/meikiocr). The vendor demo is diagnostic only because training/tuning overlap is plausible.

For an image to become verified:

1. Keep the untouched full capture; create a separate sample if a crop or exclusion mask is part of the test.
2. Freeze an image-only transcription before inspecting output from any model under test. Never seed or correct a reference with a baseline or candidate prediction.
3. Transcribe every Japanese character plus associated Latin letters/digits that the application should pass through. Do not transcribe decorative icons such as selection arrows.
4. Preserve visible punctuation and elongated/repetition marks. Do not silently correct spelling in the game or reconstruct text from external scripts or context.
5. Mark partly hidden text, translation overlays, OCR boxes, Ludivoxa UI, and ambiguous mixed-layout ordering as unscored robustness data rather than guessing.
6. Have a second reviewer compare the transcription against the exact image without seeing model output. Record any intentional exclusions in annotation.notes.
7. Balance the verified set across dialogue, menus, battle/HUD, mixed layouts, tiny/blurred text, stylized fonts, vertical text, and no-text negatives.
8. Reserve a private holdout captured after candidate selection; public demos and model-project examples must never decide promotion.

Exact duplicates are rejected by SHA-256 today. Perceptual duplicate detection should be added before further growth or any promotion decision so adjacent frames cannot create false confidence.

## Current limitations

- The existing 13-image set covers one sequence from a single game. Its inherited ground truth has only one review.
- Every currently scoreable transcription still has only one independent review; none can satisfy the promotion gate yet.
- Bounding boxes are retained in result JSON, but the initial promotion metric is end-to-end text CER. Region-level IoU/precision/recall should be added once real boxes are double-annotated.
- Public internet images are useful evaluation material, not a genuinely secret holdout.
- URL-hosted assets can disappear or change upstream; preparation fails closed on a hash or dimension mismatch, but durable use may require permission to redistribute a frozen corpus.
- The built-in runner targets MeikiOCR-compatible packages. A different OCR architecture should emit the same result schema and use the existing compare command, while preserving identical application post-processing where applicable.

Run the non-networked unit tests with:

~~~powershell
.\.venv\Scripts\pytest.exe tests\test_ocr_benchmark.py
~~~

## OneOCR and PaddleOCR on pixel dialogue

The pixel comparison runs both OCR engines locally on identical dialogue crops
and reports character error rate, exact matches, empty results, and p50/p95
wall time. It keeps each engine in its own process and times fresh recognitions
with the model loaded. Paddle runs the production recognizer directly on CPU.
OneOCR runs the native Snipping Tool engine through its Python wrapper in the
benchmark environment. Capture, worker IPC, websocket transport, translation and
speech are outside the timed interval.

Generate the synthetic set on Windows and run each engine against its manifest.
Install [`oneocr`](https://github.com/AuroraWright/oneocr) in the benchmark
environment and point it at a local directory containing the three runtime
files from Snipping Tool. Paddle needs the project’s `paddle` extra and local
inference models. Engine binaries and models are provisioned separately.

```powershell
python benchmark/ocr/pixel_fixtures.py .test-results/pixel
python benchmark/ocr/pixel_compare.py oneocr .test-results/pixel/manifest.json .test-results/pixel/oneocr.json --oneocr-dir C:/path/to/oneocr --repeats 7
python benchmark/ocr/pixel_compare.py paddle .test-results/pixel/manifest.json .test-results/pixel/paddle.json --detector C:/path/to/detector --recognizer C:/path/to/recognizer --repeats 7
```

For your own captures, the manifest contains `samples`. Each item has `image`
(relative to the manifest directory), `group` and a manually transcribed
`text`. Use `--group` to select the same subset for each run. The reports
include input hashes, transcriptions and predictions. Keep private samples
and raw reports local.

### Windows CPU measurements

Intel Core i7-13700F, OneOCR wrapper 1.0.12 with Snipping Tool 11.2609.15.0,
PaddleOCR 3.7.0 with ONNX, two Paddle intra-op threads, limit side 640 and
recognition confidence 0.6. Three warmups and seven randomized timed
repetitions per crop. The v5 run uses the English mobile recognizer for English
and the general mobile recognizer for Japanese. Values show **p50 / p95 in
milliseconds** and **CER**, with Unicode width and whitespace normalized.

| Crops | Count | OneOCR | PP-OCRv5 Mobile | PP-OCRv6 Small |
| --- | ---: | ---: | ---: | ---: |
| Recorded GBA, English | 2 | 50.89 / 55.08 · 4.65% | 82.70 / 114.22 · 3.49% | 55.86 / 61.81 · 9.30% |
| Synthetic English, 1× | 6 | 25.82 / 37.47 · 0.00% | 31.86 / 39.87 · 0.00% | 37.58 / 43.98 · 2.78% |
| Synthetic English, 3× | 6 | 46.48 / 53.21 · 0.00% | 45.73 / 51.85 · 0.46% | 44.58 / 50.48 · 0.00% |
| Synthetic Japanese, 1× | 6 | 40.42 / 49.29 · 1.47% | 35.09 / 37.90 · 1.47% | 36.51 / 40.79 · 0.00% |
| Synthetic Japanese, 3× | 6 | 63.16 / 70.87 · 2.21% | 49.59 / 58.56 · 7.35% | 46.30 / 50.36 · 1.47% |

OneOCR had 38% lower median latency than v5 on the two recorded crops. v5
had the lowest aggregate character error on those crops. v6 Small did best
on character error for the synthetic Japanese set. The synthetic samples
reuse six passages per language at two scales. Only the English run includes
two recorded crops, so this measures a narrow workload. The application
continues to use its configured OCR engine.

### Follow-up on recorded dialogue

A follow-up used five randomized repetitions per image on the same two English
crops and 15 full Japanese retro screenshots from the earlier OCR comparison.
Both engines received identical images within each group.

| Input | Images | OneOCR p50 / CER | PP-OCRv5 Mobile p50 / CER |
| --- | ---: | ---: | ---: |
| Recorded English dialogue crops | 2 | 176.29 ms / 4.65% | 116.99 ms / 3.49% |
| Japanese retro screenshots | 15 | 273.89 ms / 24.15% | 594.75 ms / 31.72% |

The English result reversed the ordering in the earlier run. The Japanese
references cover dialogue within full screenshots, so text picked up elsewhere
counts toward CER. These measurements cover warm OCR calls. Capture,
translation and speech latency require a separate comparison.
