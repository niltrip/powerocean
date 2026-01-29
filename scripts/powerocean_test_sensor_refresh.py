
import sys
import os
import argparse
import base64
import json
import time

# Mock homeassistant before importing anything from the custom component
import types
import sys
import json

class CatchAll:
    def __init__(self, name):
        self.__name__ = name
        self.__path__ = []
    def __getattr__(self, name):
        # Return a new CatchAll for any sub-module or class
        return CatchAll(f"{self.__name__}.{name}")

# Create the base homeassistant module
ha = CatchAll("homeassistant")
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.core"] = ha.core
sys.modules["homeassistant.const"] = ha.const
sys.modules["homeassistant.exceptions"] = ha.exceptions
sys.modules["homeassistant.util"] = ha.util
sys.modules["homeassistant.util.json"] = ha.util.json
sys.modules["homeassistant.helpers"] = ha.helpers
sys.modules["homeassistant.helpers.device_registry"] = ha.helpers.device_registry
sys.modules["homeassistant.helpers.entity_registry"] = ha.helpers.entity_registry
sys.modules["homeassistant.helpers.event"] = ha.helpers.event
sys.modules["homeassistant.helpers.typing"] = ha.helpers.typing
sys.modules["homeassistant.loader"] = ha.loader
sys.modules["homeassistant.components"] = ha.components
sys.modules["homeassistant.components.sensor"] = ha.components.sensor
sys.modules["homeassistant.config_entries"] = ha.config_entries

# Mock voluptuous as well
sys.modules["voluptuous"] = CatchAll("voluptuous")

# Inject necessary functions/classes
ha.exceptions.IntegrationError = Exception
ha.util.json.json_loads = json.loads
ha.const.Platform = types.SimpleNamespace(SENSOR="sensor")
ha.const.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic")

# Now add custom_components to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'custom_components'))

# Import ecoflow directly to avoid __init__.py issues
import powerocean.ecoflow as ecoflow_mod
Ecoflow = ecoflow_mod.Ecoflow

def main():
    parser = argparse.ArgumentParser(description="Test PowerOcean sensor refresh")
    parser.add_argument("--email", required=True, help="EcoFlow account email")
    parser.add_argument("--password", required=True, help="EcoFlow account password")
    parser.add_argument("--sn", required=True, help="Device serial number")
    parser.add_argument("--variant", default="83", help="Device variant (default: 83)")
    parser.add_argument("--time_wait", default=5, help="Time to wait between fetches (default: 5)")

    
    args = parser.parse_args()
    
    ecoflow = Ecoflow(
        serialnumber=args.sn,
        username=args.email,
        password=args.password,
        variant=args.variant,
        options={}
    )
    
    print("Logging in...")
    try:
        ecoflow.authorize()
    except Exception as e:
        print(f"Login failed: {e}")
        return

    print("Fetching standard data (no refresh)...")
    data1 = ecoflow.fetch_data(refresh=False)
    
    def print_pwr(data, label):
        load = None
        grid = None
        solar = None
        battery = None
        for s in data.values():
            if s.name.endswith("_sysLoadPwr"): load = s.value
            elif s.name.endswith("_sysGridPwr"): grid = s.value
            elif s.name.endswith("_mpptPv_pwrTotal"): solar = s.value
            elif s.name.endswith("_emsBpPower"): battery = s.value
        print(f"{label}: Load={load}, Grid={grid}, Solar={solar}, Battery={battery}")
        return load, grid

    l1, g1 = print_pwr(data1, "Standard")
    
    print("\nWaiting 5 seconds...")
    time.sleep(TIME_WAIT)
    
    print("Fetching data with refresh=True (heartbeat ping + derivation)...")
    data2 = ecoflow.fetch_data(refresh=True)
    l2, g2 = print_pwr(data2, "Refresh ")
    
    if l1 != l2 or g1 != g2:
        print("\nSUCCESS: Values have changed and derivation was applied!")
    else:
        print("\nNOTICE: Values are identical. If your load is currently flat, this is expected.")

if __name__ == "__main__":
    main()
