---
title: Troubleshoot OCR, audio and Remote Desktop
description: Use Diagnostics to resolve missing frames, OCR errors and Vietnamese TTS playback issues over Windows Remote Desktop.
---

Open **Diagnostics** to see the capture backend, frame status, latest OCR and translation time, and speech state. Use **Copy report** when reporting an issue. The report omits dialogue, window and device names, and endpoint settings.

| Symptom | What to check |
| --- | --- |
| The app reads menus or HUD text | Select smaller dialogue boxes and check the source language and OCR confidence. |
| Part of a line is missing | Enlarge the selected region, especially for wrapped or scrolling text. |
| The LLM fails to respond | Check the configured URL and model, wait for the engine to be ready, and run **Test** in **Engines**. |
| Speech is silent | Verify **TTS only** or **Overlay + TTS**, check **Speech volume**, and use **Test voice**. |

## Windows Remote Desktop

Ludivoxa can switch capture methods when the Windows session changes and resume when frames become available. Keep the game open in windowed or borderless mode. Some games can show a black preview with the RDP capture method.

Speech follows the Windows default output. When Windows exposes **Remote Audio** as the default, the next line can use it. Check the default and last playback devices in **Diagnostics**.

Settings are saved in `config.yml` beside the EXE. Logs are in `%USERPROFILE%/.interpreter/logs`.
[Open a GitHub issue](https://github.com/hungdinhxuan/Ludivoxa/issues)
