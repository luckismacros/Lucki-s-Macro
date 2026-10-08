# modules/discord_bot.py
"""
A Discord bot built into the macro, so a run can be checked on and controlled from a
phone (asked for 2026-10-08: "I'm at university while the macro works - if it breaks I
can do nothing but receive a notification").

How it connects: the macro logs in as YOUR bot (token from the Discord developer portal,
pasted in Settings) and keeps an outgoing connection to Discord - no port forwarding, no
router setup; bad WiFi only means it reconnects (discord.py does that by itself). It runs
on its own thread with its own asyncio loop, so nothing here can freeze the window or the
run.

Commands (slash commands, registered in every server the bot is in):
  /status      what's running, what it's doing, matches, W/L, rifts, uptime
  /screenshot  the screen right now
  /log         the last log lines
  /logfile     the full log files + newest debug screenshots as a zip (to read on another PC)
  /stop        stop the run (the same safe stop as the Stop button)
  /start       start the run that was last started (or the page that's open)
  /restart     stop, then start the same run again
  /lobby       stop, then go back to the lobby
  /mode        start any mode (Story, Raids, ..., Monster Clash Farm/Rift hunt) with the
               settings its page has in the app; stops the current run first
  /reset       stop, leave the game (disconnect popup's Leave, or Esc-L-Enter), rejoin
               through Roblox's home screen, and start the run again

Only the Discord account whose user ID is in Settings can use them - everyone else gets
a refusal. There is deliberately no command that runs arbitrary code or changes files.

Everything the commands actually do goes through `controller` (the main window, see
ui_qt/main_window.py BotController), which already knows how to start/stop runs safely;
this module only speaks Discord.
"""
import asyncio
import io
import threading
import time

try:
    import discord
    from discord import app_commands
except Exception as e:      # missing from a build - the rest of the app must still work
    discord = None
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None

import config

GOOD, BAD, NEUTRAL = 0x2ECC71, 0xE74C3C, 0x5865F2


class DiscordBot:
    def __init__(self, controller):
        self.controller = controller
        self.token = ""
        self.owner_id = 0
        self.state = "off"           # off / connecting / online / bad token / error: ...
        self._thread = None
        self._loop = None
        self._client = None
        self._stop = threading.Event()
        # Servers whose commands are already registered this session. on_ready fires again
        # after every reconnect; re-syncing each time (bad WiFi = many reconnects) runs into
        # Discord's rate limits for nothing.
        self._synced = set()

    # --- lifecycle (called from the window) --------------------------------------------
    def start(self, token, owner_id):
        self.stop()
        token = (token or "").strip()
        try:
            owner_id = int(str(owner_id).strip())
        except (TypeError, ValueError):
            owner_id = 0
        if discord is None:
            self.state = f"error: discord.py missing ({_IMPORT_ERROR})"
            print(f"[DiscordBot] Can't start - {self.state}")
            return
        if not token or not owner_id:
            self.state = "off"
            return
        if token != self.token:
            self._synced.clear()           # another bot application - its commands aren't registered
        self.token, self.owner_id = token, owner_id
        self._stop.clear()
        self.state = "connecting"
        self._thread = threading.Thread(target=self._run, name="discord-bot", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        loop, client = self._loop, self._client
        if loop and client and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=5)
            except Exception:
                pass
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None
        if not self.state.startswith(("bad", "error")):
            self.state = "off"

    # --- the connection thread ---------------------------------------------------------
    def _run(self):
        backoff = 5
        while not self._stop.is_set():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            client = self._make_client()
            self._client = client
            try:
                loop.run_until_complete(client.start(self.token, reconnect=True))
            except discord.LoginFailure:
                self.state = "bad token"
                print("[DiscordBot] Discord refused the token - paste it again in Settings "
                      "(Developer Portal > your app > Bot > Reset Token).")
                return
            except discord.PrivilegedIntentsRequired:
                self.state = "error: intents"
                print("[DiscordBot] Discord says the bot needs an intent it isn't allowed - this shouldn't "
                      "happen (only default intents are used).")
                return
            except (Exception, asyncio.CancelledError) as e:
                # CancelledError isn't an Exception (it's a BaseException): it used to escape
                # here, end this thread for good and get reported as a crash. It's what a
                # stop() mid-connect (pressing Connect again, closing Settings) looks like -
                # quiet then - or a connection cut off mid-way, which is just retried.
                if self._stop.is_set():
                    break
                self.state = f"error: {type(e).__name__}"
                print(f"[DiscordBot] Connection dropped ({type(e).__name__}: {str(e) or 'cancelled'}) - "
                      f"retrying in {backoff}s.")
            finally:
                try:
                    if not client.is_closed():
                        loop.run_until_complete(client.close())
                except BaseException:
                    pass
                try:
                    # Anything still pending would be destroyed with the loop ("Task was
                    # destroyed but it is pending!" in the log) - cancel and let it finish.
                    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except BaseException:
                    pass
                loop.close()
                self._loop = self._client = None
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 120)
        if not self.state.startswith(("bad", "error")):
            self.state = "off"

    def _make_client(self):
        client = discord.Client(intents=discord.Intents.default())
        tree = app_commands.CommandTree(client)
        bot = self

        async def sync_guild(guild):
            if guild.id in bot._synced:
                return
            try:
                # Per-server sync shows the commands immediately (a global sync can take up
                # to an hour to appear).
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)
                bot._synced.add(guild.id)
            except Exception as e:
                print(f"[DiscordBot] Couldn't register the commands in '{guild.name}': {e}")

        @client.event
        async def on_ready():
            bot.state = "online"
            print(f"[DiscordBot] Online as {client.user} in {len(client.guilds)} server(s).")
            if not client.guilds:
                print("[DiscordBot] The bot isn't in any server yet - invite it with the OAuth2 URL "
                      "(scopes: bot + applications.commands).")
            for guild in client.guilds:
                await sync_guild(guild)

        @client.event
        async def on_guild_join(guild):
            await sync_guild(guild)

        @client.event
        async def on_disconnect():
            if bot.state == "online":
                bot.state = "connecting"

        @client.event
        async def on_resumed():
            # A short drop is RESUMEd, which fires this - not on_ready.
            bot.state = "online"

        async def allowed(interaction):
            if interaction.user.id == bot.owner_id:
                return True
            print(f"[DiscordBot] Refused /{interaction.command.name if interaction.command else '?'} "
                  f"from {interaction.user} (not the owner).")
            await interaction.response.send_message("This macro only takes orders from its owner.", ephemeral=True)
            return False

        def embed(title, text="", color=NEUTRAL, fields=()):
            e = discord.Embed(title=title, description=text[:4000], color=color)
            for name, value in fields:
                e.add_field(name=str(name)[:256], value=str(value)[:1024] or "-", inline=True)
            e.set_footer(text=f"Lucki's Macro v{config.APP_VERSION}")
            return e

        async def run_blocking(fn, *args):
            return await asyncio.to_thread(fn, *args)

        @tree.command(name="status", description="What the macro is doing right now")
        async def status(interaction: discord.Interaction):
            if not await allowed(interaction):
                return
            info = await run_blocking(bot.controller.bot_status)
            color = GOOD if info.get("running") else NEUTRAL
            await interaction.response.send_message(embed=embed(
                "Running" if info.get("running") else "Idle", info.get("summary", ""), color, info.get("fields", ())))

        @tree.command(name="screenshot", description="A screenshot of the game right now")
        async def screenshot(interaction: discord.Interaction):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            data = await run_blocking(bot.controller.bot_screenshot)
            if not data:
                await interaction.followup.send("Couldn't take a screenshot.")
                return
            await interaction.followup.send(file=discord.File(io.BytesIO(data), filename="screen.jpg"))

        @tree.command(name="log", description="The last lines of the macro's log")
        @app_commands.describe(lines="How many lines (default 25, max 60)")
        async def log(interaction: discord.Interaction, lines: app_commands.Range[int, 5, 60] = 25):
            if not await allowed(interaction):
                return
            text = await run_blocking(bot.controller.bot_log, lines)
            while len(text) > 1900:
                text = text.split("\n", 1)[1] if "\n" in text else text[-1900:]
            await interaction.response.send_message(f"```\n{text or '(nothing logged yet)'}\n```")

        @tree.command(name="logfile", description="The full log files + recent debug screenshots, as a zip")
        async def logfile(interaction: discord.Interaction):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            data = await run_blocking(bot.controller.bot_logfile)
            if not data:
                await interaction.followup.send("Couldn't put the logs together - see /log.")
                return
            name = f"lucki_logs_{time.strftime('%Y-%m-%d_%H-%M')}.zip"
            await interaction.followup.send(f"Logs from this PC ({len(data) / 1024 / 1024:.1f} MB):",
                                            file=discord.File(io.BytesIO(data), filename=name))

        @tree.command(name="stop", description="Stop the run (finishes the current step safely)")
        async def stop(interaction: discord.Interaction):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            ok, msg = await run_blocking(bot.controller.bot_stop)
            await interaction.followup.send(embed=embed("Stopped" if ok else "Stop", msg, GOOD if ok else NEUTRAL))

        @tree.command(name="start", description="Start the run that was last started")
        async def start(interaction: discord.Interaction):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            ok, msg = await run_blocking(bot.controller.bot_start)
            await interaction.followup.send(embed=embed("Started" if ok else "Couldn't start", msg,
                                                        GOOD if ok else BAD))

        @tree.command(name="restart", description="Stop the run and start it again")
        async def restart(interaction: discord.Interaction):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            ok, msg = await run_blocking(bot.controller.bot_restart)
            await interaction.followup.send(embed=embed("Restarted" if ok else "Couldn't restart", msg,
                                                        GOOD if ok else BAD))

        @tree.command(name="lobby", description="Stop the run and go back to the lobby")
        async def lobby(interaction: discord.Interaction):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            ok, msg = await run_blocking(bot.controller.bot_lobby)
            data = await run_blocking(bot.controller.bot_screenshot)
            kwargs = {"file": discord.File(io.BytesIO(data), filename="screen.jpg")} if data else {}
            await interaction.followup.send(embed=embed("Back at the lobby" if ok else "Lobby", msg,
                                                        GOOD if ok else BAD), **kwargs)

        mode_choices = [app_commands.Choice(name=label, value=key)
                        for key, label in getattr(bot.controller, "BOT_MODES", [])][:25]
        mc_choices = [app_commands.Choice(name=label, value=key) for key, label in config.MONSTER_CLASH_MODES.items()]

        @tree.command(name="mode", description="Start a mode, with the settings it has in the app")
        @app_commands.describe(mode="Which mode to start (stops the current run first)",
                               monster_clash="Monster Clash only: Farm or Rift hunt (default: as set in the app)")
        @app_commands.choices(mode=mode_choices, monster_clash=mc_choices)
        async def mode(interaction: discord.Interaction, mode: app_commands.Choice[str],
                       monster_clash: app_commands.Choice[str] = None):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            ok, msg = await run_blocking(bot.controller.bot_mode, mode.value,
                                         monster_clash.value if monster_clash else None)
            await interaction.followup.send(embed=embed(f"Started {mode.name}" if ok else f"Couldn't start {mode.name}",
                                                        msg, GOOD if ok else BAD))

        @tree.command(name="reset", description="Leave the game and rejoin through Roblox's home screen")
        @app_commands.describe(restart_run="Start the run again afterwards if one was going (default: yes)")
        async def reset(interaction: discord.Interaction, restart_run: bool = True):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            await interaction.followup.send("Resetting: stopping the run, leaving the game and rejoining - "
                                            "this takes a minute or two.")
            ok, msg = await run_blocking(bot.controller.bot_reset, restart_run)
            data = await run_blocking(bot.controller.bot_screenshot)
            kwargs = {"file": discord.File(io.BytesIO(data), filename="screen.jpg")} if data else {}
            await interaction.followup.send(embed=embed("Reset done" if ok else "Reset failed", msg,
                                                        GOOD if ok else BAD), **kwargs)

        @tree.error
        async def on_error(interaction, error):
            print(f"[DiscordBot] /{interaction.command.name if interaction.command else '?'} failed: "
                  f"{type(error).__name__}: {error}")
            try:
                send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
                await send(f"That command failed: `{type(error).__name__}: {str(error)[:300]}`")
            except Exception:
                pass

        return client
