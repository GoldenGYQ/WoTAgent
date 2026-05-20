"""CLI for quick device state inspection and modification.

Usage:
  wot-sim-state list                               # list all devices
  wot-sim-state get <device_id>                    # show one device
  wot-sim-state set <device_id> <key>=<val> ...    # set properties

Examples:
  wot-sim-state set light-001 on=true brightness=80
  wot-sim-state get light-001
"""

from __future__ import annotations

import json
import sys

from .simulator import DeviceStateStore


def _parse_val(raw: str):
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "list":
        for d in DeviceStateStore.get_all_with_info():
            loc = d["location"]
            cap = ", ".join(d["capabilities"])
            state_str = json.dumps(d["state"], ensure_ascii=False)
            print(f"  {d['device_id']:16s}  {loc:12s}  [{cap:20s}]  {state_str}")

    elif cmd == "get":
        if len(args) < 2:
            print("Usage: wot-sim-state get <device_id>")
            return
        dev_id = args[1]
        state = DeviceStateStore.get(dev_id)
        if state:
            print(json.dumps(state, ensure_ascii=False, indent=2))
        else:
            print(f"Device '{dev_id}' not found")

    elif cmd == "set":
        if len(args) < 2:
            print("Usage: wot-sim-state set <device_id> <key>=<val> ...")
            return
        dev_id = args[1]
        pairs = args[2:]
        if not pairs:
            print("No properties to set")
            return
        for pair in pairs:
            if "=" not in pair:
                print(f"  skip '{pair}' — expected key=value")
                continue
            key, _, raw_val = pair.partition("=")
            val = _parse_val(raw_val)
            DeviceStateStore.set_property(dev_id, key, val)
            print(f"  {dev_id}.{key} = {json.dumps(val, ensure_ascii=False)}")

        print(json.dumps(DeviceStateStore.get(dev_id), ensure_ascii=False, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
