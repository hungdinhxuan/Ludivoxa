---
title: On-screen subtitles and offline voices
description: Use an overlay or local Vietnamese TTS, set speech volume, and assign voices to game characters.
---

Choose **Overlay only**, **TTS only**, or **Overlay + TTS** in the session controls. Press **F8** to show or hide the overlay. Use **Stop reading** to stop speech.

Choose a **Voice model** and **Speaker**, and use **Test voice** to hear it. Adjust **Speaking speed** and **Speech volume** independently. The volume applies from the next playback, including previews and History replay. VieNeu Nano supports cloning from a local reference recording.

Ludivoxa can clone a voice for English, Japanese, Korean and Mandarin Chinese. With one of these targets selected, **Play → Session** displays **Qwen3-TTS Base (local clone)** as the Voice model. Start a local llama.cpp server with Qwen3-TTS Base, enter its address under **Local clone server**, then select a WAV or MP3 under **Clone from audio** and use **Test voice**. [Setup instructions](https://github.com/hungdinhxuan/Ludivoxa/blob/main/docs/USER_GUIDE.md#clone-giọng-đa-ngôn-ngữ)

If you want the overlay to clear when the dialogue changes, turn on **Clear translation when dialogue changes** under **Overlay**.

## Voices by character name

1. Choose your game window and open **Character voices…**.
2. Choose **Select name area…** and mark the speaker label.
3. Add the name as it appears in the game, choose a voice preset and optionally assign the speaker's gender.
4. Enable automatic voice selection and save the settings.

Speaker labels guide voice selection and the LLM's forms of address. The speech output reads the dialogue.
