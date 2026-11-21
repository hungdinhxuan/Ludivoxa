---
title: Set up local Ollama or a LAN translation server
description: Configure the bundled Ollama runtime, an existing local Ollama server, or an OpenAI-compatible LAN endpoint in Ludivoxa.
---

The offline package starts a private Ollama runtime on `http://127.0.0.1:11435` with the Qwen translation model. The runtime stops when the last app instance closes.

For your own server, select an LLM provider in **Engines**, enter its URL and model name, and choose **Test → Apply**.

| Provider | Example URL |
| --- | --- |
| Your local Ollama instance | `http://127.0.0.1:11434` |
| Ollama on your LAN | `http://192.168.1.10:11434` |
| OpenAI-compatible server on your LAN | `http://192.168.1.10:8000/v1` |

Start the server before testing the endpoint and make sure your PC can reach it. Ludivoxa uses the endpoint you configure while translating. Update checks use GitHub separately and can be turned off. Shared local and LAN servers run independently of the app.
