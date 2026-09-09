"""Valheim server control commands.

Slash commands:
    /valheim status        -- describe current VM + game state, including the
                              PlayFab join code and server password
    /valheim start         -- start the VM (idempotent)
    /valheim stop          -- stop the VM (idempotent)
    /valheim world list    -- list the world saves on the server, by name
    /valheim world switch  -- switch the active world by name (restarts the VM)
    /valheim world new      -- create a new world by name (random seed)

Each handler defers, calls into src.services.compute / server_query,
then sends a response so the channel sees the result. Embed rendering
lives in src.cogs.embeds.

World switching stays inside the bot's compute-API-only model (no SSH):
the active world is an instance `world-name` metadata key that the VM's
startup-script reads into world.env on boot. `switch`/`new` set that key
(compute.set_world) then stop/start the VM -- a graceful cycle so the
world being left is saved. Because the daemon that reports the world
list only answers while the VM is up, the list is cached (world_cache)
so `list` works while the server is off.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from src.cogs.embeds import (
    valheim_modifiers_embed,
    valheim_status_embed,
    valheim_worlds_embed,
)
from src.config.logging import get_logger
from src.config.secrets import get_secrets
from src.config.settings import get_settings
from src.services import compute, modifiers, server_query, world_cache
from src.services.server_query import LiveStatus
from src.services.worlds import Inventory, resolve_inventory, validate_world_name
from src.utils.checks import requires_guild

logger = get_logger(__name__)

# How long to wait for the VM to reach TERMINATED before issuing start
# during a world switch. A graceful stop saves the world being left; the
# lloesche container's stop_grace_period is 2m but Valheim usually saves
# and exits in well under a minute.
_STOP_POLL_TIMEOUT_SECONDS = 150
_STOP_POLL_INTERVAL_SECONDS = 4


class ValheimCog(commands.GroupCog, name="valheim"):
    """The /valheim command group."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._settings = get_settings()
        super().__init__()

    def _target(self) -> tuple[str, str, str]:
        s = self._settings
        return s.gcp_project_id, s.valheim_zone, s.valheim_instance_name

    @app_commands.command(name="status", description="Show Valheim server status")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim status requested", user=str(interaction.user))
        project, zone, instance = self._target()
        state = await compute.describe_instance(project, zone, instance)

        live: LiveStatus | None = None
        if state.status == "RUNNING" and state.public_ip:
            live = await server_query.fetch_status(
                state.public_ip,
                port=self._settings.valheim_status_http_port,
            )
            # Opportunistically refresh the world cache off the same
            # reading, so /valheim world list stays fresh without its
            # own round-trip.
            if live is not None and live.server_running and live.worlds:
                world_cache.remember(live.worlds, live.active_world or state.active_world)

        # Password is in GSM; fetch every status call so a rotation is
        # picked up without restarting the bot. Cheap (cached on the
        # SecretManager after first call).
        password: str | None = None
        if state.status == "RUNNING":
            password = get_secrets(self._settings.discord_bot_name).valheim_password

        await interaction.followup.send(embed=valheim_status_embed(state, live, password))

    @app_commands.command(name="start", description="Start the Valheim server")
    async def start(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim start requested", user=str(interaction.user))
        project, zone, instance = self._target()
        state = await compute.describe_instance(project, zone, instance)
        if state.status == "RUNNING":
            ip = state.public_ip or "address pending"
            await interaction.followup.send(content=f"Server already running at `{ip}:2456`.")
            return
        await compute.start_instance(project, zone, instance)
        await interaction.followup.send(
            content="Starting the Valheim server. Run `/valheim status` in ~90s."
        )

    @app_commands.command(name="stop", description="Stop the Valheim server")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        logger.info("Valheim stop requested", user=str(interaction.user))
        project, zone, instance = self._target()
        state = await compute.describe_instance(project, zone, instance)
        if state.status == "TERMINATED":
            await interaction.followup.send(content="Server is already stopped.")
            return
        await compute.stop_instance(project, zone, instance)
        await interaction.followup.send(content="Stopping the Valheim server.")

    # -----------------------------------------------------------------
    # /valheim world <list|switch|new>
    # -----------------------------------------------------------------

    world = app_commands.Group(name="world", description="List, switch, and create Valheim worlds")

    async def _fetch_inventory(
        self, state: compute.InstanceState | None = None
    ) -> Inventory:
        """Resolve the world inventory from the live daemon + metadata + cache.

        Refreshes the on-disk cache whenever the daemon answers, so a later
        call while the VM is off still has names to show.
        """
        project, zone, instance = self._target()
        if state is None:
            state = await compute.describe_instance(project, zone, instance)

        live: LiveStatus | None = None
        if state.status == "RUNNING" and state.public_ip:
            live = await server_query.fetch_status(
                state.public_ip, port=self._settings.valheim_status_http_port
            )
        if live is not None and live.server_running and live.worlds:
            world_cache.remember(live.worlds, live.active_world or state.active_world)

        return resolve_inventory(live, state.active_world, world_cache.recall())

    async def _world_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Suggest existing world names from the cache (fast, no network)."""
        cur = current.lower()
        cached = world_cache.recall()
        return [
            app_commands.Choice(name=w, value=w)
            for w in cached.worlds
            if cur in w.lower()
        ][:25]

    @world.command(name="list", description="List the world saves on the server, by name")
    @requires_guild
    async def world_list(self, interaction: discord.Interaction) -> None:
        logger.info("Valheim world list requested", user=str(interaction.user))
        inv = await self._fetch_inventory()
        await interaction.followup.send(
            embed=valheim_worlds_embed(inv.worlds, inv.active, live=inv.live, updated=inv.updated)
        )

    @world.command(
        name="switch",
        description="Switch the active world by name (restarts the server)",
    )
    @app_commands.describe(
        name="World to switch to",
        force="Switch even if players are currently connected (they'll be disconnected)",
    )
    @app_commands.autocomplete(name=_world_name_autocomplete)
    @requires_guild
    async def world_switch(
        self, interaction: discord.Interaction, name: str, force: bool = False
    ) -> None:
        logger.info("Valheim world switch requested", user=str(interaction.user), world=name)
        await self._perform_switch(interaction, name, creating=False, force=force)

    @world.command(name="new", description="Create a new world by name (random seed)")
    @app_commands.describe(
        name="Name for the new world",
        force="Create even if players are currently connected (they'll be disconnected)",
    )
    @requires_guild
    async def world_new(
        self, interaction: discord.Interaction, name: str, force: bool = False
    ) -> None:
        logger.info("Valheim world new requested", user=str(interaction.user), world=name)
        await self._perform_switch(interaction, name, creating=True, force=force)

    async def _wait_for_status(self, target: str) -> bool:
        """Poll until the VM reaches `target` status. True on success."""
        project, zone, instance = self._target()
        attempts = _STOP_POLL_TIMEOUT_SECONDS // _STOP_POLL_INTERVAL_SECONDS
        for _ in range(attempts):
            state = await compute.describe_instance(project, zone, instance)
            if state.status == target:
                return True
            await asyncio.sleep(_STOP_POLL_INTERVAL_SECONDS)
        return False

    async def _perform_switch(
        self,
        interaction: discord.Interaction,
        name: str,
        *,
        creating: bool,
        force: bool,
    ) -> None:
        """Shared body for switch (existing world) and new (fresh world).

        Validates the name, guards against clobbering / no-op / connected
        players, sets the `world-name` metadata, then stop/starts the VM so
        the startup-script re-reads it. Assumes the interaction is already
        deferred (via @requires_guild).
        """
        project, zone, instance = self._target()

        err = validate_world_name(name)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        state = await compute.describe_instance(project, zone, instance)
        inv = await self._fetch_inventory(state=state)
        exists = name in inv.worlds

        if creating and exists:
            await interaction.followup.send(
                f"A world named `{name}` already exists — use `/valheim world switch` to load it."
            )
            return
        if not creating and not exists:
            known = ", ".join(f"`{w}`" for w in inv.worlds) or "(none cached — start the server once)"
            await interaction.followup.send(
                f"No world named `{name}`. Known worlds: {known}."
                + ("" if inv.live else " (list may be stale — server is off.)")
            )
            return
        if not creating and inv.active == name and state.status != "TERMINATED":
            await interaction.followup.send(f"`{name}` is already the active world.")
            return

        # Guard: switching restarts the VM and disconnects anyone online.
        if state.status == "RUNNING" and state.public_ip:
            live = await server_query.fetch_status(
                state.public_ip, port=self._settings.valheim_status_http_port
            )
            if live is not None and live.player_count > 0 and not force:
                await interaction.followup.send(
                    f"⚠️ {live.player_count} player(s) are connected — switching restarts the "
                    f"server and disconnects them. Re-run with `force: True` to proceed."
                )
                return

        try:
            await compute.set_world(project, zone, instance, name)

            verb = "Creating new world" if creating else "Switching to world"
            await interaction.followup.send(
                f"{verb} `{name}` — saving and restarting the server…"
            )

            if state.status != "TERMINATED":
                await compute.stop_instance(project, zone, instance)
                if not await self._wait_for_status("TERMINATED"):
                    await interaction.followup.send(
                        "The server is taking a while to stop; I'll still start it. "
                        "If it doesn't come up shortly, check `/valheim status`."
                    )
            await compute.start_instance(project, zone, instance)
        except Exception:
            logger.exception("world switch failed", world=name, creating=creating)
            await interaction.followup.send(
                "Something went wrong changing the world. The active world may not have "
                "switched — check `/valheim status` and try again."
            )
            return

        # Optimistically reflect the new active world (and, for `new`, the
        # new name) in the cache so an immediate `list` looks right.
        projected = sorted(set(inv.worlds) | {name}) if creating else inv.worlds
        world_cache.remember(projected, name)

        tail = " New worlds generate their map on first boot." if creating else ""
        await interaction.followup.send(
            f"✅ `{name}` is booting. Give it ~2–3 minutes, then `/valheim status` to join.{tail}"
        )

    # -----------------------------------------------------------------
    # /valheim modifier <list|set|preset|key>
    #
    # World modifiers are launch args the server applies at boot. The bot
    # stores them in the `world-modifiers` instance metadata (parsed by
    # src.services.modifiers); the startup-script folds that into
    # SERVER_ARGS. Changing one is a metadata write + graceful reboot,
    # same mechanism as world-switch -- see src.services.modifiers.
    # -----------------------------------------------------------------

    modifier = app_commands.Group(name="modifier", description="View and change world modifiers")

    async def _players_block(
        self, interaction: discord.Interaction, state: compute.InstanceState, force: bool
    ) -> bool:
        """True (and messages the user) if a restart would kick connected
        players and `force` wasn't set."""
        if force or state.status != "RUNNING" or not state.public_ip:
            return False
        live = await server_query.fetch_status(
            state.public_ip, port=self._settings.valheim_status_http_port
        )
        if live is not None and live.player_count > 0:
            await interaction.followup.send(
                f"⚠️ {live.player_count} player(s) are connected — applying this restarts the "
                f"server and disconnects them. Re-run with `force: True` to proceed."
            )
            return True
        return False

    async def _write_modifiers_and_reboot(
        self,
        interaction: discord.Interaction,
        state: compute.InstanceState,
        new_mods: modifiers.ServerModifiers,
        summary: str,
    ) -> None:
        """Write the modifier args to metadata and stop/start so the
        startup-script re-reads them. Assumes the interaction is deferred
        and the players-guard already ran."""
        project, zone, instance = self._target()
        args = new_mods.to_args()
        try:
            await compute.set_metadata(
                project, zone, instance, compute.WORLD_MODIFIERS_METADATA_KEY, args
            )
            await interaction.followup.send(f"{summary} — saving and restarting the server…")
            if state.status != "TERMINATED":
                await compute.stop_instance(project, zone, instance)
                if not await self._wait_for_status("TERMINATED"):
                    await interaction.followup.send(
                        "The server is taking a while to stop; I'll still start it. "
                        "If it doesn't come up shortly, check `/valheim status`."
                    )
            await compute.start_instance(project, zone, instance)
        except Exception:
            logger.exception("modifier apply failed", args=args)
            await interaction.followup.send(
                "Something went wrong applying that change — the modifiers may not have "
                "updated. Check `/valheim status` and try again."
            )
            return
        await interaction.followup.send(
            "✅ Applied. Server is restarting (~2–3 min); the new modifiers take effect on boot."
        )

    async def _mod_key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        c = current.lower()
        return [app_commands.Choice(name=k, value=k) for k in modifiers.MODIFIERS if c in k][:25]

    async def _mod_value_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        c = current.lower()
        key = (getattr(interaction.namespace, "key", "") or "").lower()
        values = ["default", *modifiers.MODIFIERS.get(key, [])]
        return [app_commands.Choice(name=v, value=v) for v in values if c in v][:25]

    async def _preset_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        c = current.lower()
        return [app_commands.Choice(name=p, value=p) for p in modifiers.PRESETS if c in p][:25]

    async def _key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        c = current.lower()
        return [
            app_commands.Choice(name=f"{label} ({gk})", value=gk)
            for gk, label in modifiers.GLOBAL_KEYS.items()
            if c in gk or c in label.lower()
        ][:25]

    @modifier.command(name="list", description="Show the current world modifiers")
    @requires_guild
    async def modifier_list(self, interaction: discord.Interaction) -> None:
        logger.info("Valheim modifier list requested", user=str(interaction.user))
        state = await compute.describe_instance(*self._target())
        mods = modifiers.parse_args(state.server_modifiers_raw)
        await interaction.followup.send(embed=valheim_modifiers_embed(mods))

    @modifier.command(name="set", description="Set a difficulty modifier (restarts the server)")
    @app_commands.describe(
        key="Which modifier (combat, deathpenalty, resources, raids, portals)",
        value="New value, or 'default' to clear it",
        force="Apply even if players are connected",
    )
    @app_commands.autocomplete(key=_mod_key_autocomplete, value=_mod_value_autocomplete)
    @requires_guild
    async def modifier_set(
        self, interaction: discord.Interaction, key: str, value: str, force: bool = False
    ) -> None:
        key, value = key.lower().strip(), value.lower().strip()
        logger.info("Valheim modifier set", user=str(interaction.user), key=key, value=value)
        err = modifiers.validate_modifier(key, value)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        state = await compute.describe_instance(*self._target())
        if await self._players_block(interaction, state, force):
            return
        new = modifiers.set_modifier(modifiers.parse_args(state.server_modifiers_raw), key, value)
        shown = "default (cleared)" if value == modifiers.CLEAR_VALUE else value
        await self._write_modifiers_and_reboot(interaction, state, new, f"Set **{key}** → `{shown}`")

    @modifier.command(name="preset", description="Apply a difficulty preset (restarts the server)")
    @app_commands.describe(name="Preset name", force="Apply even if players are connected")
    @app_commands.autocomplete(name=_preset_autocomplete)
    @requires_guild
    async def modifier_preset(
        self, interaction: discord.Interaction, name: str, force: bool = False
    ) -> None:
        name = name.lower().strip()
        logger.info("Valheim modifier preset", user=str(interaction.user), preset=name)
        err = modifiers.validate_preset(name)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        state = await compute.describe_instance(*self._target())
        if await self._players_block(interaction, state, force):
            return
        new = modifiers.set_preset(modifiers.parse_args(state.server_modifiers_raw), name)
        await self._write_modifiers_and_reboot(interaction, state, new, f"Applied preset **{name}**")

    @modifier.command(name="key", description="Turn a global-key toggle on/off (restarts the server)")
    @app_commands.describe(
        name="Which toggle", enabled="On or off", force="Apply even if players are connected"
    )
    @app_commands.autocomplete(name=_key_autocomplete)
    @requires_guild
    async def modifier_key(
        self, interaction: discord.Interaction, name: str, enabled: bool, force: bool = False
    ) -> None:
        name = name.lower().strip()
        logger.info("Valheim modifier key", user=str(interaction.user), key=name, enabled=enabled)
        err = modifiers.validate_key(name)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        state = await compute.describe_instance(*self._target())
        if await self._players_block(interaction, state, force):
            return
        new = modifiers.set_key(modifiers.parse_args(state.server_modifiers_raw), name, enabled)
        verb = "Enabled" if enabled else "Disabled"
        await self._write_modifiers_and_reboot(
            interaction, state, new, f"{verb} **{modifiers.GLOBAL_KEYS[name]}**"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ValheimCog(bot))
