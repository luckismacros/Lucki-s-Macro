# tools/make_ui_art.py
"""
Builds the interface pictures in assets/ui/ (read by ui_qt/art.py by name):

  1. crops from game screenshots in Images_For_Claude/ (portals)
  2. the artwork in assets/vanity/ (maps, raids, mode cards) - these win over 1.
  3. the logo (assets/vanity/Logo.*) -> assets/ui/brand/logo.png + icon.ico

Run:  python tools/make_ui_art.py [contact_sheet.png]
"""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "Images_For_Claude")
VANITY = os.path.join(ROOT, "assets", "vanity")
OUT = os.path.join(ROOT, "assets", "ui")
MAX_WIDTH = 480

# category, key, screenshot, (x0, y0, x1, y1)
CROPS = [
    ("portals", "sovereign", "Portals/1.png", (1285, 345, 1425, 525)),
    ("portals", "skyruins",  "Portals/1.png", (722, 243, 843, 328)),
    ("portals", "summer",    "Portals/1.png", (582, 662, 705, 747)),
]

# category, key, vanity file, trim_bottom (fraction cut off the bottom - the island art
# has its name baked in there, and the app draws its own caption)
VANITY_ART = [
    ("story_maps", "school_grounds",    "School_Grounds.png", 0.15),
    ("story_maps", "flower_forest",     "Flower_Forest.png",  0.15),
    ("story_maps", "rose_kingdom",      "Rose_Kingdom.png",   0.15),
    ("story_maps", "fairy_king_forest", "Fairy_King.png",     0.14),
    ("story_maps", "kings_tomb",        "Kings_Tomb.png",     0.14),
    ("story_maps", "east_town",         "East_Town.png",      0.14),
    ("story_maps", "crimson_shore",     "Crimson_Shore.png",  0.14),
    ("expeditions", "school_grounds",   "School_Grounds.png", 0.15),
    ("expeditions", "flower_forest",    "Flower_Forest.png",  0.15),
    ("expeditions", "rose_kingdom",     "Rose_Kingdom.png",   0.15),
    ("expeditions", "east_town",        "East_Town.png",      0.14),
    ("raids", "spirit_city",            "Spirit_City.png",    0.14),
    ("raids", "snowy_castle",           "Snowy_Castle.png",   0.14),
    ("portals", "lightning",            "Portal_Icon.png",    0.0),
    ("modes", "story",                  "Story_Card.png",     0.0),
    ("modes", "raids",                  "Raid_Card.png",      0.0),
    ("modes", "challenges",             "Challenges_Card.png", 0.0),
    ("modes", "expeditions",            "Expedition_Card.png", 0.0),
    ("modes", "portals",                "Portal_Icon.png",    0.0),
]


def _fit(img, max_width=MAX_WIDTH):
    if img.shape[1] > max_width:
        h = int(round(img.shape[0] * max_width / img.shape[1]))
        img = cv2.resize(img, (max_width, h), interpolation=cv2.INTER_AREA)
    return img


def _save(category, key, img, made):
    os.makedirs(os.path.join(OUT, category), exist_ok=True)
    dest = os.path.join(OUT, category, f"{key}.png")
    cv2.imwrite(dest, img)
    made.append((f"{category}/{key}", img))
    print(f"wrote {category}/{key}.png {img.shape[1]}x{img.shape[0]}")


def _logo(made):
    src = next((os.path.join(VANITY, f) for f in os.listdir(VANITY) if f.lower().startswith("logo.")), None)
    if src is None:
        print("no logo in assets/vanity")
        return
    img = cv2.imread(src)
    h, w = img.shape[:2]
    side = min(h, w)
    square = img[(h - side) // 2:(h - side) // 2 + side, (w - side) // 2:(w - side) // 2 + side]
    logo = cv2.resize(square, (512, 512), interpolation=cv2.INTER_AREA)
    _save("brand", "logo", logo, made)
    icon_png = os.path.join(OUT, "brand", "icon_256.png")
    cv2.imwrite(icon_png, cv2.resize(square, (256, 256), interpolation=cv2.INTER_AREA))
    from PySide6.QtGui import QImage
    ok = QImage(icon_png).save(os.path.join(OUT, "brand", "icon.ico"), "ICO")
    os.remove(icon_png)
    print(f"wrote brand/icon.ico ({'ok' if ok else 'FAILED'})")


def main(sheet_path=None):
    made = []
    for category, key, source, (x0, y0, x1, y1) in CROPS:
        img = cv2.imread(os.path.join(SHOTS, source))
        if img is None:
            print(f"skip {category}/{key}: can't read {source}")
            continue
        _save(category, key, _fit(img[y0:y1, x0:x1]), made)

    for category, key, filename, trim in VANITY_ART:
        img = cv2.imread(os.path.join(VANITY, filename))
        if img is None:
            print(f"skip {category}/{key}: can't read {filename}")
            continue
        if trim:
            img = img[:int(round(img.shape[0] * (1 - trim)))]
        _save(category, key, _fit(img), made)

    _logo(made)

    if sheet_path and made:
        tiles = []
        for name, crop in made:
            tile = np.full((230, 300, 3), 24, np.uint8)
            s = min(280 / crop.shape[1], 190 / crop.shape[0])
            small = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))))
            tile[10:10 + small.shape[0], 10:10 + small.shape[1]] = small
            cv2.putText(tile, name, (10, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)
            tiles.append(tile)
        while len(tiles) % 4:
            tiles.append(np.full((230, 300, 3), 24, np.uint8))
        cv2.imwrite(sheet_path, np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]))
        print(f"sheet {sheet_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
