"""Generate a reproducible Windows pixel-font dialogue set with exact references."""

import argparse
import os
from pathlib import Path

from benchlib import write_json
from PIL import Image, ImageDraw, ImageFont

DIALOGUE = {
    "en": (
        ("Find the old lantern.", "Meet me before dawn!"),
        ("We should leave before dark.", "Bring the map."),
        ("Mina found 10 silver coins.", "The gate opens at 07:30."),
        ("Watch out!", "The bridge is broken."),
        ("Choose a partner.", "Press START to continue."),
        ("I heard another noise.", "It came from the cellar."),
    ),
    "ja": (
        ("気をつけて。", "橋が壊れています。"),
        ("明日の朝、駅で会いましょう。", "古いランタンを持ってきて。"),
        ("勇者は銀貨を１０枚見つけた。", "門は朝の７時に開く。"),
        ("この鍵で北の門を開けられる。", "地図を持っていこう。"),
        ("そこに誰かいるのですか？", "地下室から音がした。"),
        ("セーブデータを読み込みます。", "少々お待ちください。"),
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--fonts", type=Path, default=Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    for language, dialogues in DIALOGUE.items():
        font = ImageFont.truetype(args.fonts / ("arial.ttf" if language == "en" else "msgothic.ttc"), 12)
        for index, lines in enumerate(dialogues):
            background, foreground = ("#211f36", "#fff5e8") if index % 2 else ("#f8f8f0", "#111328")
            crop = Image.new("RGB", (240, 52), background)
            draw = ImageDraw.Draw(crop)
            draw.fontmode = "1"
            for line, text in enumerate(lines):
                draw.text((8, 5 + line * 20), text, font=font, fill=foreground)
            for scale in (1, 3):
                name = f"{language}-{index:02d}-{scale}x.png"
                crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.NEAREST).save(
                    args.output_dir / name
                )
                samples.append({"image": name, "group": f"synthetic-{language}-{scale}x", "text": " ".join(lines)})
    write_json(args.output_dir / "manifest.json", {"samples": samples})


if __name__ == "__main__":
    main()
