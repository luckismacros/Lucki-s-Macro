# tools/template_check.py
"""
Offline template validator.

Answers "which templates would still match right now, and by how much?" without
running the bot. That question comes up constantly and used to need a live run plus
reading confidence numbers out of a console the built exe doesn't have:

  - after a game update moved or restyled a button, which templates broke?
  - the bot stopped and left a screenshot in debug/ - what did it fail to see?
  - a friend's laptop runs at a smaller scale - do the templates survive the resize?
  - is a threshold slightly too strict, or is the template outright stale?

Confidence tells those apart. A stale template sits low (< 0.5) no matter what; a
threshold that's merely too strict sits just under the line.

Usage:
  python tools/template_check.py                     # capture the screen and check it
  python tools/template_check.py debug/              # check every image in a folder
  python tools/template_check.py shot.png            # check one image
  python tools/template_check.py --scale 0.71        # simulate a smaller screen
  python tools/template_check.py shot.png -t play    # only templates matching "play"
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2

import config
import vision

TEMPLATE_DIR = "assets/templates"


def _all_templates():
    # Normalized to forward slashes: config.py's template path constants (and
    # TEMPLATE_THRESHOLDS keys) are always written with forward slashes, but
    # os.path.join produces backslashes on Windows for the subfolder glob - which
    # would silently miss every threshold override for a templates/<subfolder>/ file
    # and report the wrong "needs" value here without ever affecting the real bot
    # (which never builds its paths through glob).
    paths = sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.png")))
    paths += sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*", "*.png")))
    return [p.replace(os.sep, "/") for p in paths]


def _load_images(source):
    """Returns [(name, image), ...] from a file, a directory, or a live capture."""
    if source is None:
        print("Capturing the current screen...")
        return [("<live screen>", vision.capture_screen())]

    if os.path.isdir(source):
        files = sorted(glob.glob(os.path.join(source, "*.png")) +
                       glob.glob(os.path.join(source, "*.jpg")))
        if not files:
            print(f"No images found in {source}")
            return []
        return [(os.path.basename(f), cv2.imread(f)) for f in files]

    if os.path.isfile(source):
        return [(os.path.basename(source), cv2.imread(source))]

    print(f"Not a file or directory: {source}")
    return []


# How far either side of the expected size to look for a template's real best fit, and
# how finely. A template captured against the wrong client is the failure this sweep
# exists to name: it matches nothing at the size the bot uses, while sitting at a clean
# peak somewhere else - which is indistinguishable from "stale template" without it.
# +-20% covers every plausible mix-up between the sizes this bot pins to; 1% steps are
# fine enough that the peak lands within a couple of percent of the truth.
PEAK_SWEEP_SPREAD = 0.20
PEAK_SWEEP_STEP = 0.01

# How far the peak has to sit from the size actually being used before it is worth
# reporting. Crops are drawn by hand, so a real template's peak wanders a percent or two
# either way; anything past 4% is a different client size, not measurement noise.
PEAK_MISMATCH = 0.04


def _peak_scale(image, path):
    """
    (best confidence, the size that achieved it) for one template, swept over sizes.

    Returned as a multiplier of the size the bot would use right now, so 1.00 means "the
    bot is already matching this at its best size" and 1.11 means "this template wants to
    be 11% bigger than this screen, i.e. it was captured against a smaller client".
    """
    base = vision._load_template(path, scale=1.0)
    centre = config.render_scale()
    rungs = int(round(PEAK_SWEEP_SPREAD * centre / PEAK_SWEEP_STEP))
    best = (-1.0, centre)
    for k in range(-rungs, rungs + 1):
        s = centre + k * PEAK_SWEEP_STEP
        if s <= 0:
            continue
        w = max(1, int(round(base.shape[1] * s)))
        h = max(1, int(round(base.shape[0] * s)))
        if h > image.shape[0] or w > image.shape[1]:
            continue
        interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
        small = cv2.resize(base, (w, h), interpolation=interp)
        conf = float(cv2.matchTemplate(image, small, cv2.TM_CCOEFF_NORMED).max())
        if conf > best[0]:
            best = (conf, s)
    return best[0], best[1] / centre if centre else 1.0


def check(image, templates):
    """Best confidence for every template against one image, worst first."""
    rows = []
    for path in templates:
        try:
            # Measured with matchTemplate directly rather than through find_template():
            # find_template applies config.effective_threshold(), which floors at 0.5,
            # so every genuine confidence below that came back as None and got reported
            # as a flat 0.000 - hiding exactly the numbers this tool exists to show. A
            # stale template reading 0.35 and one reading 0.05 need telling apart.
            tpl = vision._load_template(path)
            if tpl.shape[0] > image.shape[0] or tpl.shape[1] > image.shape[1]:
                rows.append((path, 0.0, None,
                             f"template {tpl.shape[1]}x{tpl.shape[0]} is larger than the image",
                             None))
                continue
            result = cv2.matchTemplate(image, tpl, cv2.TM_CCOEFF_NORMED)
            _, conf, _, loc = cv2.minMaxLoc(result)
            centre = config.to_reference(loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2)

            # Only worth sweeping where the template is plausibly present but
            # underperforming. A template that is simply not on this screen peaks at
            # noise, and reporting the size that noise preferred is worse than silence.
            peak = _peak_scale(image, path) if conf < 0.85 else None
            rows.append((path, float(conf), centre, None, peak))
        except Exception as e:
            rows.append((path, None, None, str(e), None))
            continue
    rows.sort(key=lambda r: (r[1] if r[1] is not None else -1))
    return rows


def main():
    parser = argparse.ArgumentParser(description="Check templates against screenshots.")
    parser.add_argument("source", nargs="?", default=None,
                        help="image file or folder; omit to capture the screen now")
    parser.add_argument("--scale", type=float, default=None,
                        help="override the scale; by default it is derived from each "
                             "image's own width, which is almost always what you want")
    parser.add_argument("-t", "--template", default=None,
                        help="only check templates whose filename contains this")
    args = parser.parse_args()

    templates = _all_templates()
    if args.template:
        templates = [t for t in templates if args.template.lower() in os.path.basename(t).lower()]
    if not templates:
        print("No templates matched.")
        return 1

    images = _load_images(args.source)
    if not images:
        return 1

    exit_code = 0
    for name, image in images:
        if image is None:
            print(f"\nCould not read {name}")
            continue

        # Scale is taken from the image itself unless overridden. A screenshot from a
        # 1366-wide laptop has to be matched against templates shrunk by that same
        # ratio, and getting it wrong fails silently: full-size templates against a
        # smaller screenshot simply miss everything, which reads like every template
        # being broken rather than like a mismatched scale.
        # set_client_rect(), not set_scale(). set_scale() moves SCALE and leaves
        # CLIENT_SIZE at None, which makes vision.native_variant_path() return None for
        # everything - so this tool silently checked only the reference templates and
        # never once looked at the assets/templates@WxH/ variants the bot would actually
        # prefer. A whole variant set could be the wrong size, and the one tool built to
        # notice would report the reference templates as fine.
        if args.scale:
            width = int(round(config.REFERENCE_WIDTH * args.scale))
            height = int(round(config.REFERENCE_HEIGHT * args.scale))
        else:
            width, height = image.shape[1], image.shape[0]
        config.set_client_rect((0, 0), (width, height))
        vision.clear_template_cache()

        note = "" if config.SCALE == 1.0 else f"  scale {config.SCALE:.4f}"
        print(f"\n=== {name}  ({image.shape[1]}x{image.shape[0]}){note} ===")
        print(f"{'template':<40} {'conf':>7}  {'needs':>7}  {'':<4} where")
        print("-" * 92)

        wrong_size = []
        for path, conf, pos, err, peak in check(image, templates):
            label = os.path.relpath(path, TEMPLATE_DIR)
            if err and conf is None:
                print(f"{label:<40} {'ERROR':>7}           {err}")
                exit_code = 1
                continue

            # shrunk= exactly as vision.find_template() decides it, or this prints a bar
            # the bot does not actually apply. Without a native variant the reference
            # template is being resized, which earns the large relief - so the tool was
            # reporting 0.825 for templates the bot was really admitting at 0.75, and a
            # template sitting between the two read as failing here while passing there.
            shrunk = vision._usable_variant(path) is None
            needed = config.effective_threshold(path, config.MATCH_THRESHOLD, shrunk=shrunk)
            if conf >= needed:
                mark, where = "OK  ", f"({pos[0]}, {pos[1]})"
            elif conf >= 0.5:
                mark, where = "NEAR", "just under the line - try a lower threshold"
                exit_code = 1
            else:
                mark, where = "MISS", "not on screen, or the template is stale"
                exit_code = 1

            # A clean high peak at the wrong size outranks both of those readings: the
            # template is neither stale nor merely under-threshold, it is the right
            # picture at the wrong size, and re-cropping or re-tuning it would be
            # wasted work. Say which size it wants, because that number identifies the
            # client it was really captured against.
            if peak and peak[0] >= 0.80 and abs(peak[1] - 1.0) > PEAK_MISMATCH:
                # peak[1] is a multiplier of render_scale(), so the absolute resize the
                # template really wants is the two multiplied. A template native to a
                # W-wide client needs resizing by image_width / W to fit this frame, so
                # W is image_width divided by that absolute figure - NOT by the
                # multiplier alone, which leaves out the scale it is a multiple of.
                absolute = peak[1] * config.render_scale()
                captured_at = int(round(image.shape[1] / absolute)) if absolute else 0
                mark = "SIZE"
                where = (f"wrong size - peaks {peak[0]:.3f} at {peak[1]:.2f}x, i.e. "
                         f"captured against a ~{captured_at}px-wide client")
                wrong_size.append(label)
                exit_code = 1

            print(f"{label:<40} {conf:7.3f}  {needed:7.3f}  {mark} {where}")

        if wrong_size:
            print(f"\n  {len(wrong_size)} template(s) are the RIGHT PICTURE AT THE WRONG "
                  f"SIZE: {', '.join(wrong_size)}")
            print(f"  Every template in {TEMPLATE_DIR}/ must be captured against a "
                  f"{config.REFERENCE_WIDTH}x{config.REFERENCE_HEIGHT} client - that is the "
                  f"space config.SCALE scales down FROM. Captures taken at any other size "
                  f"belong in assets/templates@<W>x<H>/ instead (tools/retemplate.py), never "
                  f"in the reference folder.")

    print("\nOK = would match now.  NEAR = present but under threshold (tune it in "
          "config.TEMPLATE_THRESHOLDS).\nMISS = not in this image at all, or the "
          "template needs re-cropping.\nSIZE = correct artwork captured against the wrong "
          "client size; recapture it, don't retune it.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
