# tools/release.py
"""
One command from code to every PC: bump the version, build, zip, publish on GitHub.
Every copy of the macro then sees it (on launch, every 3 hours, or Discord /update) and
installs it itself - see modules/self_update.py.

  release.bat 3.6.1                      (or: .venv\\Scripts\\python tools\\release.py 3.6.1)
  release.bat 3.6.1 --notes "Fixed X"    release notes (default: the last commit's message)
  release.bat 3.6.1 --no-upload          build + zip only, nothing published

What it does:
  1. config.APP_VERSION -> the new version; that change is committed on its own if it's
     the only thing left uncommitted in config.py.
  2. git push (the release's tag has to point at code that's on GitHub).
  3. PyInstaller build of macro_slop_qt.spec into build_release/ (never into dist*/,
     which may hold someone's data).
  4. releases/Luckis_Macro_v<version>.zip - the name is what the updater looks for.
  5. gh release create v<version> with the zip, marked latest.

Needs the GitHub CLI signed in once: gh auth login
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "Lucki's Macro"
BUILD_DIR = os.path.join(ROOT, "build_release")
OUT_DIR = os.path.join(ROOT, "releases")


def run(cmd, check=True, capture=False):
    print("> " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=capture)
    if check and r.returncode != 0:
        if capture:
            print(r.stdout + r.stderr)
        sys.exit(f"\nFAILED: {' '.join(cmd)}")
    return r


def set_version(version):
    path = os.path.join(ROOT, "config.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(r'^APP_VERSION = "[^"]*"', f'APP_VERSION = "{version}"', text, count=1, flags=re.M)
    if n != 1:
        sys.exit("Couldn't find APP_VERSION in config.py")
    if new == text:
        print(f"config.py is already at {version}.")
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"config.py -> APP_VERSION = \"{version}\"")
    return True


def default_notes():
    msg = run(["git", "log", "-1", "--format=%B"], capture=True).stdout
    lines = [l for l in msg.splitlines() if not l.lower().startswith("co-authored-by")]
    return "\n".join(lines).strip()


def build():
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "macro_slop_qt.spec", "--clean", "--noconfirm",
         "--distpath", os.path.join(BUILD_DIR, "dist"), "--workpath", os.path.join(BUILD_DIR, "work")])
    app = os.path.join(BUILD_DIR, "dist", APP_NAME)
    if not os.path.isfile(os.path.join(app, APP_NAME + ".exe")):
        sys.exit(f"The build didn't produce {APP_NAME}.exe")
    return app


def make_zip(app, version):
    os.makedirs(OUT_DIR, exist_ok=True)
    zip_path = os.path.join(OUT_DIR, f"Luckis_Macro_v{version}.zip")
    print(f"Zipping -> {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(app):
            for name in files:
                path = os.path.join(root, name)
                z.write(path, os.path.join(APP_NAME, os.path.relpath(path, app)))
    print(f"  {os.path.getsize(zip_path) / 1e6:.0f} MB")
    return zip_path


def main():
    ap = argparse.ArgumentParser(description="Build and publish a release.")
    ap.add_argument("version", nargs="?", help="e.g. 3.6.1")
    ap.add_argument("--notes", help="release notes (default: the last commit's message)")
    ap.add_argument("--no-upload", action="store_true", help="build and zip only")
    args = ap.parse_args()

    version = (args.version or input("Version to release (e.g. 3.6.1): ")).strip().lstrip("vV.")
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        sys.exit(f"'{version}' isn't a version number like 3.6.1")
    tag = f"v{version}"

    if not args.no_upload:
        if shutil.which("gh") is None:
            sys.exit("The GitHub CLI (gh) isn't installed - https://cli.github.com")
        if run(["gh", "auth", "status"], check=False, capture=True).returncode != 0:
            sys.exit("Sign in to GitHub once first:  gh auth login")
        if run(["gh", "release", "view", tag], check=False, capture=True).returncode == 0:
            sys.exit(f"A release {tag} already exists - pick the next version number.")

    if set_version(version):
        others = run(["git", "diff", "--name-only"], capture=True).stdout.split()
        run(["git", "add", "config.py"])
        run(["git", "commit", "-m", f"Version {version}", "--", "config.py"])
        if "config.py" in others:
            print("(config.py had other uncommitted changes - they went into that commit too)")

    if not args.no_upload:
        run(["git", "push", "origin", "HEAD"])

    app = build()
    zip_path = make_zip(app, version)

    if args.no_upload:
        print(f"\nBuilt {zip_path} - not published (--no-upload).")
        return

    notes = args.notes or default_notes() or f"Update {version}"
    notes_file = os.path.join(BUILD_DIR, "notes.md")
    with open(notes_file, "w", encoding="utf-8") as f:
        f.write(notes)
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True).stdout.strip()
    run(["gh", "release", "create", tag, zip_path, "--title", f"Update {version}",
         "--notes-file", notes_file, "--target", branch, "--latest"])
    print(f"\nPublished {tag}. Every macro picks it up on launch, within 3 hours, or with /update on Discord.")


if __name__ == "__main__":
    main()
