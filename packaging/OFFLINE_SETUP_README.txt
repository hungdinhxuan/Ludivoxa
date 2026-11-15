Ludivoxa 1.1.1 - Windows release

Keep the Setup.exe and every Setup-*.bin file in the same directory. Run Setup.exe.
The installer is per-user and does not require administrator rights. Python and
a separate Ollama installation are not required. Model files are included.

For an existing installation, Ludivoxa-1.1.1-App-Update.exe contains only the
app and libraries. Select the existing installation directory; configuration,
models and Ollama are kept. It cannot create a fresh offline installation.
Version 1.0.0 users must upgrade manually once. From 1.1.0, open Updates to
check/download/install future stable releases. Public GitHub access needs no
key or sign-in. Startup checks are optional; installation requires a click.

Open Ludivoxa from the Start menu or installed Interpreter.exe. Its private
Ollama service listens only on 127.0.0.1:11435 and uses the included HauhauCS
Qwen3 4B Aggressive Q4_K_M model. It does not alter an existing Ollama server on
11434. No model download or cloud inference fallback is used. The first model
load can take longer than subsequent dialogue translations; wait for Ready.
Closing the last Ludivoxa window from this installation stops its private
Ollama and model runners, including when the running app is forcibly ended.
Other Ludivoxa windows from the same installation keep the shared private
service alive. External local/LAN services are left under their owner's control.
After closing the private runtime, the model must reload the next time you open
the app. Model files stay on disk and remain available offline.

Open the game, select Refresh, choose the game window, then Start Capture.
Output can be Overlay only, TTS only, or Overlay + TTS. F8 hides/shows the overlay.
Stop reading cancels speech. Configure OCR can exclude unwanted screen regions.
To scan only the dialogue, open Configure OCR and choose Scan: Only selected
box. Draw the blue box and press OK. Move/resize it, enter percentages, or use
Use bottom dialogue box. Clear Box lets you redraw. The selection is saved per
game. Full window (use exclusions) restores the previous exclusion workflow.
The editor previews OCR without translating or speaking while you adjust it.
Speech filters decorative symbols (sqrt/check marks/arrows/emoji/markup) before
synthesis. The displayed translation is unchanged; normal letters, accents,
numbers, sentence punctuation, currency and percentages are preserved.

The English speed profile is selected. For Japanese or mixed English/Japanese,
choose Auto/Japanese and models/PP-OCRv5_mobile_rec as the recognition directory.
The included CPU OCR models also contain official ONNX exports, selected by
PaddleOCR when present in both model folders. Existing saved paths remain valid.
Capture polls every 150 ms; preview painting remains at 500 ms. Longer dialogue
still takes longer to recognize, and speech waits for text to settle.
Exact recent frames are cached to avoid recognizing cursor-blink states again.
Speech also guards against minor panel shading and requeuing the translation
currently playing when OCR oscillates between variants.

VieNeu Nano has 11 preset voices. No personal recording is included. To clone a
voice later, select a clean local recording in Clone from audio; no transcript
is required. Clear that optional field to return to a preset. Piper's original
voices are also included. Reference files are never uploaded.
Nano uses eight inference steps on two CPU threads for faster generation; this
can sound rougher than sixteen steps. Obsolete synthesis is cancelled between
chunks. Nano plays generated audio chunks through one native stream; complete
translation sentences can start speech before the LLM response finishes.
Callback-only audio drivers and Piper retain buffered playback. Waiting for
the current spoken line to finish still adds delay. The default prompt keeps
the source's tone and intensity; an existing custom prompt remains in control.

Settings are in config.yml beside Interpreter.exe. Upgrades and uninstall keep
this settings file. Uninstall stops only the Ollama service owned by this
installation. External recordings and unrelated Ollama services are untouched.
Logs are in %USERPROFILE%\.interpreter\logs.

This is an unsigned Ludivoxa build derived from Interpreter, not an upstream
Interpreter release. See LICENSE,
THIRD_PARTY_NOTICES.txt and model cards for bundled component terms.
