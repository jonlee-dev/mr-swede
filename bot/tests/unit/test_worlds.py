"""Unit tests for src.services.worlds.

Both functions under test are pure, so no discord.py / GCE mocking:
`validate_world_name` is straight string rules, and `resolve_inventory`
is a reconciliation over three plain inputs (live daemon reading,
instance metadata, on-disk cache).
"""

import pytest

from src.services.server_query import LiveStatus
from src.services.world_cache import CachedWorlds
from src.services.worlds import Inventory, resolve_inventory, validate_world_name


class TestValidateWorldName:
    @pytest.mark.parametrize(
        "name",
        ["1.0", "default", "MyWorld", "a", "world_2", "w-1", "A" * 32, "2024-run.3"],
    )
    def test_valid_names(self, name):
        assert validate_world_name(name) is None

    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            "   ",  # whitespace only
            " leading",  # leading space
            "trailing ",  # trailing space
            "has space",  # interior space
            "bad/name",  # path separator
            "..",  # parent-dir
            ".",  # current-dir
            ".hidden",  # must start alnum, not dot
            "-dash",  # must start alnum, not hyphen
            "@handle",  # illegal char
            "A" * 33,  # too long
        ],
    )
    def test_invalid_names_return_message(self, name):
        msg = validate_world_name(name)
        assert isinstance(msg, str) and msg

    def test_dot_is_allowed_after_first_char(self):
        # The live "1.0" world depends on this.
        assert validate_world_name("1.0") is None


def _live(worlds, active, *, running=True) -> LiveStatus:
    return LiveStatus(
        join_code=None,
        player_count=0,
        server_running=running,
        last_update="2026-09-09T00:00:00+00:00",
        worlds=list(worlds),
        active_world=active,
    )


class TestResolveInventory:
    def test_live_running_is_ground_truth_and_sorts(self):
        live = _live(["zeta", "alpha", "1.0"], active="alpha")
        inv = resolve_inventory(live, metadata_active=None, cached=CachedWorlds())
        assert inv == Inventory(worlds=["1.0", "alpha", "zeta"], active="alpha", live=True, updated=None)

    def test_metadata_active_wins_over_live_world_env(self):
        # world.env says "alpha" but the bot just set metadata to "beta";
        # metadata is authoritative for what will boot.
        live = _live(["alpha", "beta"], active="alpha")
        inv = resolve_inventory(live, metadata_active="beta", cached=CachedWorlds())
        assert inv.active == "beta"
        assert inv.live is True

    def test_falls_back_to_cache_when_live_is_none(self):
        cached = CachedWorlds(worlds=["b", "a"], active="a", updated="2026-09-01T00:00:00+00:00")
        inv = resolve_inventory(None, metadata_active=None, cached=cached)
        assert inv == Inventory(
            worlds=["a", "b"], active="a", live=False, updated="2026-09-01T00:00:00+00:00"
        )

    def test_falls_back_to_cache_when_server_not_running(self):
        live = _live(["a", "b"], active="a", running=False)
        cached = CachedWorlds(worlds=["cached1"], active="cached1", updated="t")
        inv = resolve_inventory(live, metadata_active=None, cached=cached)
        assert inv.live is False
        assert inv.worlds == ["cached1"]

    def test_falls_back_to_cache_when_live_worlds_empty(self):
        # Daemon answered but hasn't scanned worlds yet (early boot).
        live = _live([], active=None)
        cached = CachedWorlds(worlds=["a"], active="a", updated="t")
        inv = resolve_inventory(live, metadata_active=None, cached=cached)
        assert inv.live is False
        assert inv.worlds == ["a"]

    def test_metadata_active_wins_even_on_cache_fallback(self):
        cached = CachedWorlds(worlds=["a", "b"], active="a", updated="t")
        inv = resolve_inventory(None, metadata_active="b", cached=cached)
        assert inv.active == "b"
        assert inv.live is False

    def test_empty_everywhere_is_empty_inventory(self):
        inv = resolve_inventory(None, metadata_active=None, cached=CachedWorlds())
        assert inv.worlds == []
        assert inv.active is None
        assert inv.live is False

    def test_backup_artifacts_are_filtered_from_live(self):
        # lloesche backup dirs/files share worlds_local with real saves;
        # they must never appear as switchable worlds.
        live = _live(
            ["1.0", "1.0_backup_auto-20260909-165319", "default", "default.old"],
            active="1.0",
        )
        inv = resolve_inventory(live, metadata_active=None, cached=CachedWorlds())
        assert inv.worlds == ["1.0", "default"]

    def test_backup_artifacts_are_filtered_from_cache(self):
        cached = CachedWorlds(
            worlds=["1.0", "1.0_backup_auto-20260909-165319", "default"],
            active="1.0",
            updated="t",
        )
        inv = resolve_inventory(None, metadata_active=None, cached=cached)
        assert inv.worlds == ["1.0", "default"]
