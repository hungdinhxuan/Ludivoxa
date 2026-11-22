---
title: Translate game dialogue with offline OCR
description: Capture a game window, select multiple dialogue regions for offline OCR, and configure per-game language settings.
---

Use **Interface language** at the bottom of the sidebar to choose **English** or **Tiếng Việt**, then reopen Ludivoxa.

1. Open the game in windowed or borderless mode. In Ludivoxa select **Refresh**, choose the game window, and select **Start Capture** when the engines are ready.
2. Open **Configure OCR**, choose **Only selected boxes**, draw the dialogue boxes, and select **OK**.
3. Choose **English** with `en_PP-OCRv5_mobile_rec` for English games. Choose **Japanese** or **Auto** with `PP-OCRv5_mobile_rec` for Japanese or mixed dialogue.
4. In **Engines → Translate to**, select **Vietnamese**, **English**, **Simplified Chinese**, **Traditional Chinese**, **Japanese**, or **Korean** and select **Apply**. You can also type a language supported by your model.
5. Choose **Overlay only**, **TTS only**, or **Overlay + TTS**. The bundled voices speak Vietnamese. From v2.1.0, other supported targets can use an optional [local clone server](../voice/).

Boxes are saved per game window. Move or resize the boxes if the dialogue moves. Use **Full window (use exclusions)** to scan the window while omitting selected areas.

Select **Chinese** with PaddleOCR for Simplified or Traditional Chinese. Select **Korean** with PaddleOCR for Korean games. Each option selects a local recognition model. Use an explicit source language for Han-only dialogue so the translator receives the correct language.

## History and game profiles

**History** keeps up to 100 translated entries for the session. Select a line to replay its configured voice, copy the translation, or translate it again. On Windows, the floating **History** button is available during game-window capture.

Open **Tools → Game profile…** to set the source language, LLM prompt, or full OCR lines to ignore for that game. For example, add `Press START` to skip a matching OCR line. The app resets translation context on game changes.

Choose **Append to Engines prompt** to keep the shared translation rules and add game-specific instructions, such as a glossary or forms of address. Choose **Override Engines prompt** to supply the full translation prompt for that game. A blank field uses the Engines prompt, with the built-in prompt as fallback. Existing profiles with a saved prompt retain Override mode.

You can write game instructions in Vietnamese or another language your LLM understands. Ludivoxa fills in `{source_language}` and `{target_language}` in both prompt modes. The translation target comes from **Engines → Translate to** when the shared prompt is used. Include the desired target or `{target_language}` in a full override.
