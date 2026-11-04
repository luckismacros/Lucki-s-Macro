# Building and releasing Lucki's Macro

`tools/release.py` (or `release.bat`) does everything. It builds with PyInstaller from
`macro_slop_qt.spec` into `build_release/`, zips the result into `releases/`, and publishes
it on GitHub. It needs Python, Git and the GitHub CLI signed in - see section 1 of
`CLAUDE.md` to set them up on a new PC.

**Don't use `dist/` to test.** Publish a pre-release, download it again and keep that copy
in `Downloads\Lucki's Macro`, so what gets tested is exactly what people will get.

## Release flow

1. Commit the change. **New files under `assets/` must be committed to ship:** the spec only
   bundles what `git ls-files assets` lists.
2. Publish a pre-release. Installed macros only follow the latest full release, so nobody
   else is offered it:
   ```
   release.bat 3.9 --prerelease --notes "What changed, in game terms"
   ```
3. Download that zip from the GitHub release page, unzip it into `Downloads`, and try it.
4. When it works, send it to everyone:
   ```
   release.bat 3.9 --promote
   ```
   Every copy then offers the update on launch, within 3 hours, or with `/update` on Discord.

Other options: `release.bat 3.9 --no-upload` builds and zips without publishing anything,
and `release.bat 3.9` publishes straight to everyone. Always use a new version number,
because the updater compares versions.

## Check the zip

The zip must contain the new assets and no user data: `settings.json`, `logs/`, `debug/`,
`presets/` (other than `assets/default_presets`), `sho_farm.json`, `sharkfang_bar.json`,
`stage_digits_learned/`.

## Things to expect

- **The exe isn't signed,** so Windows shows "Unknown publisher" the first time, and antivirus
  programs sometimes flag it. That's common for PyInstaller programs that read the screen and
  send input.
- **Always build clean.** `tools/release.py` passes `--clean`, because PyInstaller's cache can
  silently drop bundled files.
- **The program and its images can be read by anyone.** PyInstaller bytecode is easy to turn
  back into source, and the repository is public anyway. Never put a secret in the code or in
  `assets/`.

`macro_slop.spec`, `build_nuitka.bat` and `gui.py` belong to the old Tkinter build. The
release doesn't use them.
