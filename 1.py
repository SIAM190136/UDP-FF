# ==================== STANDARD IMPORTS ====================
import sys
import asyncio
import httpx
import random
import json
import socket
import struct
import time
import os
import uuid
import itertools
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ==================== ORIGINAL IMPORTS ====================
from google_play_scraper import app as play_scraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from protobuf_decoder.protobuf_decoder import Parser
from message_ids import MESSAGE_ID_TO_NAME
import thunderFF_pb2
import StartMatch_pb2

# ==================== WEB DASHBOARD ====================
from dashboard_server import bot_state, start_web_dashboard

# ==================== CONFIGURATION ====================
WEB_HOST = "0.0.0.0"
WEB_PORT = 20331
ACCOUNTS_FILE = "accounts.json"
TOKEN_CACHE_FILE = "token_cache.json"
DEVICES_FILE = "devices.json"  # 🔥 NEW: Persistent device storage
TOKEN_CACHE_TTL = 1200

# 🔥 70+ MATCHES/HOUR + FULL MATCH
START_MATCH_INTERVAL = 2.0
NEW_MATCH_DELAY = 2.0
MAX_MATCH_DURATION = 350        # Full LW match natural end (6 min)
MATCH_IDLE_TIMEOUT = 10.0
MAX_CONCURRENT_MATCHES = 15      # 7 parallel = 72 matches/hour
PRIORITY_REGIONS = ["BD", "IND", "SG", "TH", "PH", "VN", "MY", "ID", "HK", "TW", "BR", "EU", "RU", "TR", "ME", "NA", "SAC", "US", "SSA"]

ANTI_AFK_ENABLED = True
ANTI_AFK_MIN_INTERVAL = 2.5
ANTI_AFK_MAX_INTERVAL = 4.5
ANTI_AFK_FIRE_CHANCE = 0.5
ANTI_AFK_MOVE_ONLY_CHANCE = 0.5
FALLBACK_UID = ""
FALLBACK_PASSWORD = ""


# ==================== ULTRA SAFE PERSISTENT DEVICE RANDOMIZER (1:1 ACCOUNT SYNC) ====================
def load_accounts() -> List[dict]:
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def _generate_new_device() -> dict:
    device_list = [
        ("Samsung", "SM-G998B", "Adreno (TM) 660", "Android OS 12 / API-31"),
        ("Xiaomi", "2201122G", "Adreno (TM) 730", "Android OS 13 / API-33"),
        ("Realme", "RMX3700", "Mali-G710", "Android OS 14 / API-34"),
        ("OnePlus", "CPH2451", "Adreno (TM) 740", "Android OS 13 / API-33"),
        ("OPPO", "CPH2611", "Adreno (TM) 720", "Android OS 14 / API-34"),
        ("Vivo", "V2203", "Mali-G710", "Android OS 12 / API-31"),
        ("Poco", "M2102J20SG", "Adreno (TM) 660", "Android OS 13 / API-33"),
    ]
    brand, model, gpu, os_ver = random.choice(device_list)
    return {
        "unique_device_id": f"Google|{str(uuid.uuid4())}",
        "brand": brand,
        "model": model,
        "gpu_renderer": gpu,
        "system_software": os_ver,
        "screen_width": random.choice([1080, 1440, 720, 1280]),
        "screen_height": random.choice([2400, 3200, 1600, 2400]),
        "screen_dpi": str(random.randint(300, 420)),
        "memory": random.randint(2800, 6500),
        "processor_details": f"ARM64 FP ASIMD AES VMH | {random.randint(2200, 3200)} | {random.randint(6, 12)}",
        "client_ip": f"{random.randint(103, 223)}.{random.randint(10, 250)}.{random.randint(10, 250)}.{random.randint(10, 250)}"
    }

def sync_devices_with_accounts() -> dict:
    """
    Enforces strict 1-to-1 parity between accounts.json and devices.json:
    - len(devices) == len(accounts) ALWAYS (not one less, not one more).
    - If accounts.json has 45 IDs, exactly 45 devices are kept/created.
    - If an account is deleted from web or file, its device is immediately purged.
    """
    accounts = load_accounts()
    devices = {}
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, "r", encoding="utf-8") as f:
                devices = json.load(f)
                if not isinstance(devices, dict):
                    devices = {}
        except Exception:
            devices = {}

    cached_data = _load_token_cache()
    synced_devices = {}

    for acc in accounts:
        acc_key = None
        aliases = []
        if "uid" in acc and acc["uid"]:
            acc_key = str(acc["uid"]).strip()
            aliases.append(acc_key)
            if acc_key in bot_state.auth_to_game_id:
                aliases.append(str(bot_state.auth_to_game_id[acc_key]))
        elif "token" in acc and acc["token"]:
            tok = str(acc["token"]).strip()
            tok_pfx = tok[:16]
            aliases.append(tok_pfx)
            aliases.append(tok)
            cached_entry = cached_data.get(f"tok_{tok[:20]}") or cached_data.get(tok)
            if cached_entry:
                if cached_entry.get("open_id"):
                    aliases.insert(0, str(cached_entry["open_id"]))
                if cached_entry.get("account_id"):
                    aliases.append(str(cached_entry["account_id"]))
            if tok_pfx in bot_state.account_token_map:
                aliases.insert(0, str(bot_state.account_token_map[tok_pfx]))
            if tok in bot_state.account_token_map:
                aliases.insert(0, str(bot_state.account_token_map[tok]))

            acc_key = aliases[0] if aliases else tok_pfx

        if not acc_key:
            continue

        dev_profile = None
        for a in aliases:
            if a in devices:
                dev_profile = devices[a]
                break
        if not dev_profile and acc_key in devices:
            dev_profile = devices[acc_key]

        if not dev_profile:
            dev_profile = _generate_new_device()

        synced_devices[acc_key] = dev_profile

    try:
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(synced_devices, f, indent=4)
    except Exception as e:
        print_error(f"Failed to save synced devices: {e}")

    return synced_devices

def get_device_for_account(account_identifier: str) -> dict:
    """
    Retrieves or assigns the persistent device profile for the given account identifier.
    Guarantees that 1 account = 1 device.
    """
    devices = {}
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, "r", encoding="utf-8") as f:
                devices = json.load(f)
                if not isinstance(devices, dict):
                    devices = {}
        except Exception:
            devices = {}

    acc_key = str(account_identifier).strip()
    if acc_key in devices:
        return devices[acc_key]

    # Check known aliases
    for alias in [bot_state.auth_to_game_id.get(acc_key),
                  bot_state.game_to_auth_id.get(acc_key),
                  bot_state.account_token_map.get(acc_key),
                  bot_state.account_token_map.get(acc_key[:16]) if len(acc_key) >= 16 else None]:
        if alias and str(alias) in devices:
            dev = devices.pop(str(alias))
            devices[acc_key] = dev
            try:
                with open(DEVICES_FILE, "w", encoding="utf-8") as f:
                    json.dump(devices, f, indent=4)
            except Exception:
                pass
            return dev

    new_device = _generate_new_device()
    devices[acc_key] = new_device
    try:
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, indent=4)
    except Exception as e:
        print_error(f"Failed to save device mapping: {e}")

    return new_device


# ==================== CLOUDFLARE DNS RESOLVER & SOCKET OPTIMIZERS ====================
CLOUDFLARE_PRIMARY_DNS = "1.1.1.1"
CLOUDFLARE_SECONDARY_DNS = "1.0.0.1"
_DNS_CACHE: Dict[str, Tuple[str, float]] = {}
_DNS_CACHE_TTL = 300.0  # 5 minutes DNS cache

async def resolve_host_cloudflare(hostname: str) -> str:
    if not hostname:
        return hostname

    parts = hostname.split('.')
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return hostname

    now = time.time()
    if hostname in _DNS_CACHE:
        ip, exp = _DNS_CACHE[hostname]
        if now < exp:
            return ip

    def _query_cloudflare(server_ip: str) -> Optional[str]:
        s = None
        try:
            tx_id = random.randint(1000, 65535)
            header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
            qname = b"".join(bytes([len(part)]) + part.encode('ascii') for part in hostname.split('.')) + b"\x00"
            query_pkt = header + qname + struct.pack(">HH", 1, 1)

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.2)
            s.sendto(query_pkt, (server_ip, 53))
            resp, _ = s.recvfrom(1024)

            if len(resp) >= 12:
                ancount = struct.unpack(">H", resp[6:8])[0]
                if ancount > 0:
                    offset = 12 + len(qname) + 4
                    for _ in range(ancount):
                        if offset >= len(resp):
                            break
                        if (resp[offset] & 0xC0) == 0xC0:
                            offset += 2
                        else:
                            while offset < len(resp) and resp[offset] != 0:
                                offset += 1 + resp[offset]
                            offset += 1
                        if offset + 10 > len(resp):
                            break
                        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", resp[offset:offset+10])
                        offset += 10
                        if rtype == 1 and rdlen == 4 and offset + 4 <= len(resp):
                            return socket.inet_ntoa(resp[offset:offset+4])
                        offset += rdlen
        except Exception:
            pass
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        return None

    loop = asyncio.get_running_loop()
    ip = await loop.run_in_executor(None, _query_cloudflare, CLOUDFLARE_PRIMARY_DNS)
    if not ip:
        ip = await loop.run_in_executor(None, _query_cloudflare, CLOUDFLARE_SECONDARY_DNS)
    if not ip:
        try:
            ip_info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
            if ip_info:
                ip = ip_info[0][4][0]
        except Exception:
            ip = hostname

    if ip:
        _DNS_CACHE[hostname] = (ip, now + _DNS_CACHE_TTL)
    return ip or hostname


def optimize_tcp_socket(sock: socket.socket):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Fast keep-alive probes on Windows (10s idle, 2s interval)
        if hasattr(socket, "SIO_KEEPALIVE_VALS") and os.name == 'nt':
            try:
                sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 2000))
            except Exception:
                pass
        elif hasattr(socket, "TCP_KEEPIDLE"):
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
            except Exception:
                pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
    except Exception:
        pass


async def safe_close_writer(writer):
    if not writer:
        return
    try:
        if not writer.is_closing():
            writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=1.5)
    except Exception:
        pass


def optimize_udp_socket(sock: socket.socket):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
        if hasattr(socket, 'SIO_UDP_CONNRESET') and os.name == 'nt':
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception:
                pass
    except Exception:
        pass

# ============================================================
# 🔥 ANTI-AFK — Movement + Fire Simulation
# ============================================================

async def send_anti_afk_movement(sock, server_addr, udp_key_bytes, match_code, account_id):
    """
    Simulated movement packet — server ko lagta hai player chal raha hai.
    Uses UDP_PLAYER_STATE_SYNC-like structure for anti-AFK.
    """
    try:
        nr = (await layouts_from_mask(match_code))[1]

        # Random direction vector (chhota movement)
        rand_x = random.randint(-100, 100)
        rand_y = random.randint(-50, 50)
        rand_z = random.randint(-100, 100)

        # Timestamp
        ts = int(time.time() * 1000) & 0xFFFFFFFF

        # Player ID
        try:
            player_id = int(account_id)
        except Exception:
            player_id = 0

        # Body: [player_id(4) | ts(4) | x(4) | y(4) | z(4) | state(2)]
        body = struct.pack("<IIiiiH", player_id & 0xFFFFFFFF, ts, rand_x, rand_y, rand_z, 1)

        # Movement ke liye RUDP cmd 104 (START_FIRE) ke bajaye generic player state
        # Use msg_key 0x6B (safe) aur cmd 2001 (UDP_PLAYER_STATE_SYNC)
        pkt = await build_packet(
            0x6B, nr, 0, 2001, None, 0,
            body, udp_key_bytes, encrypted=False
        )
        loop = asyncio.get_event_loop()
        await loop.sock_sendto(sock, pkt, server_addr)
        return True
    except Exception:
        return False


async def send_anti_afk_fire(sock, server_addr, udp_key_bytes, match_code, account_id):
    """
    Simulated fire button click — server ko active signal.
    Uses RUDP_START_FIRE (cmd 104) + RUDP_STOP_FIRE (cmd 105) sequence.
    """
    try:
        nr = (await layouts_from_mask(match_code))[1]

        try:
            player_id = int(account_id)
        except Exception:
            player_id = 0

        ts = int(time.time() * 1000) & 0xFFFFFFFF

        # FIRE START body: [player_id(4) | ts(4) | weapon_id(2) | fire_mode(1)]
        weapon_id = random.choice([101, 102, 103, 201, 202])   # random weapon
        fire_body = struct.pack("<IIHB", player_id & 0xFFFFFFFF, ts, weapon_id, 1)

        # Send FIRE START
        fire_pkt = await build_packet(
            0x68, nr, 0, 104, None, 0,
            fire_body, udp_key_bytes, encrypted=False
        )
        loop = asyncio.get_event_loop()
        await loop.sock_sendto(sock, fire_pkt, server_addr)

        # 150-300ms baad FIRE STOP bhejo
        await asyncio.sleep(random.uniform(0.15, 0.30))

        stop_body = struct.pack("<IIHB", player_id & 0xFFFFFFFF, ts + 200, weapon_id, 0)
        stop_pkt = await build_packet(
            0x68, nr, 0, 105, None, 0,
            stop_body, udp_key_bytes, encrypted=False
        )
        await loop.sock_sendto(sock, stop_pkt, server_addr)

        return True
    except Exception:
        return False


async def anti_afk_worker(sock, resolved_ip, port, udp_key_bytes, match_code,
                          account_id, stop_event, match_index):
    """
    Continuously sends movement + fire packets so server doesn't cancel the match.
    Runs for the entire match duration.
    """
    loop = asyncio.get_event_loop()
    server_addr = (resolved_ip, port)
    fire_count = 0
    move_count = 0

    try:
        # Match start hone ke 2-3 sec baad shuru karo
        await asyncio.sleep(2.5)

        while not stop_event.is_set():
            try:
                roll = random.random()

                if roll < ANTI_AFK_FIRE_CHANCE:
                    # 🔥 FIRE click
                    ok = await send_anti_afk_fire(
                        sock, server_addr, udp_key_bytes, match_code, account_id
                    )
                    if ok:
                        fire_count += 1
                    # Kabhi kabhi saath me movement bhi
                    if random.random() < 0.4:
                        await send_anti_afk_movement(
                            sock, server_addr, udp_key_bytes, match_code, account_id
                        )
                        move_count += 1
                else:
                    # 🎮 MOVEMENT only
                    ok = await send_anti_afk_movement(
                        sock, server_addr, udp_key_bytes, match_code, account_id
                    )
                    if ok:
                        move_count += 1

                # Random delay between 1.8s to 3.5s
                wait_time = random.uniform(ANTI_AFK_MIN_INTERVAL, ANTI_AFK_MAX_INTERVAL)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=wait_time)
                    break  # stop_event set ho gaya
                except asyncio.TimeoutError:
                    pass

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
    finally:
        # Summary log
        try:
            if fire_count + move_count > 0:
                print_info(
                    f"[🎮] Anti-AFK #{match_index} | Fires: {fire_count} | Moves: {move_count} | UID: {str(account_id)}"
                )
        except Exception:
            pass

# ==================== NETWORK & CRYPTO ====================
client = httpx.AsyncClient(
    verify=False,
    timeout=15.0,
    limits=httpx.Limits(max_connections=300, max_keepalive_connections=150)
)
_LOGIN_SEMAPHORE = asyncio.Semaphore(4)

headers = {
    'User-Agent': 'UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)',
    'Connection': 'Keep-Alive',
    'Accept-Encoding': 'gzip',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Expect': '100-continue',
    'X-Unity-Version': '2018.4.12f1',
    'X-GA-SV': '1789535859',
    'X-GA': 'v1 1',
    'ReleaseVersion': 'OB55'
}

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'

CRC7_TABLE = bytes([
    0, 9, 18, 27, 36, 45, 54, 63, 72, 65, 90, 83, 108, 101, 126, 119,
    25, 16, 11, 2, 61, 52, 47, 38, 81, 88, 67, 74, 117, 124, 103, 110,
    50, 59, 32, 41, 22, 31, 4, 13, 122, 115, 104, 97, 94, 87, 76, 69,
    43, 34, 57, 48, 15, 6, 29, 20, 99, 106, 113, 120, 71, 78, 85, 92,
    100, 109, 118, 127, 64, 73, 82, 91, 44, 37, 62, 55, 8, 1, 26, 19,
    125, 116, 111, 102, 89, 80, 75, 66, 53, 60, 39, 46, 17, 24, 3, 10,
    86, 95, 68, 77, 114, 123, 96, 105, 30, 23, 12, 5, 58, 51, 40, 33,
    79, 70, 93, 84, 107, 98, 121, 112, 7, 14, 21, 28, 35, 42, 49, 56,
    65, 72, 83, 90, 101, 108, 119, 126, 9, 0, 27, 18, 45, 36, 63, 54,
    88, 81, 74, 67, 124, 117, 110, 103, 16, 25, 2, 11, 52, 61, 38, 47,
    115, 122, 97, 104, 87, 94, 69, 76, 59, 50, 41, 32, 31, 22, 13, 4,
    106, 99, 120, 113, 78, 71, 92, 85, 34, 43, 48, 57, 6, 15, 20, 29,
    37, 44, 55, 62, 1, 8, 19, 26, 109, 100, 127, 118, 73, 64, 91, 82,
    60, 53, 46, 39, 24, 17, 10, 3, 116, 125, 102, 111, 80, 89, 66, 75,
    23, 30, 5, 12, 51, 58, 33, 40, 95, 86, 77, 68, 123, 114, 105, 96,
    14, 7, 28, 21, 42, 35, 56, 49, 70, 79, 84, 93, 98, 107, 112, 121,
])

_DELTA = 0x9E3779B9
_ROUNDS = 16
_FIELD_SIZES = {0: 1, 1: 2, 2: 2, 3: 1, 4: 2}
_FIELD_NAMES = {0: "sendOption", 1: "cmd", 2: "orderId", 3: "flags", 4: "length"}

sai_tail_dul = bytes.fromhex(
    "0101030101045452000103000100000410312e3133302e3232"
    "1432303139313231303430ca0163736f7665727365612e737472"
    "6f6e67686f6c642e66726565666972656d6f62696c652e636f6d"
    "3b302e302e302e303b33342e3132362e37362e34353b33342e38"
    "372e3137372e31343b33342e38372e3137302e3233303b33352e"
    "3138352e3138332e353700000000000001000000000000000000"
    "0000000100000000000100000000000100b8eeec91c5d7ffde110200"
)

class Colors:
    HEADER = '\033[95m'
    GREEN = '\033[92m'
    FAIL = '\033[91m'
    WARNING = '\033[93m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    WHITE = '\033[97m'
    ENDC = '\033[0m'

def print_colored(text, color=Colors.WHITE):
    try:
        print(f"{color}{text}{Colors.ENDC}")
    except Exception:
        try:
            print(f"{color}{text.encode('ascii', errors='replace').decode('ascii')}{Colors.ENDC}")
        except Exception:
            pass

def print_success(text):
    print_colored(f"[+] {text}", Colors.GREEN)
    try:
        bot_state.log(text, "success")
    except Exception:
        pass

def print_error(text):
    print_colored(f"[-] {text}", Colors.FAIL)
    try:
        bot_state.log(text, "error")
    except Exception:
        pass

def print_warning(text):
    print_colored(f"[!] {text}", Colors.WARNING)
    try:
        bot_state.log(text, "warning")
    except Exception:
        pass

def print_info(text):
    print_colored(f"[i] {text}", Colors.CYAN)
    try:
        bot_state.log(text, "info")
    except Exception:
        pass

def get_proto_field(d, key, default=None):
    if not d or not isinstance(d, dict):
        return default
    if key in d:
        val = d[key].get('data')
        return val if val is not None else default
    if str(key) in d:
        val = d[str(key)].get('data')
        return val if val is not None else default
    return default


# ==================== PER-ACCOUNT MATCH COUNTER ====================
_match_counters: Dict[str, int] = {}
_match_counter_lock = asyncio.Lock()

async def _inc_match(uid: str) -> int:
    async with _match_counter_lock:
        _match_counters[uid] = _match_counters.get(uid, 0) + 1
        return _match_counters[uid]

async def _dec_match(uid: str) -> int:
    async with _match_counter_lock:
        if uid in _match_counters and _match_counters[uid] > 0:
            _match_counters[uid] -= 1
        return _match_counters.get(uid, 0)

async def _get_match_count(uid: str) -> int:
    async with _match_counter_lock:
        return _match_counters.get(uid, 0)

async def _get_total_match_count() -> int:
    async with _match_counter_lock:
        return sum(_match_counters.values())


# ==================== TOKEN CACHE ====================
_token_cache_memo: Dict[str, Any] = {}
_token_cache_memo_time: float = 0.0
_TOKEN_CACHE_MEMO_TTL = 5.0

def _json_serializer(obj):
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_hex__": bytes(obj).hex()}
    raise TypeError(f"Type {type(obj)} not serializable")

def _json_deserializer(obj):
    if isinstance(obj, dict):
        if "__bytes_hex__" in obj and len(obj) == 1:
            try:
                return bytes.fromhex(obj["__bytes_hex__"])
            except Exception:
                return b""
        return {k: _json_deserializer(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_deserializer(x) for x in obj]
    return obj

def _load_token_cache() -> Dict[str, Any]:
    global _token_cache_memo, _token_cache_memo_time
    now = time.time()
    if _token_cache_memo and (now - _token_cache_memo_time) < _TOKEN_CACHE_MEMO_TTL:
        return _token_cache_memo

    if not os.path.exists(TOKEN_CACHE_FILE):
        return {}
    try:
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {}
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("Cache root must be dict")
        parsed = _json_deserializer(data)
        _token_cache_memo = parsed
        _token_cache_memo_time = now
        return parsed
    except Exception as e:
        print_error(f"Token cache corrupt → deleting: {e}")
        try:
            os.remove(TOKEN_CACHE_FILE)
        except Exception:
            pass
        return {}

def _save_token_cache(cache: Dict[str, Any]):
    global _token_cache_memo, _token_cache_memo_time
    try:
        tmp_file = TOKEN_CACHE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, default=_json_serializer)
        os.replace(tmp_file, TOKEN_CACHE_FILE)
        _token_cache_memo = cache
        _token_cache_memo_time = time.time()
    except Exception as e:
        print_error(f"Token cache save error: {e}")

def cache_get(uid: str) -> Optional[Dict]:
    cache = _load_token_cache()
    entry = cache.get(str(uid))
    if not entry:
        return None
    if time.time() - entry.get("cached_at", 0) > TOKEN_CACHE_TTL:
        print_info(f"[CACHE] UID {uid} expired. Re-login needed.")
        cache_invalidate(uid)
        return None
    if str(entry.get("account_id", "")).isdigit():
        entry["account_id"] = int(entry["account_id"])
    if not isinstance(entry.get("login_payload_data"), (bytes, bytearray)):
        print_warning(f"[CACHE] UID {uid} missing payload → invalidating")
        cache_invalidate(uid)
        return None
    return entry

def cache_set(uid: str, account_data: Dict):
    cache = _load_token_cache()
    entry = dict(account_data)
    entry["cached_at"] = time.time()
    cache[str(uid)] = entry
    _save_token_cache(cache)
    print_success(f"[CACHE] Saved credentials for UID {uid}")

def cache_invalidate(uid: str):
    cache = _load_token_cache()
    if str(uid) in cache:
        del cache[str(uid)]
        _save_token_cache(cache)
        print_warning(f"[CACHE] Invalidated: {uid}")


# ==================== ENCRYPTION & PROTOBUF ====================

async def aes_encrypt(payload, key, iv):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad(payload, AES.block_size))

_VERSION_CONFIG_CACHE = None
_VERSION_CONFIG_CACHE_TIME = 0.0
_VERSION_CONFIG_TTL = 1800.0  # 30 minutes cache for all accounts

async def get_playstore_version():
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: play_scraper('com.dts.freefiremax', lang='hi', country='id')
        )
        return result.get("version")
    except Exception:
        return "2.133.1"

async def version_config():
    app_version = await get_playstore_version()
    api_url = (
        "https://version.ggwhitehawk.com/live/ver.php"
        f"?version={app_version}"
        "&lang=hi&device=android&channel=android"
        "&appstore=googleplay&region=IND"
        "&whitelist_version=1.3.0&whitelist_sp_version=1.0.0"
    )
    try:
        response = await client.get(api_url)
        response.raise_for_status()
        data = response.json()
        
        server_url = data.get("server_url") or data.get("cdn_url") or "https://dl-tata.freefireind.in/live/ABHotUpdates/"
        remote_version = data.get("remote_version") or data.get("remote_option_version") or "OB55"
        latest_release_version = data.get("latest_release_version", "OB55")

        if not server_url:
            print_error(f"[VERCONFIG] Missing server_url in response: {data}")
            return None

        return latest_release_version, remote_version, server_url
    except Exception as e:
        print_error(f"[VERCONFIG] Error fetching version config: {e}")
        return None


async def get_access_token(uid, password):
    url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    hdrs = {
        "Host": "100067.connect.garena.com",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; SM-G998B Build/SP1A.210812.016)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "close"
    }
    data = {
        "uid": uid,
        "password": password,
        "response_type": "token",
        "client_type": "2",
        "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        "client_id": "100067"
    }
    for attempt in range(5):
        try:
            response = await client.post(url, headers=hdrs, data=data)
            if response.status_code == 200:
                response_data = response.json()
                open_id = response_data.get("open_id")
                access_token = response_data.get("access_token")
                platform = response_data.get("platform", 4)
                if open_id and access_token:
                    return open_id, access_token, platform
            if response.status_code == 429:
                await asyncio.sleep(1)
                continue
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None

async def parse_results(parsed_results):
    result_dict = {}
    for result in parsed_results:
        field_data = {"wire_type": result.wire_type}
        if result.wire_type == "varint":
            field_data["data"] = result.data
        elif result.wire_type == "string":
            field_data["data"] = result.data
        elif result.wire_type == "bytes":
            field_data["data"] = result.data
        elif result.wire_type == "length_delimited":
            if hasattr(result.data, "results"):
                field_data["data"] = await parse_results(result.data.results)
            elif isinstance(result.data, list):
                field_data["data"] = await parse_results(result.data)
            else:
                field_data["data"] = str(result.data)
        result_dict[str(result.field)] = field_data
    return result_dict

async def decode_protobuf(data):
    parsed_results = Parser().parse(data)
    parsed_results_dict = await parse_results(parsed_results)
    return json.dumps(parsed_results_dict)

async def build_majorlogin_payload(open_id, access_token, platform, client_version, device_info):
    try:
        proto = thunderFF_pb2.MajorLoginReq()
        proto.event_time = str(datetime.now())[:-7]
        proto.game_name = "free fire"
        
        # Ensure integer casting for platform types
        try:
            p_id = int(platform)
        except (ValueError, TypeError):
            p_id = 1
        proto.platform_id = 1 if p_id in [1, 4] else p_id
        
        proto.client_version = str(client_version)
        proto.client_version_code = "2019118527"
        
        proto.system_software = str(device_info.get("system_software", "Android OS 14 / API-34"))
        proto.system_hardware = str(device_info.get("brand", "Handheld"))
        proto.device_type = str(device_info.get("model", "Handheld"))
        proto.screen_width = int(device_info.get("screen_width", 1600))
        proto.screen_height = int(device_info.get("screen_height", 900))
        proto.screen_dpi = str(device_info.get("screen_dpi", "300"))
        proto.processor_details = str(device_info.get("processor_details", "x86-64 SSE3 SSE4.1 SSE4.2 AVX | 2400 | 4"))
        proto.memory = int(device_info.get("memory", 5951))
        proto.gpu_renderer = str(device_info.get("gpu_renderer", "Adreno (TM) 640"))
        proto.unique_device_id = str(device_info.get("unique_device_id", "Google|725030d8-6585-4f55-bcca-a6df7e59935b"))
        proto.client_ip = str(device_info.get("client_ip", "103.145.112.210"))
        
        proto.telecom_operator = "Citycell"
        proto.network_operator_a = "Citycell"
        proto.network_type = "WIFI"
        proto.network_type_a = "WIFI"
        proto.cpu_type = 2
        proto.cpu_architecture = "64"
        proto.gpu_version = "OpenGL ES 3.2"
        proto.graphics_api = "OpenGLES2"
        proto.language = "en"
        proto.open_id = str(open_id)
        proto.open_id_type = str(platform)
        proto.login_open_id_type = int(platform)
        proto.access_token = str(access_token)
        proto.login_by = 3
        proto.platform_sdk_id = 2
        proto.origin_platform_type = str(platform)
        proto.primary_platform_type = str(platform)
        proto.reg_avatar = 1
        proto.channel_type = 3
        
        proto.memory_available.version = 55
        proto.memory_available.hidden_value = 81
        
        proto.external_storage_total = 34308
        proto.external_storage_available = 30777
        proto.internal_storage_total = 2519
        proto.internal_storage_available = 243
        proto.game_disk_storage_total = 34308
        proto.game_disk_storage_available = 32224
        proto.external_sdcard_total_storage = 34308
        proto.external_sdcard_avail_storage = 32224
        
        proto.library_path = "/data/app/~~gMPXdINLbMwnOicaGqLIBQ==/com.dts.freefiremax-bebwEkFXYnMZtwtXImes-A==/lib/arm64"
        proto.library_token = "/data/app/~~gMPXdINLbMwnOicaGqLIBQ==/com.dts.freefiremax-bebwEkFXYnMZtwtXImes-A==/base.apk"
        proto.client_using_version = "7428b253defc164018c604a1ebbfebdf"
        proto.supported_astc_bitset = 4095
        proto.analytics_detail = b"FwQVTgUPX1UaUllDDwcWCRBpWAUOUgsvA1snWlBaO1kFYg=="
        proto.loading_time = 14582
        proto.release_channel = "android"
        proto.extra_info = "KqsHT4tDHGqm9PQ3syB24XA4N6SWy/Q/HfMFTQM+SgxmVqsgPK138ajtCFyVNW/Q7p6hxoenpRjeZ2NphiIosCZ3YDkONB5NAa+zTwNo7iabx/mj"
        proto.android_engine_init_flag = 111207
        proto.if_push = 1
        proto.is_vpn = 0
        
        payload = proto.SerializeToString()
        return await aes_encrypt(payload, AES_KEY, AES_IV)
    except Exception as e:
        print_error(f"[MAJORLOGIN_BUILD] Payload creation failed: {e}")
        print_error(traceback.format_exc())
        return None


async def send_majorlogin(data, release_version, server_url):
    if not data:
        print_error("[MAJORLOGIN] Encrypted payload is None. Check AES encryption or Protobuf serialization.")
        return None

    try:
        url = f"{server_url}MajorLogin" if server_url.endswith('/') else f"{server_url}/MajorLogin"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = str(release_version)
        
        response = await client.post(url, headers=req_headers, data=data)
        
        print_info(f"[MAJORLOGIN] Response Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print_error(f"[MAJORLOGIN] Server rejected payload with status {response.status_code}")
            return None
            
        response_content = response.content
        print_info(f"[MAJORLOGIN] Received {len(response_content)} bytes response")

        if len(response_content) < 40:
            print_error("[MAJORLOGIN] Received unexpected short response body.")
            return None

        # Protobuf Parsing Logic
        res_proto = thunderFF_pb2.MajorLoginRes()
        for offset in range(min(128, len(response_content))):
            try:
                res_proto.ParseFromString(response_content[offset:])
                if res_proto.region and res_proto.token:
                    print_success(f"[MAJORLOGIN] Auth successful! Region: {res_proto.region}")
                    return res_proto
            except Exception:
                continue

        print_error("[MAJORLOGIN] Could not parse valid region/token from server response bytes.")
        return None

    except Exception as e:
        print_error(f"[MAJORLOGIN] Connection error: {e}")
        return None

async def send_getlogin(data, base_url, token, release_version):
    try:
        url = f"{base_url.rstrip('/')}/GetLoginData"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = release_version
        req_headers['Authorization'] = f"Bearer {token}"
        req_headers['Host'] = "clientbp.ppmainecoonghj.com"
        response = await client.post(url, headers=req_headers, data=data)
        if response.status_code != 200:
            return None
        response_content = response.content

        res_proto = thunderFF_pb2.GetLoginDataRes()
        parsed_successfully = False
        try:
            res_proto.ParseFromString(response_content)
            if res_proto.functional_addrs or res_proto.informational_addrs:
                parsed_successfully = True
        except Exception:
            pass

        if not parsed_successfully:
            for offset in range(min(128, len(response_content))):
                try:
                    candidate = thunderFF_pb2.GetLoginDataRes()
                    candidate.ParseFromString(response_content[offset:])
                    if candidate.functional_addrs or candidate.informational_addrs:
                        res_proto = candidate
                        break
                except Exception:
                    pass

        dict_res = {}
        try:
            parsed = Parser().parse(response_content.hex())
            dict_res = await parse_results(parsed)
        except Exception:
            pass

        return res_proto, dict_res
    except Exception:
        return None

async def build_tcp_startup_packet(account_id, token, server_time, key, iv, region="BD", typ='OnLine'):
    uid_hex = f"{int(account_id):016x}"
    timestamp_hex = f"{int(server_time):08x}"
    encode_token = token.encode()
    encrypted_packet = (await aes_encrypt(encode_token, key, iv)).hex()
    encrypted_packet_length = f"{len(encrypted_packet) // 2:08x}"
    reg = str(region).upper() if region else "BD"
    if typ == 'OnLine':
        prefix = '7219' if reg == 'BD' else ('7214' if reg == 'IND' else '7215')
        return f"{prefix}{uid_hex}{timestamp_hex}00000000{encrypted_packet_length}{encrypted_packet}"
    else:  # ChaT / Informational
        prefix = '8119' if reg == 'BD' else ('8114' if reg == 'IND' else '8115')
        return f"{prefix}{uid_hex}{timestamp_hex}{encrypted_packet_length}{encrypted_packet}"

async def send_keep_alive(region="BD"):
    """Send 2-byte keep-alive pulse to maintain connection in OB55"""
    try:
        reg = str(region).upper() if region else "BD"
        ka_hex = "0219" if reg == "BD" else ("0214" if reg == "IND" else "0215")
        return bytes.fromhex(ka_hex)
    except Exception:
        return bytes.fromhex("0219")


async def start_game_battle_royale(region, client_version, writer, key, iv):
    packet = bytes.fromhex("080112800a0a010110013a110a044944433110aa011a064555524f50453a100a044944433210311a064555524f504540014a0801090a0b1219202758016291090a8001303838463832424630324139363736373032303130313030303030303030303030303136303030313030313530303032323246393745454530463030303030303436373632353134303030303030303030303030303030303030303030303030303030303030303030303030303066663030303030303030636163666131366410241afb02735d5e571400024a775d45414d1a041b1c001f11010449715f4243481a001e1d071c1703004b1a4066785c524570735c51486775421b5c5a4c07504042685a63610816054e19025e75196001477c015165406370195f5547404e4550640103020f1304064863754268676c755f65576e40467e5f0a417a4701026d675d6e73670b1108495a4c6a0b78470b740065645e525a057258425f584a447d4e6759440c11044e7c596d7f4b625f7d04055a47505c4e1d6b5b4107447d7201057d7f0f14084e430457674f7e517d72015172415d027473577c4d615f79535256780911030f4d5e027a797f614165067806505d53777750475e75064257076500460817014e741e7e5078487e7a7c465e7669767153497064605a7376677773550d160148037e18675966787f4c42607a645f577e7b441b460776025b18685d0b110205490060020f70676175654674706671797f41067346677c4e06585e780f15074c57047b40517075415f6364027259674b5b0166407f7340600407770a22047a5d5c52300b3a0a167305067162727516134208312e3133302e3232480350015ae90403626253513635686e556f4e36416456324b796f566c636f477776484f624e56526c4d727073504b4f43654177616848494176795556497273743752737149734a7a786b3247525268377a2f637664626d504f6a73552f79626d38547a4c69586d2f474351696d494b53486833447955726f39515152756c34545350626d6d624b7949565937545671577059455372323646572f59624578507338514f706d317372785455736c30796a434144444d4f34616a654b615753366361496c554b4963797a494e396d52516f715277687939797257476d337a644345337a6a61436f492f5a585233656f65365a42647a64677654636b6b665733356e4d4c6a6a565072564b6433523172756174394e50514150724a5546627859696c4c5a3859707336654d5447666b6649793574666a526c314d4648706b51774c6373374439656378566c41636f374e664f6d2b30654756466c4434744478706771385533595973587645384842502f70666c767a737138316a32524f4d7857437556445442492f684735625462773166456e4249725162762b636144775147696f74554e316d4c4b77734379456f4766706746614251457645672b736a764c4c78704743334c304a5344532f74526169504354553344374e6249306547516651622f5a466f4c36455630775a324d6f583932414c572f5049752f56634663584e70596b356f7966326151416a536971486a2f363276354843644f525551303578754e6171795251625653704654303137655237675255636b4966366c6f447476342b514e4a4670766d74757077707774396a5a5974437a4b56743657726d6e36785837706658456251555434684f3758a201050803108703a201050804108103a20105080510c001a20105081d10cc01a2010408161078a20105080e10af01a201020815")
    proto = thunderFF_pb2.StartMatch()
    proto.ParseFromString(packet)
    if hasattr(proto.main, 'region_list') and len(proto.main.region_list) > 0:
        proto.main.region_list[0].region = region
        if len(proto.main.region_list) > 1:
            proto.main.region_list[1].region = region
    if hasattr(proto.main, 'client_version'):
        proto.main.client_version.remote_version = client_version
    packet = proto.SerializeToString()
    encrypted_packet = (await aes_encrypt(packet, key, iv)).hex()
    packet_length = len(encrypted_packet) // 2
    hex_length = hex(packet_length)[2:]
    hex_length = hex_length if len(hex_length) > 1 else "0" + hex_length
    reg = str(region).upper() if region else "BD"
    reg_prefix = "031900" if reg == "BD" else ("031400" if reg == "IND" else "031500")
    final_packet = reg_prefix + "0" * (6 - len(hex_length)) + hex_length + encrypted_packet
    writer.write(bytes.fromhex(final_packet))
    await writer.drain()
    print_info(f"[⚔] Battle Royale Match Search Packet Sent ({packet_length} bytes, prefix: {reg_prefix}) | Region: {reg}")

async def start_game_lone_wolf(region, client_version, writer, key, iv):
    packet = bytes.fromhex("080112800a0a010b102b3a110a044944433110aa011a064555524f50453a100a044944433210311a064555524f504540014a0801090a0b1219202758016291090a8001303838463832424630324139363736373032303130313030303030303030303030303136303030313030313530303032323246393745454530463030303030303436373632353134303030303030303030303030303030303030303030303030303030303030303030303030303066663030303030303030636163666131366410241afb02735d5e571400024a775d45414d1a041b1c001f11010449715f4243481a001e1d071c1703004b1a4066785c524570735c51486775421b5c5a4c07504042685a63610816054e19025e75196001477c015165406370195f5547404e4550640103020f1304064863754268676c755f65576e40467e5f0a417a4701026d675d6e73670b1108495a4c6a0b78470b740065645e525a057258425f584a447d4e6759440c11044e7c596d7f4b625f7d04055a47505c4e1d6b5b4107447d7201057d7f0f14084e430457674f7e517d72015172415d027473577c4d615f79535256780911030f4d5e027a797f614165067806505d53777750475e75064257076500460817014e741e7e5078487e7a7c465e7669767153497064605a7376677773550d160148037e18675966787f4c42607a645f577e7b441b460776026b18685d0b110205490060020f70676175654674706671797f41067346677c4e06585e780f15074c57047b40517075415f6364027259674b5b0166407f7340600407770a22047a5d5c52300b3a0a167305067162727516134208312e3133302e3232480350015ae90403626253513635686e556f4e36416456324b796f566c636f477776484f624e56526c4d727073504b4f43654177616848494176795556497273743752737149734a7a786b3247525268377a2f637664626d504f6a73552f79626d38547a4c69586d2f474351696d494b53486833447955726f39515152756c34545350626d6d624b7949565937545671577059455372323646572f59624578507338514f706d317372785455736c30796a434144444d4f34616a654b615753366361496c554b4963797a494e396d52516f715277687939797257476d337a644345337a6a61436f492f5a585233656f65365a42647a64677654636b6b665733356e4d4c6a6a565072564b6433523172756174394e50514150724a5546627859696c4c5a3859707336654d5447666b6649793574666a526c314d4648706b51774c6373374439656378566c41636f374e664f6d2b30654756466c4434744478706771385533595973587645384842502f70666c767a737138316a32524f4d7857437556445442492f684735625462773166456e4249725162762b636144775147696f74554e316d4c4b77734379456f4766706746614251457645672b736a764c4c78704743334c304a5344532f74526169504354553344374e6249306547516651622f5a466f4c36455630775a324d6f583932414c572f5049752f56634663584e70596b356f7966326151416a536971486a2f363276354843644f525551303578754e6171795251625653704654303137655237675255636b4966366c6f447476342b514e4a4670766d74757077707774396a5a5974437a4b56743657726d6e36785837706658456251555434684f3758a201050803108703a201050804108103a20105080510c001a20105081d10cc01a2010408161078a20105080e10af01a201020815")
    proto = thunderFF_pb2.StartMatch()
    proto.ParseFromString(packet)
    if hasattr(proto.main, 'region_list') and len(proto.main.region_list) > 0:
        proto.main.region_list[0].region = region
        if len(proto.main.region_list) > 1:
            proto.main.region_list[1].region = region
    if hasattr(proto.main, 'client_version'):
        proto.main.client_version.remote_version = client_version
    packet = proto.SerializeToString()
    encrypted_packet = (await aes_encrypt(packet, key, iv)).hex()
    packet_length = len(encrypted_packet) // 2
    hex_length = hex(packet_length)[2:]
    hex_length = hex_length if len(hex_length) > 1 else "0" + hex_length
    reg = str(region).upper() if region else "BD"
    reg_prefix = "031900" if reg == "BD" else ("031400" if reg == "IND" else "031500")
    final_packet = reg_prefix + "0" * (6 - len(hex_length)) + hex_length + encrypted_packet
    writer.write(bytes.fromhex(final_packet))
    await writer.drain()
    print_info(f"[🐺] Lone Wolf Match Search Packet Sent ({packet_length} bytes, prefix: {reg_prefix}) | Region: {reg}")

async def has_ssan_zig(n):
    z = (n << 1) & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while z >= 0x80:
        out.append((z & 0x7F) | 0x80)
        z >>= 7
    out.append(z)
    return bytes(out)

async def uleb_encode(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)

async def tea_enc(v0, v1, k0, k1, k2, k3):
    s = 0
    for _ in range(_ROUNDS):
        s = (s + _DELTA) & 0xFFFFFFFF
        v0 = (v0 + (((((v1 << 4) & 0xFFFFFFFF) + k0) & 0xFFFFFFFF ^
                      ((v1 + s) & 0xFFFFFFFF) ^
                      (((v1 >> 5) + k1) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        v1 = (v1 + (((((v0 << 4) & 0xFFFFFFFF) + k2) & 0xFFFFFFFF ^
                      ((v0 + s) & 0xFFFFFFFF) ^
                      (((v0 >> 5) + k3) & 0xFFFFFFFF)))) & 0xFFFFFFFF
    return v0, v1

async def tea_dec(v0, v1, k0, k1, k2, k3):
    s = (_DELTA * _ROUNDS) & 0xFFFFFFFF
    for _ in range(_ROUNDS):
        v1 = (v1 - (((((v0 << 4) & 0xFFFFFFFF) + k2) & 0xFFFFFFFF ^
                      ((v0 + s) & 0xFFFFFFFF) ^
                      (((v0 >> 5) + k3) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        v0 = (v0 - (((((v1 << 4) & 0xFFFFFFFF) + k0) & 0xFFFFFFFF ^
                      ((v1 + s) & 0xFFFFFFFF) ^
                      (((v1 >> 5) + k1) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        s = (s - _DELTA) & 0xFFFFFFFF
    return v0, v1

async def tea_cbc_encrypt(padded, key_bytes):
    k0, k1, k2, k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0, 4, 8, 12))
    out = bytearray(len(padded))
    prev_cipher = bytearray(8)
    prev_intermediate = bytearray(8)
    for i in range(0, len(padded), 8):
        xored = bytearray(8)
        for j in range(8):
            xored[j] = padded[i + j] ^ prev_cipher[j]
        e0, e1 = await tea_enc(
            struct.unpack_from("<I", xored, 0)[0],
            struct.unpack_from("<I", xored, 4)[0],
            k0, k1, k2, k3,
        )
        enc = bytearray(8)
        struct.pack_into("<I", enc, 0, e0)
        struct.pack_into("<I", enc, 4, e1)
        for j in range(8):
            out[i + j] = enc[j] ^ prev_intermediate[j]
        prev_cipher[:] = out[i:i + 8]
        prev_intermediate[:] = xored
    return bytes(out)

async def build_padded(content):
    pad_len = (8 - (len(content) + 10) % 8) % 8
    return bytes([pad_len, 0, 0]) + b"\x00" * pad_len + content + b"\x00" * 7

async def encode_header(layout, send_option, cmd, order_id, flags, length, k, v80):
    out = bytearray()
    for code in layout:
        value = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: length}[code]
        if _FIELD_SIZES[code] == 1:
            out.append((value & 0xFF) ^ k)
        else:
            v = ((value & 0xFFFF) ^ v80) & 0xFFFF
            out.append(v & 0xFF)
            out.append((v >> 8) & 0xFF)
    return bytes(out)

async def crc7_buff(crc, buf):
    c = crc & 0x7F
    for b in buf:
        c = CRC7_TABLE[((2 * (c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F

async def sv_frame(msg_key, layout, send_option, cmd, order_id, flags, content, key, encrypted=True):
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0]) + await encode_header(layout, send_option, cmd, order_id, flags, len(body), k, v80)
    packet = bytearray(hdr + body)
    packet[1] = await crc7_buff(0, bytes(packet[2:])) & 0x7F
    return bytes(packet)

async def build_match_startup_packets(token, udp_key, match_code, account_id, block_val,
                                      server_ip="", region="IND", client_version="2.133.1",
                                      client_version_code="2019118527", access_token="",
                                      mode="BR"):
    token = token.strip()
    udp_key = bytes.fromhex(udp_key)
    match_code = [int(ch) for ch in str(match_code).strip()]

    # OB55 splits JWT match token at 660 bytes
    thunder_jwt = token[:660] if len(token) > 660 else token
    sharma_jwt = token[660:] if len(token) > 660 else ""
    encoded_thunder_jwt = thunder_jwt.encode() if isinstance(thunder_jwt, str) else thunder_jwt
    encoded_sharma_jwt = sharma_jwt.encode() if isinstance(sharma_jwt, str) else sharma_jwt

    garena420 = await has_ssan_zig(len(encoded_thunder_jwt)) + encoded_thunder_jwt

    reg = str(region).upper() if region else "BD"
    
    # Mode-dependent Sharma packet payload
    if mode == "BR":
        csoversea_block = bytes.fromhex(
            "ca0163736f7665727365612e7374726f6e67686f6c642e66726565666972656d6f62696c652e636f6d"
            "3b302e302e302e303b33342e3132362e37362e34353b33342e38372e3137372e31343b33342e38372e"
            "3137302e3233303b33352e3138352e3138332e35370000000000000100000000000000000000000001"
            "00000000000100010000000100b09df8c5fad88bdf110200"
        )
        m_val1 = 1
        m_val2 = 1
    else:  # LONE_WOLF
        csoversea_block = bytes.fromhex(
            "ca0163736f7665727365612e7374726f6e67686f6c642e66726565666972656d6f62696c652e636f6d"
            "3b302e302e302e303b33342e3132362e37362e34353b33342e38372e3137372e31343b33342e38372e"
            "3137302e3233303b33352e3138352e3138332e35370000000000000100000000000000000000000001"
            "00000800000100000000000100a8a2d7bebd8d8bdf110200"
        )
        m_val1 = 43
        m_val2 = 11

    mid = bytes.fromhex('0000000001000102030101') + await has_ssan_zig(len(reg)) + reg.encode()
    mid += bytes.fromhex('0001030003000004')
    mid += await has_ssan_zig(len(client_version)) + client_version.encode()
    mid += await has_ssan_zig(len(client_version_code)) + client_version_code.encode()
    mid += csoversea_block

    clean_ip = server_ip.split(':')[0] if server_ip else "0.0.0.0"
    mid += await has_ssan_zig(len(clean_ip)) + clean_ip.encode()

    clean_acc_tok = access_token.strip() if access_token else ""
    if clean_acc_tok:
        mid += await has_ssan_zig(len(clean_acc_tok)) + clean_acc_tok.encode()

    mid += await has_ssan_zig(len(encoded_sharma_jwt)) + encoded_sharma_jwt

    tg_garena420 = (
        await uleb_encode(int(account_id)) +
        await uleb_encode(int(block_val)) +
        await uleb_encode(1) +
        await uleb_encode(m_val1) +
        await uleb_encode(int(block_val)) +
        await uleb_encode(m_val2) +
        mid
    )

    process = await sv_frame(0x5E, match_code, 2, 447, 0, 1, garena420, udp_key)
    loading = await sv_frame(0x5A, match_code, 2, 448, 1, 1, tg_garena420, udp_key)
    return process.hex(), loading.hex()

async def produce_xor_key(secret_key):
    k = secret_key[0] if secret_key and len(secret_key) > 0 else 10
    return k, ((k << 8) | k) & 0xFFFF

async def parse_layout(layout):
    if isinstance(layout, str):
        return [int(ch) for ch in layout.strip()]
    return list(layout)

async def tea_cbc_decrypt(body, key_bytes):
    k0, k1, k2, k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0, 4, 8, 12))
    out = bytearray(len(body))
    prev_intermediate = bytearray(8)
    prev_cipher = bytearray(8)
    xored = bytearray(8)
    dec = bytearray(8)
    for i in range(0, len(body), 8):
        for j in range(8):
            xored[j] = body[i + j] ^ prev_intermediate[j]
        d0, d1 = await tea_dec(
            struct.unpack_from("<I", xored, 0)[0],
            struct.unpack_from("<I", xored, 4)[0],
            k0, k1, k2, k3
        )
        struct.pack_into("<I", dec, 0, d0)
        struct.pack_into("<I", dec, 4, d1)
        for j in range(8):
            out[i + j] = dec[j] ^ prev_cipher[j]
        prev_cipher[:] = body[i:i + 8]
        prev_intermediate[:] = dec
    return bytes(out)

async def build_hello_packet(text, key, layout):
    data = text.encode("utf-8")
    if len(data) > 25:
        raise ValueError(f"Text is too long ({len(data)} bytes)")
    content = b"\x10\x00\x00\x00" + data + b"\x00" * (29 - 4 - len(data))
    k, v80 = await produce_xor_key(key)
    layout = await parse_layout(layout)
    padded = await build_padded(content)
    enc_body = await tea_cbc_encrypt(padded, key)
    header_bytes = await encode_header(layout, 1, 1, 0, 1, len(enc_body), k, v80)
    packet = bytearray([0x63, 0x00]) + header_bytes + enc_body
    packet[1] = await crc7_buff(0, packet[2:]) & 0x7F
    return bytes(packet).hex()

async def classify(frame):
    cmd = frame["cmd"]
    msg_name = MESSAGE_ID_TO_NAME.get(cmd, f"UNKNOWN_{cmd}")
    if msg_name == "UDP_HELLO":
        return "HELLO"
    if msg_name == "UDP_ACK":
        return "ACK"
    if msg_name == "UDP_PING":
        return "PING"
    if msg_name == "RUDP_JOIN_MATCH":
        return "JOIN_MATCH"
    if msg_name.startswith("RUDP_"):
        return msg_name
    if msg_name.startswith("UDP_"):
        return msg_name
    return "DATA"

async def build_packet(msg_key, layout, send_option, cmd, order_id, flags, content, key, encrypted=True):
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0])
    for code in layout:
        value = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: len(body)}[code]
        if _FIELD_SIZES[code] == 1:
            hdr.append((value & 0xFF) ^ k)
        else:
            v = ((value & 0xFFFF) ^ v80) & 0xFFFF
            hdr.append(v & 0xFF)
            hdr.append((v >> 8) & 0xFF)
    packet = bytearray(hdr + body)
    packet[1] = await crc7_buff(0, bytes(packet[2:])) & 0x7F
    return bytes(packet)

async def layouts_from_mask(mask):
    ru = [int(c) for c in str(mask).strip()]
    nr = [c for c in ru if c != 2]
    return ru, nr

async def reply_for(frame, key, mask, ack_key=0x68, ping_key=0x6D, hello_key=0x5B, ack_style="short"):
    ru, nr = await layouts_from_mask(mask)
    typ = await classify(frame)
    if typ == "HELLO":
        if ack_style == "echo":
            content = frame["content"] if frame["content"] else b"\x10\x00\x00\x00"
            return typ, await build_packet(hello_key, nr, 1, 1, None, 1, content, key)
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x01\x00", key)
    if typ == "ACK":
        content = frame["content"] if frame["content"] else b"\x01\x00"
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, content, key)
    if typ == "PING":
        c = frame["content"]
        counter = c[:4] if len(c) >= 4 else c
        return typ, await build_packet(ping_key, nr, 0, 3, None, 0, counter + b"\x00\x00\x00", key, encrypted=False)
    if typ == "JOIN_MATCH":
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x02\x00", key)
    return typ, None

async def keepalive_ping(sock, ip, port, key_bytes, mask, stop_event):
    nr = (await layouts_from_mask(mask))[1]
    ping_keys = [0x66, 0x6D, 0x69, 0x6C, 0x6B, 0x6E, 0x6F, 0x70]
    loop = asyncio.get_event_loop()
    i = 0
    while not stop_event.is_set():
        pk = ping_keys[i % len(ping_keys)]
        counter = int(time.time() * 1000) & 0xFFFFFFFF
        pkt = await build_packet(pk, nr, 0, 3, None, 0, struct.pack("<I", counter) + b"\x00\x00\x00", key_bytes, encrypted=False)
        try:
            await loop.sock_sendto(sock, pkt, (ip, port))
        except Exception:
            pass
        i += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

async def try_header(buf, layout, k, v80):
    off = 2
    out = {}
    for code in layout:
        size = _FIELD_SIZES[code]
        if off + size > len(buf):
            return None
        out[_FIELD_NAMES[code]] = (buf[off] ^ k) if size == 1 else ((buf[off] | (buf[off + 1] << 8)) ^ v80) & 0xFFFF
        off += size
    out["headerLen"] = off
    return out

async def oicq_unpad(padded):
    if not padded or len(padded) < 8:
        return None
    if not all(padded[-1 - i] == 0 for i in range(7)):
        return None
    pad_len = padded[0] & 0x07
    s = 3 + pad_len
    e = len(padded) - 7
    return padded[s:e] if s < e else b""

async def decode_packet(packet, key, mask=None):
    data = bytes(packet) if isinstance(packet, bytes) else bytes.fromhex(packet)
    if len(data) < 8:
        return None
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    crc_ok = (data[1] & 0x7F) == await crc7_buff(0, data[2:])
    candidates = []
    if mask:
        ru, nr = await layouts_from_mask(mask)
        layouts = [("RUDP", ru), ("nonRUDP", nr)]
    else:
        layouts = [("RUDP", list(p)) for p in itertools.permutations([0, 1, 2, 3, 4])]
        layouts += [("nonRUDP", list(p)) for p in itertools.permutations([0, 1, 3, 4])]
    for kind, layout in layouts:
        f = await try_header(data, layout, k, v80)
        if not f:
            continue
        if f["flags"] > 7 or f["sendOption"] > 7:
            continue
        if f["length"] != len(data) - f["headerLen"]:
            continue
        body = data[f["headerLen"]:f["headerLen"] + f["length"]]
        content = None
        padded = None
        if f["flags"] & 1:
            if len(body) < 8 or len(body) % 8 != 0:
                continue
            padded = await tea_cbc_decrypt(body, key)
            content = await oicq_unpad(padded)
            if content is None:
                continue
        else:
            content = body
        score = (1 if crc_ok else 0) + (1 if content is not None else 0)
        candidates.append({
            "kind": kind, "layout": layout, "headerLen": f["headerLen"],
            "msgKey": data[0], "cmd": f["cmd"], "flags": f["flags"],
            "sendOption": f["sendOption"], "orderId": f.get("orderId"),
            "length": f["length"], "content": content, "crcOk": crc_ok,
            "padded": padded, "score": score, "total": len(data),
        })
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["kind"] == "RUDP" or c["kind"] == "nonRUDP", c["score"]), reverse=True)
    return candidates[0]


# ============================================================
# play_game — UDP MATCH (FIXED & DNS OPTIMIZED)
# ============================================================
async def refresh_account_profile(account_id):
    pass

async def play_game(server_ip_port, thunder, sharma, udp_key, match_code,
                    account_id, player_region, client_version, key, iv,
                    match_index: int):
    match_start_time = time.time()
    ping_task = None
    anti_afk_task = None
    sock = None
    ping_stop = asyncio.Event()
    uid_str = str(account_id)
    completed_cleanly = False

    try:
        ip, port = server_ip_port.split(":")
        port = int(port)
        resolved_ip = await resolve_host_cloudflare(ip)

        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('0.0.0.0', 0))
        except Exception:
            pass
        optimize_udp_socket(sock)
        sock.setblocking(False)

        udp_key_bytes = bytes.fromhex(udp_key)
        hello_packet = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
        await loop.sock_sendto(sock, bytes.fromhex(hello_packet), (resolved_ip, port))

        ack_state = "waiting_for_hello_reply"
        thunder_sent = False
        sharma_sent = False
        join_match_received = False
        local_closed = False
        send_lock = asyncio.Lock()

        ping_task = asyncio.create_task(
            keepalive_ping(sock, resolved_ip, port, udp_key_bytes, match_code, ping_stop)
        )

        # ANTI-AFK task — movement + fire simulation
        if ANTI_AFK_ENABLED:
            anti_afk_task = asyncio.create_task(
                anti_afk_worker(
                    sock, resolved_ip, port, udp_key_bytes, match_code,
                    account_id, ping_stop, match_index
                )
            )
            print_info(f"[ANTI-AFK] Started | Match #{match_index} | UID: {uid_str}")

        last_activity = time.time()
        MAX_IDLE_BEFORE_HELLO_RESEND = 7.0

        async def send_thunder_sharma_inline():
            nonlocal ack_state, thunder_sent, sharma_sent
            if thunder_sent:
                return
            async with send_lock:
                if thunder_sent:
                    return
                try:
                    await loop.sock_sendto(sock, bytes.fromhex(thunder), (resolved_ip, port))
                    thunder_sent = True
                    await asyncio.sleep(0.3)
                    prepare_ack = await build_packet(
                        0x68, (await layouts_from_mask(match_code))[1],
                        0, 2, None, 1, b"\x01\x00", udp_key_bytes
                    )
                    await loop.sock_sendto(sock, prepare_ack, (resolved_ip, port))
                    await asyncio.sleep(0.4)
                    await loop.sock_sendto(sock, bytes.fromhex(sharma), (resolved_ip, port))
                    sharma_sent = True
                    ack_state = "thunder_sharma_sent"
                except Exception:
                    pass

        while not local_closed:
            if time.time() - match_start_time > MAX_MATCH_DURATION:
                break
            try:
                response, server_addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 65535), timeout=1.5
                )
                if response:
                    last_activity = time.time()
                    frame = await decode_packet(response, udp_key_bytes, match_code)
                    if frame:
                        ptype = await classify(frame)

                        if frame['cmd'] in [103, 107]:
                            completed_cleanly = True
                            local_closed = True
                            continue

                        if frame['cmd'] == 101:
                            try:
                                ack_pkt = await build_packet(
                                    0x68, (await layouts_from_mask(match_code))[1],
                                    0, 2, None, 1, b"\x01\x00", udp_key_bytes
                                )
                                await loop.sock_sendto(sock, ack_pkt, server_addr)
                            except Exception:
                                pass
                            continue

                        if ptype in ["ACK", "PING", "HELLO", "JOIN_MATCH"]:
                            if ptype == "HELLO" and ack_state == "waiting_for_hello_reply":
                                typ, reply = await reply_for(
                                    frame, udp_key_bytes, match_code, ack_style="short"
                                )
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                                ack_state = "ack_sent_waiting"
                            elif ptype == "ACK":
                                if ack_state == "waiting_for_hello_reply":
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply:
                                        await loop.sock_sendto(sock, reply, server_addr)
                                    ack_state = "ready_to_send_thunder"
                                elif ack_state == "ack_sent_waiting":
                                    ack_state = "ready_to_send_thunder"
                                else:
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply:
                                        await loop.sock_sendto(sock, reply, server_addr)
                            elif ptype == "PING":
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                            elif ptype == "JOIN_MATCH" and not join_match_received:
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                                    join_match_received = True
            except asyncio.TimeoutError:
                if ack_state == "ready_to_send_thunder" and not thunder_sent:
                    await send_thunder_sharma_inline()
                elif ack_state == "waiting_for_hello_reply":
                    if (time.time() - last_activity) > MAX_IDLE_BEFORE_HELLO_RESEND:
                        try:
                            pkt = await build_hello_packet(
                                f"{account_id}_2585", udp_key_bytes, match_code
                            )
                            await loop.sock_sendto(sock, bytes.fromhex(pkt), (resolved_ip, port))
                        except Exception:
                            pass
                        last_activity = time.time()
                    if (time.time() - match_start_time) > 25.0:
                        print_warning(f"[MATCH #{match_index}] Handshake timeout")
                        break
                elif ack_state == "thunder_sharma_sent":
                    if (time.time() - last_activity) > MATCH_IDLE_TIMEOUT:
                        completed_cleanly = True
                        break
                continue
            except BlockingIOError:
                await asyncio.sleep(0.05)
            except OSError:
                await asyncio.sleep(0.5)
                continue
            except Exception:
                await asyncio.sleep(0.5)
                continue

            if ack_state == "ready_to_send_thunder" and not thunder_sent:
                await send_thunder_sharma_inline()

        return f"match #{match_index} finished"

    except Exception:
        return f"match #{match_index} error"

    finally:
        if completed_cleanly:
            try:
                bot_state.increment_match(uid_str)
                print_success(f"[★] Match #{match_index} Complete | UID: {uid_str}")

                async def _post_match_exp_check(uid):
                    try:
                        await asyncio.sleep(1.5)
                        await refresh_account_profile(uid)
                    except Exception:
                        pass
                asyncio.create_task(_post_match_exp_check(uid_str))
            except Exception:
                pass

        ping_stop.set()

        if anti_afk_task:
            anti_afk_task.cancel()
            try:
                await anti_afk_task
            except (asyncio.CancelledError, Exception):
                pass

        if ping_task:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass

        if sock:
            try:
                sock.close()
            except Exception:
                pass

        remaining = await _dec_match(uid_str)
        try:
            bot_state.update_status(uid_str, "IN_MATCH" if remaining > 0 else "ONLINE", remaining)
        except Exception:
            pass

# ============================================================
# 🔥 functional_lone_wolf — TRUE Parallel + Smart Cache + DNS
# ============================================================
async def functional_lone_wolf(addrs, starter_packet, account_region, client_version,
                                key, iv, account_id="", account_data=None,
                                max_reconnects=10):
    reconnects = 0
    ip, port = addrs.split(":")
    play_matches: List[asyncio.Task] = []
    no_response_count = 0
    search_attempts = 0
    last_start_time = 0.0
    uid_str = str(account_id)

    consecutive_parse_failures = 0

    current_token = starter_packet
    current_key = key
    current_iv = iv
    current_account_data = account_data

    def get_current_mode() -> Tuple[str, int]:
        cur_lvl = bot_state.get_account_level(uid_str)
        if cur_lvl == 1 and current_account_data and "level" in current_account_data:
            cur_lvl = max(cur_lvl, int(current_account_data.get("level", 1) or 1))
        cur_mode = "BR" if cur_lvl < 3 else "LONE_WOLF"
        return cur_mode, cur_lvl

    try:
        while True:
            # Check pause status before establishing TCP connection
            while bot_state.is_paused(uid_str):
                try:
                    bot_state.update_status(uid_str, "PAUSED", 0)
                except Exception:
                    pass
                await asyncio.sleep(1.0)

            # If already at max concurrent matches, wait for an active match to complete
            while True:
                play_matches[:] = [m for m in play_matches if not m.done()]
                if len(play_matches) < MAX_CONCURRENT_MATCHES:
                    break
                await asyncio.sleep(1.0)

            writer = None
            try:
                if current_account_data:
                    fresh = None
                    if current_account_data.get('auth_type') == 'guest' and current_account_data.get('auth_uid'):
                        fresh = cache_get(str(current_account_data['auth_uid']))
                    elif current_account_data.get('auth_type') == 'token' and current_account_data.get('auth_token'):
                        fresh = cache_get(f"tok_{current_account_data['auth_token'][:20]}")

                    if fresh:
                        current_account_data = fresh
                        current_key = fresh['aes_ak']
                        current_iv = fresh['iv_i']
                        current_token = await build_tcp_startup_packet(
                            fresh['account_id'],
                            fresh['token'],
                            fresh['server_time'],
                            current_key,
                            current_iv,
                            region=fresh.get('region', account_region),
                            typ='OnLine'
                        )
                    else:
                        print_warning(f"[FUNCTIONAL] Cache miss for {uid_str} → re-login needed")
                        try:
                            if current_account_data.get('auth_uid'):
                                cache_invalidate(str(current_account_data['auth_uid']))
                            if current_account_data.get('auth_token'):
                                cache_invalidate(f"tok_{current_account_data['auth_token'][:20]}")
                        except Exception:
                            pass
                        raise ConnectionError("Cache expired, triggering fresh login")

                resolved_ip = await resolve_host_cloudflare(ip)
                reader, writer = await asyncio.open_connection(resolved_ip, int(port))
                bot_state.register_writer(uid_str, writer)

                raw_sock = writer.get_extra_info('socket')
                if raw_sock:
                    optimize_tcp_socket(raw_sock)

                writer.write(bytes.fromhex(current_token))
                await writer.drain()

                # Send initial keepalive pulse right after connecting in OB55
                try:
                    init_ka = await send_keep_alive(account_region)
                    if init_ka and writer and not writer.is_closing():
                        writer.write(init_ka)
                        await asyncio.wait_for(writer.drain(), timeout=3)
                except Exception:
                    pass

                # Persistent Gateway Keepalive task to maintain TCP connection in OB55
                async def func_gateway_keepalive():
                    ka_bytes = await send_keep_alive(account_region)
                    while True:
                        await asyncio.sleep(5)
                        try:
                            if writer and not writer.is_closing():
                                writer.write(ka_bytes)
                                await writer.drain()
                        except Exception:
                            break

                gateway_ping_task = asyncio.create_task(func_gateway_keepalive())

                print_success(f"[✓] TCP Gateway Connected | UID: {uid_str}")
                reconnects = 0
                no_response_count = 0
                last_start_time = 0.0

                async def send_start_match():
                    nonlocal search_attempts, last_start_time
                    search_attempts += 1
                    current_region = "IND"
                    cur_mode, cur_lvl = get_current_mode()
                    try:
                        await asyncio.sleep(random.uniform(0.2, 0.4))
                        if cur_mode == "BR":
                            print_info(f"[⚔] Account Level {cur_lvl} (< 3) → Searching for Battle Royale Match... | UID: {uid_str} | Attempt #{search_attempts}")
                            await start_game_battle_royale(
                                current_region, client_version, writer,
                                current_key, current_iv
                            )
                        else:
                            print_info(f"[🐺] Account Level {cur_lvl} (>= 3) → Searching for Lone Wolf Match... | UID: {uid_str} | Attempt #{search_attempts}")
                            await start_game_lone_wolf(
                                current_region, client_version, writer,
                                current_key, current_iv
                            )
                        active = await _get_match_count(uid_str)
                        try:
                            bot_state.update_status(uid_str, f"SEARCHING ({cur_mode})", active)
                        except Exception:
                            pass
                    except Exception as e:
                        print_warning(f"[!] StartMatch attempt notice: {e}")
                    last_start_time = asyncio.get_running_loop().time()

                play_matches[:] = [m for m in play_matches if not m.done()]
                if len(play_matches) < MAX_CONCURRENT_MATCHES:
                    await send_start_match()

                while True:
                    play_matches[:] = [m for m in play_matches if not m.done()]

                    if bot_state.is_paused(uid_str):
                        try:
                            bot_state.update_status(uid_str, "PAUSED", 0)
                        except Exception:
                            pass
                        print_warning(f"[⏸] UID {uid_str} PAUSED. Closing TCP gateway socket...")
                        bot_state.unregister_writer(uid_str, writer)
                        if 'gateway_ping_task' in locals() and gateway_ping_task:
                            gateway_ping_task.cancel()
                        await safe_close_writer(writer)
                        writer = None
                        while bot_state.is_paused(uid_str):
                            await asyncio.sleep(1.0)
                        print_success(f"[▶] UID {uid_str} RESUMED. Reconnecting TCP Gateway...")
                        break

                    active_count = await _get_match_count(uid_str)
                    has_active_match = any(not m.done() for m in play_matches)
                    cur_mode, cur_lvl = get_current_mode()
                    current_status = f"IN_MATCH ({cur_mode})" if (active_count > 0 or has_active_match) else f"ONLINE ({cur_mode})"
                    try:
                        bot_state.update_status(uid_str, current_status, active_count)
                    except Exception:
                        pass

                    now = asyncio.get_running_loop().time()
                    if (now - last_start_time >= START_MATCH_INTERVAL) and (len(play_matches) < MAX_CONCURRENT_MATCHES):
                        await send_start_match()

                    try:
                        resp_data = await asyncio.wait_for(reader.read(4096), timeout=3.0)
                        if not resp_data:
                            no_response_count += 1
                            if no_response_count >= 5 and len(play_matches) == 0:
                                print_warning(f"[!] Gateway inactive | UID: {uid_str}")
                                break
                            continue
                        no_response_count = 0
                    except asyncio.TimeoutError:
                        continue

                    # Attempt to parse match allocation response from TCP Gateway
                    parsed_text = None
                    try:
                        parsed_text = await decode_protobuf(resp_data)
                    except Exception:
                        pass

                    server_ip_port = None
                    udp_key = None
                    match_code = None
                    block_val = None

                    if parsed_text:
                        try:
                            res_json = json.loads(parsed_text)
                            server_ip_port = get_proto_field(res_json, "1")
                            udp_key = get_proto_field(res_json, "2")
                            match_code = get_proto_field(res_json, "3")
                            block_val = get_proto_field(res_json, "4")
                        except Exception:
                            pass

                    if server_ip_port and udp_key and match_code:
                        consecutive_parse_failures = 0
                        match_idx = await _inc_match(uid_str)
                        print_success(f"[⚔] Match #{match_idx} Allocated! Server: {server_ip_port} | Mode: {cur_mode}")

                        acc_tok = current_account_data.get('access_token', '') if current_account_data else ""
                        match_jwt_token = current_account_data.get('token', '') if current_account_data else ""

                        thunder, sharma = await build_match_startup_packets(
                            token=match_jwt_token,
                            udp_key=udp_key,
                            match_code=match_code,
                            account_id=account_id,
                            block_val=block_val or 1,
                            server_ip=server_ip_port,
                            region=account_region,
                            client_version=client_version,
                            access_token=acc_tok,
                            mode=cur_mode
                        )

                        task = asyncio.create_task(
                            play_game(
                                server_ip_port, thunder, sharma, udp_key,
                                match_code, account_id, account_region,
                                client_version, current_key, current_iv,
                                match_idx
                            )
                        )
                        play_matches.append(task)
                        await asyncio.sleep(NEW_MATCH_DELAY)
                    else:
                        consecutive_parse_failures += 1

            except (ConnectionResetError, BrokenPipeError, ConnectionError, OSError) as e:
                reconnects += 1
                print_warning(f"[!] Connection lost for {uid_str} ({e}). Reconnect attempt #{reconnects}...")
                await asyncio.sleep(min(2 * reconnects, 10))
            except Exception as e:
                reconnects += 1
                print_error(f"[-] Gateway Error on {uid_str}: {e}")
                await asyncio.sleep(3)
            finally:
                bot_state.unregister_writer(uid_str, writer)
                if 'gateway_ping_task' in locals() and gateway_ping_task:
                    gateway_ping_task.cancel()
                await safe_close_writer(writer)

    except asyncio.CancelledError:
        print_warning(f"[!] Bot worker cancelled for UID {uid_str}")
    finally:
        if play_matches:
            await asyncio.gather(*play_matches, return_exceptions=True)
        try:
            bot_state.update_status(uid_str, "OFFLINE", 0)
        except Exception:
            pass


# ==================== MAIN SCRIPT ENTRY ====================
async def main():
    print_colored("==================================================", Colors.HEADER)
    print_colored("         FREE FIRE BOT CLIENT INITIALIZING        ", Colors.HEADER)
    print_colored("==================================================", Colors.HEADER)

    # Initialize web dashboard
    asyncio.create_task(start_web_dashboard(host=WEB_HOST, port=WEB_PORT))
    print_success(f"Dashboard running on http://{WEB_HOST}:{WEB_PORT}")

    # Synchronize persistent device profiles
    sync_devices_with_accounts()

    # Load release/version configs
    ver_info = await version_config()
    if ver_info:
        rel_ver, rem_ver, srv_url = ver_info
        print_info(f"Version Info: Release={rel_ver}, Remote={rem_ver}")
    else:
        rel_ver, rem_ver, srv_url = "OB55", "OB55", "https://dl-tata.freefireind.in/live/ABHotUpdates/"

    accounts = load_accounts()
    if not accounts:
        print_warning("No accounts found in accounts.json. Waiting for Dashboard input...")
        while True:
            await asyncio.sleep(10)

    for acc in accounts:
        uid = str(acc.get("uid", acc.get("token", "Unknown")[:10]))
        print_info(f"Loaded account: {uid}")

    print_success("Bot initialization completed. Control via Web Dashboard.")

    # Keep background loops running
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_warning("\n[!] Bot Client Shutting Down...")
