# Building Macro Slop

Packages the bot into a distributable folder (not a single .exe file) - the exe sits
right next to its bundled `assets/`, so nothing needs to be extracted at runtime.

There are two builds, for two different situations.

| | Use it for | Build time | Source recoverable? |
|---|---|---|---|
| **PyInstaller** (`macro_slop.spec`) | your own machines | ~30 s | **Yes** - trivially |
| **Nuitka** (`build_nuitka.bat`) | anything public | several min | No bytecode to extract |

### Which one, and why it matters

PyInstaller bundles `.pyc` bytecode. `pyinstxtractor` plus a decompiler turns that
back into near-original source, comments and all - and you don't even need the
decompiler to read every string in the program, since the bytecode is only zlib'd.
That is fine for your own laptop and much faster to iterate on.

Nuitka compiles the Python to C and then to a native binary. There is no bytecode
left in the output, so reading the logic back means genuinely reverse-engineering a
compiled program rather than running a one-line extraction script. Use it for
anything you hand to people you don't know.

Neither is unbreakable. Nuitka raises the bar from "anyone who can paste a command"
to "someone who actually reverse-engineers binaries", which is the realistic goal.
Note also that `assets/templates/*.png` ship as plain images either way - anyone can
open them and see which UI elements the bot keys on.

## Build - PyInstaller (fast, for yourself)

```
pip install pyinstaller
pyinstaller macro_slop.spec --clean
```

**Always pass `--clean`, or delete the `build/` folder before rebuilding.** Without
it, PyInstaller reuses its cached Analysis from the last build - which went stale
here in a way that was invisible until launch: customtkinter's bundled hook stopped
being re-run, `customtkinter/assets/themes/*.json` silently dropped out of the
shipped folder, and every launch died before the window even opened with
`FileNotFoundError: ...assets/themes/blue.json` - before the log file is even
created, since that crash happens while importing customtkinter, above the point
gui.py installs the logger. `--clean` forces the datas (including hook-contributed
ones) to be recomputed from scratch every time, which costs a little build time and
is worth it every single time.

Output lands in `dist/Macro Slop/` - `Macro Slop.exe` plus its bundled `assets/` and
Python runtime files.

## Build - Nuitka (compiled, for public release)

```
pip install nuitka
build_nuitka.bat
```

Output lands in `dist_nuitka/Macro Slop/`. The script also copies `presets/`,
`movement_presets/` and `challenge_links.json` in for you, so that folder is ready to
zip as-is.

The first run downloads a C compiler (MinGW64) automatically and takes several
minutes; later builds reuse the cache and are quicker.

**Expect antivirus false positives.** Compiled/packed Python executables trip Windows
Defender and friends routinely, and this gets worse with Nuitka, not better. If you're
posting it publicly, expect people to report it as a virus - that's a distribution
problem to plan for (VirusTotal link, source availability, or a signing certificate),
not a sign anything is wrong with the build.

## Before sending it anywhere

`presets/`, `movement_presets/`, and `challenge_links.json` are **not** bundled by the
spec on purpose - they're read and written at runtime, not read-only resources. A
fresh build creates empty `presets/`/`movement_presets/` folders itself on first
launch, but if you want to ship your current presets/macros as a starter set, copy
them in manually:

```
xcopy /E /I presets "dist\Macro Slop\presets"
xcopy /E /I movement_presets "dist\Macro Slop\movement_presets"
copy challenge_links.json "dist\Macro Slop\"
```

Then zip the whole `dist/Macro Slop/` folder and send that.

## Verifying a build

1. Run `dist/Macro Slop/Macro Slop.exe` directly (not via `python`) - confirm the
   title bar reads "Macro Slop" and no console window appears.
2. Open Items -> Portals (or any gamemode) and confirm template matching still finds
   things - if `assets/` didn't land next to the exe, this is where it'd show up as
   "template not found" warnings.
3. Try "New Preset" - confirms `presets/` gets created fine on a machine that's
   never run this before.
4. The real test of the cross-device coordinate fix: copy/zip `dist/Macro Slop/` to
   the other machine and run it there.
