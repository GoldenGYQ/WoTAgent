from __future__ import annotations

import pytest

from wot_device_simulator.scenes import scene_registry


def test_scene_switch_changes_device_boundary() -> None:
    original = scene_registry.active().profile_id
    try:
        eldercare = scene_registry.activate("eldercare")
        assert "wristband-001" in eldercare.device_ids
        assert "light-101" in eldercare.device_ids
        assert "light-001" not in eldercare.device_ids
        assert "light-002" not in eldercare.device_ids
        with pytest.raises(PermissionError):
            scene_registry.assert_allowed("light-001")

        villa = scene_registry.activate("villa")
        assert "light-001" in villa.device_ids
        assert "light-002" in villa.device_ids
        assert "light-101" not in villa.device_ids
        assert "wristband-001" not in villa.device_ids
        with pytest.raises(PermissionError):
            scene_registry.assert_allowed("wristband-001")
    finally:
        scene_registry.activate(original)
