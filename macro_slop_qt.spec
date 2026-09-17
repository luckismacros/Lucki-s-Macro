# macro_slop_qt.spec
#
# PyInstaller build for the Qt window (app_qt.py). Same layout rules as
# macro_slop.spec - read that file's comments for why: ONEDIR, flat contents
# directory ('.') so assets/ sits next to the exe, no UPX, no custom manifest.
#
# presets/, movement_presets/, challenge_links.json, settings.json, logs/ and debug/
# are runtime data and are NOT bundled. A --clean rebuild into an existing
# "dist/Macro Slop" folder deletes them: back them up first and restore after.
#
# Build: pyinstaller macro_slop_qt.spec
# Output: dist/Lucki's Macro/Lucki's Macro.exe

a = Analysis(
    ['app_qt.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['PySide6.QtSvg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The Qt window needs QtCore/QtGui/QtWidgets/QtSvg only. Everything else PySide6
    # ships (WebEngine alone is ~150 MB) would just bloat the folder. Tk is the old
    # window's toolkit and isn't imported by app_qt.py at all.
    excludes=[
        'tkinter', 'customtkinter', 'gui', 'theme', 'ui_motion', 'modules.preset_share',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtQuickWidgets', 'PySide6.Qt3DCore',
        'PySide6.Qt3DRender', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtNetwork', 'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtBluetooth',
        'PySide6.QtPositioning', 'PySide6.QtLocation', 'PySide6.QtSensors', 'PySide6.QtSerialPort',
        'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lucki's Macro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory='.',
    icon='assets/ui/brand/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Lucki's Macro",
)
