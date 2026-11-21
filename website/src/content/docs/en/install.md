---
title: Install Ludivoxa offline on Windows
description: Download the Windows x64 offline installer, verify its files, and update Ludivoxa without redownloading your models.
---

## First installation

1. Download the setup EXE, all three BIN files and `SHA256SUMS.txt` from the [v1.1.1 full offline release](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.1.1).
2. Verify the files against `SHA256SUMS.txt`. Put the EXE and the three BIN files together, then run the EXE.
3. Open **Updates → Check for updates** to install the latest stable app version.

The full package includes Python, Ollama, the Qwen translation model, English and Japanese OCR models, and Vietnamese speech. It is about 4.9 GB to download and 6.4 GB once installed. Download the files under **Assets**. The Windows installer is unsigned.

## Update an existing installation

Open **Updates → Check for updates**, choose a download, and select **Install and restart** once verification finishes. Starting with v1.4.0, the updater offers a smaller patch if the release provides one and your app files match. It verifies downloads with SHA-256.

For a manual upgrade, download the standard **App-Update.exe** and choose the existing Ludivoxa directory. The update keeps your configuration, local models, and Ollama runtime. [Latest stable release](https://github.com/hungdinhxuan/Ludivoxa/releases/latest)
