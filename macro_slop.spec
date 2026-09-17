# macro_slop.spec
#
# PyInstaller build spec for Macro Slop. Builds a ONEDIR distributable (a folder,
# not a single .exe) - the exe sits right next to its bundled assets/ on disk, so
# every "assets/templates/....png" path already in config.py keeps working exactly
# as-is, with none of onefile mode's temp-extraction/sys._MEIPASS path juggling.
#
# presets/, movement_presets/, and challenge_links.json are deliberately NOT listed
# in datas below - they're read AND written at runtime, and onedir's datas are meant
# for read-only bundled resources. Ship them by copying the current ones into
# dist/Macro Slop/ after building (see BUILD.md).
#
# Build: pip install pyinstaller && pyinstaller macro_slop.spec
# Output: dist/Macro Slop/Macro Slop.exe (+ its bundled assets/ folder alongside it)

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Macro Slop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX packing is disabled deliberately. Compressed executables are a strong
    # antivirus heuristic - real malware packs itself constantly - and this program
    # already looks maximally suspicious to a scanner without help: it installs global
    # keyboard/mouse hooks (pynput), injects synthetic input (pydirectinput), captures
    # the screen (mss) and moves other processes' windows (win32gui). That is a
    # keylogger's behavioural profile. Nothing here needs the few MB UPX would save.
    upx=False,
    console=False,  # GUI app - no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # No custom manifest. One was tried here to declare DPI awareness at launch, on
    # the theory that display scaling was resizing the pinned window - it wasn't (the
    # width came through untouched, and DPI scaling is uniform), and the hand-written
    # manifest replaced PyInstaller's known-good one with something Windows rejected,
    # failing every launch with "the side-by-side configuration is incorrect" before
    # any code ran. The window-size fix lives in input_controller._drive_client_to_size
    # and needs nothing from the manifest.
    # PyInstaller >=6.0 defaults onedir builds to tucking bundled data into an
    # "_internal" subfolder instead of laying it flat next to the exe. "." restores
    # the flat layout - config.py's "assets/templates/...png" paths are relative to
    # wherever the exe itself lives, not to some internal support folder.
    contents_directory='.',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # see the EXE() block above
    upx_exclude=[],
    name='Macro Slop',
)
