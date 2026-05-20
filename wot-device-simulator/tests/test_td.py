"""Tests for Thing Description loading and device discovery."""
from __future__ import annotations


class TestTDLoading:
    def test_load_tds(self):
        from wot_device_simulator.td import load_tds

        tds = load_tds()
        assert isinstance(tds, list)
        assert len(tds) >= 3

    def test_load_tds_local(self):
        from wot_device_simulator.td import load_tds_local

        tds = load_tds_local()
        assert len(tds) >= 3

    def test_td_has_device_id(self):
        from wot_device_simulator.td import load_tds

        for td in load_tds():
            assert td.device_id
            assert td.title
            assert td.location

    def test_td_has_properties(self):
        from wot_device_simulator.td import load_tds

        for td in load_tds():
            assert len(td.properties) > 0
            for p in td.properties:
                assert p.name
                assert p.type

    def test_td_has_actions(self):
        from wot_device_simulator.td import load_tds

        for td in load_tds():
            for a in td.actions:
                assert a.name
        # Most TDs should have at least one action (pure sensors may have none)
        with_actions = [td for td in load_tds() if td.actions]
        assert len(with_actions) >= 3

    def test_light_has_luminance(self):
        from wot_device_simulator.td import find_devices

        lights = find_devices(capability="luminance")
        assert len(lights) >= 3
        for l in lights:
            caps = [c.lower() for c in l.capabilities]
            assert "luminance" in caps

    def test_ac_has_temperature(self):
        from wot_device_simulator.td import find_devices

        acs = find_devices(capability="temperature")
        assert len(acs) >= 1

    def test_find_by_location(self):
        from wot_device_simulator.td import find_devices

        living_room = find_devices(location="living_room")
        assert len(living_room) > 0

    def test_get_device_by_id(self):
        from wot_device_simulator.td import get_device_by_id

        light = get_device_by_id("light-001")
        assert light is not None
        assert light.title

    def test_get_device_by_id_nonexistent(self):
        from wot_device_simulator.td import get_device_by_id

        assert get_device_by_id("nonexistent-device") is None

    def test_device_id_extraction(self):
        from wot_device_simulator.td import ThingDescription, get_device_by_id

        td = get_device_by_id("light-001")
        assert td is not None
        assert td.device_id == "light-001"

    def test_location_synonyms(self):
        from wot_device_simulator.td import find_devices

        # "parlor" should map to living_room
        parlor = find_devices(location="parlor")
        living_room = find_devices(location="living_room")
        assert len(parlor) == len(living_room)
        for d in parlor:
            assert d.location == "living_room"
