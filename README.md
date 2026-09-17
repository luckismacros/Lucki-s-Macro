==============================================
 LUCKI'S MACRO - README
==============================================

WHAT THIS IS
------------
Lucki's Macro is a desktop tool for a Roblox tower-defense game. It looks at
your own screen, recognises buttons/screens it already has pictures of, and
sends mouse clicks and key presses to Roblox the same way you would - so it
can place units, start matches, farm raids/portals/expeditions, and generally
keep a run going while you're not sitting at the keyboard.

It does NOT:
 - touch your Roblox account, password, or cookies
 - read, upload or modify any files other than its own settings/recordings
 - talk to the internet at all, unless YOU turn on the optional Discord
   notification feature and paste in a webhook URL you made yourself. If you
   never set one up, nothing is ever sent anywhere.
 - run in the background, hide itself, install anything, or survive after
   you close it. It's one folder; delete the folder and it's gone.

It's a personal project, made by one person as a hobby, not a company or a
paid product. It's free, and it's still in beta - see "BETA / BUGS" below.


WHY THIS ISN'T A TROJAN (AND WHY YOUR ANTIVIRUS MIGHT STILL COMPLAIN)
----------------------------------------------------------------------
This program's code was written with the help of an AI coding assistant
(Claude Code, by Anthropic). That assistant operates under safety rules that
refuse to help build malware, spyware, credential stealers, hidden backdoors,
or anything designed to run without the user knowing - so those things were
never going to end up in here through that pathway, and nobody asked for one.

That said, "an AI helped write it" isn't something you should just take on
faith - and you don't have to:

 - What it does is exactly what's described above: look at the screen, click
   things, press keys. That's the whole program. There's no hidden second
   job.
 - It's built with PyInstaller, a standard, widely-used tool that packages a
   normal, ordinary Python program into a .exe. It is NOT signed with a
   developer certificate (that costs money most solo hobby projects don't
   spend), so Windows will show an "Unknown publisher" warning the first
   time you run it. That warning is about the missing signature, not a virus
   detection - see the tutorial below for what to do about it.
 - Antivirus/Windows Defender heuristics sometimes flag tools like this one
   as suspicious - not because of what THIS program does, but because
   "reads the screen and sends synthetic mouse/keyboard input" is also a
   pattern real malware uses, and cheap heuristic scanners can't always tell
   the difference between a game macro and something malicious just from
   that shape. This is an extremely common false positive for this entire
   category of tool (game bots, accessibility input tools, automation
   scripts) - it is not unique to this program and not evidence of anything
   being wrong with it.
 - If you or a friend wants to be extra careful: run it on a spare/alt
   account first, or inside a sandboxed/virtual machine, and watch what it
   actually does. It'll do exactly what's described above and nothing else.

Use it at your own discretion. Automating a game is generally against most
games' terms of service in some form - that's true of any macro/bot for any
game, this one included, and it's on you to decide if that's a risk you're
willing to take with your account.


BETA / BUGS
-----------
This is still beta software. It's been tested a fair amount, but it's one
person's project, the game it automates can change at any time and break
something, and edge cases get missed. If something doesn't work right:

 - it's a bug, not sabotage - please report it rather than assuming the
   worst
 - it will get fixed/updated when bugs are found and there's time to do so
 - future versions may change how things look or work as a result

There's no guarantee of support turnaround - this isn't a company with a
help desk, it's a hobby project shared with friends.


HOW TO START IT (yes, it's this simple)
-----------------------------------------
1. Unzip the download somewhere you can find again (Desktop, Documents,
   wherever) - the whole folder, not just the .exe on its own.
2. Open that folder.
3. Double-click "Lucki's Macro.exe".
4. Windows will very likely show a blue "Windows protected your PC" screen
   the FIRST time only (this is the "unsigned .exe" thing explained above,
   not a virus warning). Click "More info", then "Run anyway".
5. The app opens. If it's your first time, a short in-app tutorial walks you
   through picking a gamemode and how unit placement works - just follow it.
6. Get Roblox open and follow the on-screen setup. That's it.

To close it, just close the window like any other program - nothing keeps
running afterwards.
