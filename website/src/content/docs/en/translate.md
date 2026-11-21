---
title: Translate English and Japanese game dialogue
description: Capture a game window, select multiple dialogue regions for offline OCR, and configure per-game language settings.
---

1. Open the game in windowed or borderless mode. In Ludivoxa select **Refresh**, choose the game window, and select **Start Capture** when the engines are ready.
2. Open **Configure OCR**, choose **Only selected boxes**, draw the dialogue boxes, and select **OK**.
3. Choose **English** with `en_PP-OCRv5_mobile_rec` for English games. Choose **Japanese** or **Auto** with `PP-OCRv5_mobile_rec` for Japanese or mixed dialogue.
4. Choose **Overlay only**, **TTS only**, or **Overlay + TTS**. For other target languages, use **Overlay only**. The bundled voices read Vietnamese.

Boxes are saved per game window. Move or resize the boxes if the dialogue moves. Use **Full window (use exclusions)** to scan the window while omitting selected areas.

## History and game profiles

**History** keeps up to 100 translated entries for the session. Select a line to replay its Vietnamese speech, copy the translation, or translate it again. On Windows, the floating **History** button is available during game-window capture.

Use **Game profile…** to set the source language, LLM prompt, or full OCR lines to ignore for that game. For example, add `Press START` to skip a matching OCR line. The app resets translation context on game changes.
