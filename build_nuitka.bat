@echo off
REM ============================================================================
REM  Macro Slop - Nuitka build (obfuscated / compiled release)
REM ============================================================================
REM  Unlike PyInstaller, which bundles readable .pyc bytecode that pyinstxtractor
REM  plus a decompiler turns back into near-original source, Nuitka compiles the
REM  Python to C and then to native machine code. There is no bytecode left in the
REM  output to extract, so recovering the logic means actually reverse-engineering
REM  a binary rather than running a one-line extraction script.
REM
REM  This is the build to use for anything published publicly. macro_slop.spec
REM  (PyInstaller) is still fine for your own machines - it builds far faster.
REM
REM  First run downloads a C compiler (MinGW64) automatically and takes a while;
REM  later builds reuse the cache and are much quicker.
REM
REM  Output: dist_nuitka\gui.dist\  ->  renamed to  dist_nuitka\Macro Slop\
REM ============================================================================

setlocal
cd /d "%~dp0"

set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo.
echo === Building Macro Slop with Nuitka (this takes several minutes) ===
echo.

"%PY%" -m nuitka gui.py ^
  --standalone ^
  --output-dir=dist_nuitka ^
  --output-filename="Macro Slop.exe" ^
  --windows-console-mode=disable ^
  --enable-plugin=tk-inter ^
  --include-data-dir=assets=assets ^
  --include-package-data=customtkinter ^
  --include-package=customtkinter ^
  --include-package=cv2 ^
  --include-package=mss ^
  --include-package=pynput ^
  --include-package=pydirectinput ^
  --company-name="Macro Slop" ^
  --product-name="Macro Slop" ^
  --file-description="Macro Slop" ^
  --file-version=1.0.0 ^
  --product-version=1.0.0 ^
  --assume-yes-for-downloads ^
  --remove-output

if errorlevel 1 (
  echo.
  echo *** BUILD FAILED ***
  exit /b 1
)

REM Nuitka names the folder after the entry script; give it the product name.
if exist "dist_nuitka\Macro Slop" rmdir /s /q "dist_nuitka\Macro Slop"
ren "dist_nuitka\gui.dist" "Macro Slop"

REM presets/, movement_presets/ and challenge_links.json are read AND written at
REM runtime, so they are copied in rather than compiled in - same reasoning as the
REM PyInstaller build (see BUILD.md).
if exist presets            xcopy /E /I /Y presets            "dist_nuitka\Macro Slop\presets"            >nul
if exist movement_presets   xcopy /E /I /Y movement_presets   "dist_nuitka\Macro Slop\movement_presets"   >nul
if exist challenge_links.json copy /Y challenge_links.json    "dist_nuitka\Macro Slop\"                   >nul

echo.
echo === Done: dist_nuitka\Macro Slop\Macro Slop.exe ===
echo.
endlocal
