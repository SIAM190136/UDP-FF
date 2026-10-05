import traceback

async def version_config():
    app_version = await get_playstore_version()
    api_url = (
        "https://version.ggwhitehawk.com/live/ver.php"
        f"?version={app_version}"
        "&lang=hi&device=android&channel=android"
        "&appstore=googleplay&region=BD"
        "&whitelist_version=1.3.0&whitelist_sp_version=1.0.0"
    )
    try:
        response = await client.get(api_url)
        response.raise_for_status()
        data = response.json()
        server_url = data.get("server_url")
        remote_version = data.get("remote_version")
        latest_release_version = data.get("latest_release_version")
        if not server_url or not remote_version or not latest_release_version:
            print_error(f"[VERCONFIG] Missing expected fields in response: {data}")
            return None
        return latest_release_version, remote_version, server_url
    except Exception as e:
        print_error(f"[VERCONFIG] Error fetching version config: {e}")
        return None

async def build_majorlogin_payload(open_id, access_token, platform, client_version, device_info):
    try:
        proto = thunderFF_pb2.MajorLoginReq()
        proto.event_time = str(datetime.now())[:-7]
        proto.game_name = "free fire"
        proto.platform_id = 1 if str(platform) in ["1", "4"] else int(platform)
        proto.client_version = client_version
        proto.client_version_code = "2019121229"
        
        proto.system_software = device_info.get("system_software", "Android OS 14 / API-34")
        proto.system_hardware = device_info.get("brand", "Handheld")
        proto.device_type = device_info.get("model", "Handheld")
        proto.screen_width = int(device_info.get("screen_width", 1600))
        proto.screen_height = int(device_info.get("screen_height", 900))
        proto.screen_dpi = str(device_info.get("screen_dpi", "300"))
        proto.processor_details = device_info.get("processor_details", "x86-64 SSE3 SSE4.1 SSE4.2 AVX | 2400 | 4")
        proto.memory = int(device_info.get("memory", 5951))
        proto.gpu_renderer = device_info.get("gpu_renderer", "Adreno (TM) 640")
        proto.unique_device_id = device_info.get("unique_device_id", "Google|725030d8-6585-4f55-bcca-a6df7e59935b")
        proto.client_ip = device_info.get("client_ip", "103.145.112.210")
        
        proto.telecom_operator = "Citycell"
        proto.network_operator_a = "Citycell"
        proto.network_type = "WIFI"
        proto.network_type_a = "WIFI"
        proto.cpu_type = 2
        proto.cpu_architecture = "64"
        proto.gpu_version = "OpenGL ES 3.2"
        proto.graphics_api = "OpenGLES2"
        proto.language = "en"
        proto.open_id = open_id
        proto.open_id_type = str(platform)
        proto.login_open_id_type = int(platform)
        proto.access_token = access_token
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
        
        proto.library_path = "/data/app/~~UKDdGuy32C5yOa0KZe_ROA==/com.dts.freefireth-UAKF1gjDbXSGfpA07JDTKQ==/lib/arm64"
        proto.library_token = "b8e0cd5e295eee42f5860d3c86e483dd|/data/app/~~UKDdGuy32C5yOa0KZe_ROA==/com.dts.freefireth-UAKF1gjDbXSGfpA07JDTKQ==/base.apk"
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
    try:
        url = f"{server_url.rstrip('/')}/MajorLogin"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = release_version
        
        print_info(f"[MAJORLOGIN] Sending request to {url}...")
        response = await client.post(url, headers=req_headers, data=data)
        
        if response.status_code != 200:
            print_error(f"[MAJORLOGIN] Server returned status HTTP {response.status_code}: {response.text[:200]}")
            return None
            
        response_content = response.content
        if len(response_content) < 40:
            print_error(f"[MAJORLOGIN] Response content too short ({len(response_content)} bytes): {response_content.hex()}")
            return None

        res_proto = thunderFF_pb2.MajorLoginRes()
        try:
            res_proto.ParseFromString(response_content)
            if res_proto.region and res_proto.token:
                print_success(f"[MAJORLOGIN] Parse successful (Region: {res_proto.region})")
                return res_proto
        except Exception as e:
            print_warning(f"[MAJORLOGIN] Standard Protobuf parse attempt failed: {e}")

        if len(response_content) > 64:
            try:
                res_proto = thunderFF_pb2.MajorLoginRes()
                res_proto.ParseFromString(response_content[64:])
                if res_proto.region and res_proto.token:
                    print_success(f"[MAJORLOGIN] Parse successful with 64-byte offset (Region: {res_proto.region})")
                    return res_proto
            except Exception:
                pass

        for offset in range(min(128, len(response_content))):
            try:
                candidate = thunderFF_pb2.MajorLoginRes()
                candidate.ParseFromString(response_content[offset:])
                if candidate.region and candidate.token:
                    print_success(f"[MAJORLOGIN] Parse successful with offset {offset} (Region: {candidate.region})")
                    return candidate
            except Exception:
                pass

        print_error(f"[MAJORLOGIN] Failed to parse valid region and token from server response (Length: {len(response_content)})")
        return None
    except Exception as e:
        print_error(f"[MAJORLOGIN] HTTP request failed: {e}")
        print_error(traceback.format_exc())
        return None
