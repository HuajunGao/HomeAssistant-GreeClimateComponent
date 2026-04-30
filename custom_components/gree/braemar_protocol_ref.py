"""
Braemar Dominator Zone Controller — Protocol Reference
======================================================
Device: GR-ZCntrlr_5000_02_6902_EC
MAC:    94:24:B8:B2:69:02  →  9424b8b26902
IP:     192.168.31.179
Port:   7000
Encryption: AES-128 ECB (v1)

Key Protocol Differences vs Standard Gree Split Unit
------------------------------------------------------
1. ADDRESSING: All status/cmd packets use MAC + 2-digit zone ID suffix
     Master (zone 0):  mac = "9424b8b2690200"   (controls whole system)
     Zone 1:           mac = "9424b8b2690201"
     Zone 2:           mac = "9424b8b2690202"   ...up to zone 5

2. PARAMETERS:
     Master zone: Pow, Mod, WdSpd, EnSvSt, StFahFlg, ColdMod, ...
     Sub zones:   Pow, StTem (on/off + per-room temperature setpoint)

3. TEMPERATURE ENCODING:
     StTem = desired_celsius - 16
     e.g.  24°C  →  StTem = 8
           19°C  →  StTem = 3
     (NOT SetTem like standard split units)

4. CMD PACKET: includes "sub" field with MAC+zone_id
     master:  {"opt":["Pow","Mod","WdSpd"], "p":[1,1,0], "t":"cmd", "sub":"9424b8b2690200"}
     zone N:  {"opt":["Pow","StTem"],       "p":[1,8],   "t":"cmd", "sub":"9424b8b2690201"}

5. MASTER MODES (Mod values, same as standard Gree):
     0=Auto, 1=Cool, 2=Dry, 3=Fan, 4=Heat

6. BIND (key exchange): uses plain MAC without zone suffix
     tcid / mac in outer envelope always = plain MAC "9424b8b26902"

Verified Results (2026-04-30)
------------------------------
- Bind key: vU9gRAVGHWx0Fkpo  (fetched via standard ECB bind handshake)
- subList response: {"t":"subList","r":200,"c":5,"i":49,"list":[]}  → 5 zones
- Master status:  Pow=0/1, Mod=1, StTem=0, WdSpd=0, InProtocol=19, VavleAllOn=4
- Zone 1: Pow=1, StTem=3  (19°C, on)
- Zone 2: Pow=0, StTem=3  (19°C, off)
- Zone 3: Pow=1, StTem=4  (20°C, on)
- Zone 4: Pow=1, StTem=3  (19°C, on)
- Physical turn-on CONFIRMED working with cmd to "9424b8b2690200"
"""

import socket
import json
import base64
from Crypto.Cipher import AES

# ── Device config ────────────────────────────────────────────────────────────
DEVICE_IP   = "192.168.31.179"
DEVICE_PORT = 7000
DEVICE_MAC  = "9424b8b26902"       # plain MAC, used in bind + outer envelope tcid
DEVICE_KEY  = "vU9gRAVGHWx0Fkpo"  # session key from bind handshake
ZONE_COUNT  = 5

# Master zone and all zone params
MASTER_MAC    = DEVICE_MAC + "00"
MASTER_PARAMS = ["Pow","Mod","StTem","WdSpd","EnSvSt","StFahFlg","ColdMod",
                 "HeatSvStTemMax","CoolSvStTemMin","TemUnit","IndoorType","OMod",
                 "LowDeHumi","Quier","RmType","RmNum","VavleAllOn",
                 "CSvStTemMinFlg","HSvStTemMaxFlg","AllErr","InProtocol","Demand"]
ZONE_PARAMS   = ["Pow", "StTem"]


# ── Crypto helpers ───────────────────────────────────────────────────────────
def _pad(s: str) -> str:
    n = 16 - len(s) % 16
    return s + chr(n) * n

def _encrypt(plaintext: str) -> str:
    cipher = AES.new(DEVICE_KEY.encode(), AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(_pad(plaintext).encode())).decode()

def _decrypt(b64: str) -> dict:
    cipher = AES.new(DEVICE_KEY.encode(), AES.MODE_ECB)
    raw = cipher.decrypt(base64.b64decode(b64)).decode("utf-8", errors="ignore")
    raw = raw.replace("\x0f", "")
    raw = raw[: raw.rindex("}") + 1]
    return json.loads(raw)

def _udp_send(payload: str, timeout: int = 3) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    sock.sendto(payload.encode(), (DEVICE_IP, DEVICE_PORT))
    data, _ = sock.recvfrom(64000)
    sock.close()
    return json.loads(data)


# ── Protocol functions ───────────────────────────────────────────────────────
def get_status(zone_mac: str, params: list) -> dict:
    """Query device status. zone_mac = DEVICE_MAC + zone_id (e.g. '9424b8b2690200')."""
    plaintext = json.dumps({"cols": params, "mac": zone_mac, "t": "status"})
    envelope  = json.dumps({
        "cid": "app", "i": 0,
        "pack": _encrypt(plaintext),
        "t": "pack", "tcid": DEVICE_MAC, "uid": 0
    })
    response = _udp_send(envelope)
    inner    = _decrypt(response["pack"])
    return dict(zip(inner.get("cols", []), inner.get("dat", [])))


def send_cmd(zone_mac: str, opts: list, vals: list) -> dict:
    """Send command. zone_mac used as both tcid-zone and sub field."""
    plaintext = json.dumps({"opt": opts, "p": vals, "t": "cmd", "sub": zone_mac})
    envelope  = json.dumps({
        "cid": "app", "i": 0,
        "pack": _encrypt(plaintext),
        "t": "pack", "tcid": DEVICE_MAC, "uid": 0
    })
    response = _udp_send(envelope)
    return _decrypt(response["pack"])


# ── Integration helpers ───────────────────────────────────────────────────────
def zone_mac(zone_id: int) -> str:
    """Return MAC+zone_id string. zone_id 0 = master."""
    return DEVICE_MAC + f"{zone_id:02d}"


def master_on(mode: int = 1, fan_speed: int = 0) -> dict:
    """Turn master on. mode: 0=Auto,1=Cool,2=Dry,3=Fan,4=Heat. fan: 0=Auto."""
    return send_cmd(MASTER_MAC, ["Pow","Mod","WdSpd"], [1, mode, fan_speed])


def master_off() -> dict:
    """Turn master off."""
    return send_cmd(MASTER_MAC, ["Pow"], [0])


def zone_set(zone_id: int, on: bool, temp_celsius: float | None = None) -> dict:
    """Control individual zone (zone_id 1-5). temp_celsius optional."""
    assert 1 <= zone_id <= ZONE_COUNT
    zm = zone_mac(zone_id)
    if temp_celsius is not None:
        st_tem = int(temp_celsius) - 16   # StTem encoding
        return send_cmd(zm, ["Pow","StTem"], [1 if on else 0, st_tem])
    else:
        return send_cmd(zm, ["Pow"], [1 if on else 0])


def get_master_status() -> dict:
    return get_status(MASTER_MAC, MASTER_PARAMS)


def get_all_zones() -> dict[int, dict]:
    """Returns {zone_id: {"Pow": 0/1, "StTem": N, "temp_celsius": N+16}}"""
    result = {}
    for z in range(1, ZONE_COUNT + 1):
        try:
            s = get_status(zone_mac(z), ZONE_PARAMS)
            s["temp_celsius"] = s.get("StTem", 0) + 16
            result[z] = s
        except Exception:
            result[z] = None
    return result


# ── Demo ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Master status:", get_master_status())
    print("\nAll zones:")
    for z, s in get_all_zones().items():
        if s:
            print(f"  Zone {z}: Pow={s['Pow']}, {s['temp_celsius']}°C")
        else:
            print(f"  Zone {z}: no response")
