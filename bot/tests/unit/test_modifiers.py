"""Unit tests for src.services.modifiers (pure parse/format/validate/edit)."""

import pytest

from src.services import modifiers as m
from src.services.modifiers import ServerModifiers


class TestParseArgs:
    def test_full_parse(self):
        s = "-preset hard -modifier portals casual -modifier raids none -setkey nobuildcost"
        got = m.parse_args(s)
        assert got.preset == "hard"
        assert got.modifiers == {"portals": "casual", "raids": "none"}
        assert got.keys == ("nobuildcost",)

    def test_empty_and_none(self):
        assert m.parse_args("") == ServerModifiers()
        assert m.parse_args(None) == ServerModifiers()

    def test_unknown_modifier_key_ignored(self):
        got = m.parse_args("-modifier bogus value -modifier raids more")
        assert got.modifiers == {"raids": "more"}

    def test_stray_tokens_ignored(self):
        got = m.parse_args("-crossplay 1 -modifier raids none junk")
        assert got.modifiers == {"raids": "none"}

    def test_case_insensitive(self):
        got = m.parse_args("-modifier Raids None -setkey NoBuildCost")
        assert got.modifiers == {"raids": "none"}
        assert got.keys == ("nobuildcost",)


class TestToArgsRoundTrip:
    def test_roundtrip_normalizes_order(self):
        s = "-setkey nobuildcost -modifier raids none -modifier portals casual"
        once = m.parse_args(s)
        # to_args emits preset, then modifiers in MODIFIERS order, then keys
        assert once.to_args() == "-modifier portals casual -modifier raids none -setkey nobuildcost"
        # re-parsing the formatted string yields the same structure
        assert m.parse_args(once.to_args()) == once

    def test_empty_to_args(self):
        assert ServerModifiers().to_args() == ""

    def test_preset_normal_is_omitted(self):
        assert m.set_preset(ServerModifiers(), "normal").to_args() == ""


class TestValidate:
    def test_modifier_ok(self):
        assert m.validate_modifier("raids", "none") is None
        assert m.validate_modifier("deathpenalty", "casual") is None

    def test_modifier_default_sentinel_ok(self):
        assert m.validate_modifier("raids", "default") is None

    def test_modifier_bad_key(self):
        assert m.validate_modifier("nope", "none") is not None

    def test_modifier_bad_value(self):
        msg = m.validate_modifier("raids", "sometimes")
        assert msg and "Invalid value" in msg

    def test_preset(self):
        assert m.validate_preset("hard") is None
        assert m.validate_preset("brutal") is not None

    def test_key(self):
        assert m.validate_key("nobuildcost") is None
        assert m.validate_key("godmode") is not None


class TestEdits:
    def test_set_modifier_add_update_clear(self):
        base = ServerModifiers()
        one = m.set_modifier(base, "raids", "none")
        assert one.modifiers == {"raids": "none"}
        two = m.set_modifier(one, "raids", "more")
        assert two.modifiers == {"raids": "more"}
        cleared = m.set_modifier(two, "raids", "default")
        assert cleared.modifiers == {}

    def test_set_preset_and_clear(self):
        assert m.set_preset(ServerModifiers(), "hard").preset == "hard"
        assert m.set_preset(ServerModifiers(preset="hard"), "normal").preset is None
        assert m.set_preset(ServerModifiers(preset="hard"), None).preset is None

    def test_set_key_toggle_idempotent(self):
        on = m.set_key(ServerModifiers(), "nobuildcost", True)
        assert on.keys == ("nobuildcost",)
        # enabling again doesn't duplicate
        assert m.set_key(on, "nobuildcost", True).keys == ("nobuildcost",)
        off = m.set_key(on, "nobuildcost", False)
        assert off.keys == ()

    def test_edits_do_not_mutate_input(self):
        base = ServerModifiers(modifiers={"portals": "casual"})
        m.set_modifier(base, "raids", "none")
        assert base.modifiers == {"portals": "casual"}  # unchanged
