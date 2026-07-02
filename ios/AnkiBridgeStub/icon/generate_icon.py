#!/usr/bin/env python3
"""Generate the Bauhaus "Rating Spectrum" app icon for AnkiBridgeStub.

Draws the 1024x1024 master deterministically from the locked Bauhaus palette
and the system Futura font, then writes it into the app's asset catalog along
with the AppIcon.appiconset / Assets.xcassets Contents.json files.

Design: see docs/superpowers/specs/2026-07-02-ios-app-icon-bauhaus-design.md
Run:    python3 ios/AnkiBridgeStub/icon/generate_icon.py
Deps:   Pillow, macOS system Futura (/System/Library/Fonts/Supplemental/Futura.ttc)
"""
import json
import os

from PIL import Image, ImageDraw, ImageFont

# --- locked Bauhaus palette -------------------------------------------------
RED = (226, 35, 26)      # #E2231A  Again
YELLOW = (242, 194, 0)   # #F2C200  Hard
GREEN = (46, 158, 79)    # #2E9E4F  Good
BLUE = (30, 82, 168)     # #1E52A8  Easy
INK = (26, 26, 26)       # #1A1A1A  grid + disc
PAPER = (245, 241, 230)  # #F5F1E6  "G"

FONT_PATH = "/System/Library/Fonts/Supplemental/Futura.ttc"

SS = 3            # supersample factor for clean edges
BASE = 1024       # delivered master size
S = BASE * SS     # working render size

# paths relative to this script: icon/ -> ../AnkiBridgeStub/Assets.xcassets/...
HERE = os.path.dirname(os.path.abspath(__file__))
XCASSETS = os.path.normpath(os.path.join(HERE, "..", "AnkiBridgeStub", "Assets.xcassets"))
APPICONSET = os.path.join(XCASSETS, "AppIcon.appiconset")
PNG_NAME = "AppIcon-1024.png"


def load_futura(size):
    """Pick the heaviest upright, non-condensed Futura face available."""
    best, best_score = None, -1
    for idx in range(12):
        try:
            f = ImageFont.truetype(FONT_PATH, size, index=idx)
        except Exception:
            break
        try:
            name = " ".join(f.getname()).lower()
        except Exception:
            name = ""
        score = 0
        if "italic" in name:
            score -= 100
        if "condensed" in name:
            score -= 40
        if "extrabold" in name:
            score += 30
        elif "bold" in name:
            score += 20
        elif "medium" in name:
            score += 5
        if score > best_score:
            best_score, best = score, f
    if best is None:
        raise SystemExit(f"Could not load Futura from {FONT_PATH}")
    return best


def render_master():
    img = Image.new("RGB", (S, S), INK)
    d = ImageDraw.Draw(img)
    gap = S * 0.026                 # thick ink grid rule
    half = (S - gap) / 2
    d.rectangle([0, 0, half, half], fill=RED)          # top-left  Again
    d.rectangle([half + gap, 0, S, half], fill=YELLOW) # top-right Hard
    d.rectangle([0, half + gap, half, S], fill=GREEN)  # bot-left  Good
    d.rectangle([half + gap, half + gap, S, S], fill=BLUE)  # bot-right Easy
    cr = S * 0.205                  # center ink disc
    d.ellipse([S / 2 - cr, S / 2 - cr, S / 2 + cr, S / 2 + cr], fill=INK)
    gf = load_futura(int(S * 0.285))
    b = d.textbbox((0, 0), "G", font=gf)
    gw, gh = b[2] - b[0], b[3] - b[1]
    d.text((S / 2 - gw / 2 - b[0], S / 2 - gh / 2 - b[1]), "G", font=gf, fill=PAPER)
    return img.resize((BASE, BASE), Image.LANCZOS)


def write_catalog(master):
    os.makedirs(APPICONSET, exist_ok=True)
    # opaque RGB, no alpha (asset-catalog / marketing-size requirement)
    master.convert("RGB").save(os.path.join(APPICONSET, PNG_NAME))
    appicon_contents = {
        "images": [
            {"filename": PNG_NAME, "idiom": "universal",
             "platform": "ios", "size": "1024x1024"}
        ],
        "info": {"author": "xcode", "version": 1},
    }
    with open(os.path.join(APPICONSET, "Contents.json"), "w") as f:
        json.dump(appicon_contents, f, indent=2)
        f.write("\n")
    with open(os.path.join(XCASSETS, "Contents.json"), "w") as f:
        json.dump({"info": {"author": "xcode", "version": 1}}, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    write_catalog(render_master())
    print(f"Wrote icon + catalog under {XCASSETS}")
