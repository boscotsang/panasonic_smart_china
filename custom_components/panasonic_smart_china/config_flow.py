import logging
import hashlib
import voluptuous as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

from .const import (
    DOMAIN, CONF_USR_ID, CONF_DEVICE_ID, CONF_TOKEN, 
    CONF_SSID, CONF_SENSOR_ID, CONF_CONTROLLER_MODEL, CONF_DEVICE_TYPE,
    SUPPORTED_CONTROLLERS, DEVICE_TYPE_AC, DEVICE_TYPE_HUMIDIFIER
)

_LOGGER = logging.getLogger(__name__)

URL_LOGIN = "https://app.psmartcloud.com/App/UsrLogin"
URL_GET_DEV = "https://app.psmartcloud.com/App/UsrGetBindDevInfo"
URL_GET_TOKEN = "https://app.psmartcloud.com/App/UsrGetToken"

class PanasonicConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._login_data = {}
        self._devices = {}
        self._temp_login_info = {}

    async def async_step_user(self, user_input=None):
        """步骤1: 检查缓存 Session 或 登录"""
        errors = {}

        # 1. 检查全局缓存中是否有现成的 Session
        # 修复点：使用 .get() 安全访问，防止 KeyError
        domain_data = self.hass.data.get(DOMAIN, {})
        cached_session = domain_data.get("session")

        if cached_session:
            _LOGGER.info("Found cached session, verifying validity...")
            
            # 验证 Session 是否有效
            valid_devices = await self._get_devices_with_ssid(
                cached_session[CONF_USR_ID], cached_session[CONF_SSID]
            )
            
            if valid_devices:
                _LOGGER.info("Session valid. Skipping login.")
                self._login_data = {
                    CONF_USR_ID: cached_session[CONF_USR_ID],
                    CONF_SSID: cached_session[CONF_SSID]
                }
                self._devices = valid_devices
                return await self.async_step_device()
            else:
                _LOGGER.warning("Cached session expired.")
                # 清除无效 Session (如果存在)
                if DOMAIN in self.hass.data:
                    self.hass.data[DOMAIN]["session"] = None

        # 2. 处理用户登录输入
        if user_input is not None:
            try:
                usr_id, ssid, devices = await self._authenticate_full_flow(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
                
                if not devices:
                    return self.async_abort(reason="no_devices_found")

                # 更新实例变量
                self._login_data = {CONF_USR_ID: usr_id, CONF_SSID: ssid}
                self._devices = devices
                
                # *** 关键修复：确保 DOMAIN 字典存在 ***
                self.hass.data.setdefault(DOMAIN, {})
                
                # 更新全局缓存
                self.hass.data[DOMAIN]["session"] = {
                    CONF_USR_ID: usr_id,
                    CONF_SSID: ssid,
                    "devices": devices,
                    "familyId": self._temp_login_info.get('familyId'),
                    "realFamilyId": self._temp_login_info.get('realFamilyId')
                }

                return await self.async_step_device()

            except Exception as e:
                _LOGGER.error("Login failed: %s", e)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    def _detect_device_type(self, device_id: str, device_info: dict) -> str:
        """检测设备类型：空调或加湿器
        
        设备ID分隔符:
        - _0900_: 空调
        - _0840_: 加湿器
        """
        device_name = device_info.get("deviceName", "").lower()
        device_id_upper = device_id.upper()
        
        # 通过设备ID分隔符识别 (最可靠)
        if "_0840_" in device_id_upper:
            return DEVICE_TYPE_HUMIDIFIER
        if "_0900_" in device_id_upper:
            return DEVICE_TYPE_AC
        
        # 通过设备名称识别加湿器
        humidifier_keywords = ["加湿", "humidifier", "hum", "湿度", "aircle"]
        for keyword in humidifier_keywords:
            if keyword in device_name:
                return DEVICE_TYPE_HUMIDIFIER
        
        # 通过设备ID前缀识别(松下加湿器可能使用FV或HUM前缀)
        device_id_lower = device_id.lower()
        humidifier_prefixes = ["fv", "hum", "fvrzm", "fvrjm"]
        for prefix in humidifier_prefixes:
            if device_id_lower.startswith(prefix):
                return DEVICE_TYPE_HUMIDIFIER
        
        # 默认认为是空调
        return DEVICE_TYPE_AC

    async def async_step_device(self, user_input=None):
        """步骤2: 选择设备"""
        errors = {}
        
        # 获取已添加的设备，防止重复
        existing_ids = self._async_current_ids()
        
        # 构建可选设备列表 (排除已存在的)，并检测设备类型
        available_devices = {}
        device_types = {}
        for did, info in self._devices.items():
            # 注意：这里的 unique_id 必须与 climate.py/humidifier.py 中保持一致
            if f"panasonic_{did}" not in existing_ids:
                detected_type = self._detect_device_type(did, info)
                type_label = "加湿器" if detected_type == DEVICE_TYPE_HUMIDIFIER else "空调"
                available_devices[did] = f"{info['deviceName']} [{type_label}] ({did})"
                device_types[did] = detected_type

        if not available_devices:
            return self.async_abort(reason="all_devices_configured")

        if user_input is not None:
            selected_dev_id = user_input[CONF_DEVICE_ID]
            dev_info = self._devices.get(selected_dev_id)
            dev_name = dev_info.get("deviceName", "Panasonic Device")
            
            # 用户可手动指定设备类型，或使用自动检测的结果
            selected_type = user_input.get(CONF_DEVICE_TYPE, device_types.get(selected_dev_id, DEVICE_TYPE_AC))
            
            token = self._generate_token(selected_dev_id, selected_type)
            if not token:
                errors["base"] = "token_generation_failed"
            else:
                # 根据设备类型构建配置数据
                data = {
                    CONF_USR_ID: self._login_data[CONF_USR_ID],
                    CONF_SSID: self._login_data[CONF_SSID],
                    CONF_DEVICE_ID: selected_dev_id,
                    CONF_TOKEN: token,
                    CONF_DEVICE_TYPE: selected_type,
                }
                
                if selected_type == DEVICE_TYPE_AC:
                    # 空调需要额外配置
                    data[CONF_SENSOR_ID] = user_input.get(CONF_SENSOR_ID, "")
                    data[CONF_CONTROLLER_MODEL] = user_input.get(CONF_CONTROLLER_MODEL, "CZ-RD501DW2")
                
                return self.async_create_entry(title=dev_name, data=data)

        # 构建控制器列表
        controller_options = {k: v["name"] for k, v in SUPPORTED_CONTROLLERS.items()}
        
        # 设备类型选项
        device_type_options = {
            DEVICE_TYPE_AC: "空调 (Air Conditioner)",
            DEVICE_TYPE_HUMIDIFIER: "加湿器 (Humidifier)"
        }

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_ID): vol.In(available_devices),
                vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_AC): vol.In(device_type_options),
                vol.Optional(CONF_CONTROLLER_MODEL, default="CZ-RD501DW2"): vol.In(controller_options),
                vol.Optional(CONF_SENSOR_ID): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
            }),
            errors=errors,
        )

    async def _get_devices_with_ssid(self, usr_id, ssid):
        """仅使用 SSID 尝试获取设备列表 (用于验证 Session)"""
        headers = {'User-Agent': 'SmartApp', 'Content-Type': 'application/json', 'Cookie': f"SSID={ssid}"}
        
        # 安全读取缓存
        domain_data = self.hass.data.get(DOMAIN, {})
        session_cache = domain_data.get("session")
        
        if not session_cache or 'familyId' not in session_cache:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(URL_GET_DEV, json={
                    "id": 3, "uiVersion": 4.0,
                    "params": {
                        "realFamilyId": session_cache['realFamilyId'], 
                        "familyId": session_cache['familyId'], 
                        "usrId": usr_id
                    }
                }, headers=headers, ssl=False) as resp:
                    if resp.status != 200: return None
                    dev_res = await resp.json()
                    if 'results' not in dev_res: return None
                    
                    devices = {}
                    for dev in dev_res['results']['devList']:
                        devices[dev['deviceId']] = dev['params']
                    return devices
        except:
            return None

    async def _authenticate_full_flow(self, username, password):
        """完整的登录流程"""
        headers = {'User-Agent': 'SmartApp', 'Content-Type': 'application/json'}
        async with aiohttp.ClientSession() as session:
            # 1. GetToken
            async with session.post(URL_GET_TOKEN, json={
                "id": 1, "uiVersion": 4.0, "params": {"usrId": username}
            }, headers=headers, ssl=False) as resp:
                data = await resp.json()
                if 'results' not in data: raise Exception("GetToken Failed")
                token_start = data['results']['token']
            
            # 2. Calc Password
            pwd_md5 = hashlib.md5(password.encode()).hexdigest().upper()
            inter_md5 = hashlib.md5((pwd_md5 + username).encode()).hexdigest().upper()
            final_token = hashlib.md5((inter_md5 + token_start).encode()).hexdigest().upper()
            
            # 3. Login
            async with session.post(URL_LOGIN, json={
                "id": 2, "uiVersion": 4.0, 
                "params": {"telId": "00:00:00:00:00:00", "checkFailCount": 0, "usrId": username, "pwd": final_token}
            }, headers=headers, ssl=False) as resp:
                login_res = await resp.json()
                if "results" not in login_res: raise Exception("Login Failed")
                
                res = login_res['results']
                real_usr_id = res['usrId']
                ssid = res['ssId']
                
                # 临时保存 family 数据
                self._temp_login_info = {
                    'realFamilyId': res['realFamilyId'],
                    'familyId': res['familyId']
                }

            # 4. Get Devices
            headers['Cookie'] = f"SSID={ssid}"
            async with session.post(URL_GET_DEV, json={
                "id": 3, "uiVersion": 4.0,
                "params": {"realFamilyId": res['realFamilyId'], "familyId": res['familyId'], "usrId": real_usr_id}
            }, headers=headers, ssl=False) as resp:
                dev_res = await resp.json()
                devices = {}
                if 'results' in dev_res and 'devList' in dev_res['results']:
                    for dev in dev_res['results']['devList']:
                        devices[dev['deviceId']] = dev['params']
                return real_usr_id, ssid, devices

    def _generate_token(self, device_id: str, device_type: str = DEVICE_TYPE_AC) -> str | None:
        """生成设备token, 支持空调和加湿器
        
        设备ID格式: XXXXXXXXXXXX_YYYY_ZZZZZZ
        - 空调: _0900_
        - 加湿器: _0840_
        - 其他可能: _0A00_, _0B00_ 等
        
        Token算法: SHA512(SHA512(后6位+分隔符+前6位) + '_' + 设备后缀)
        """
        try:
            did = device_id.upper()
            
            # 支持的分隔符列表 (按设备类型)
            separators = ['_0900_', '_0840_', '_0A00_', '_0B00_', '_0C00_']
            
            for sep in separators:
                if sep in did:
                    parts = did.split(sep)
                    if len(parts) != 2:
                        continue
                    
                    prefix = parts[0]  # 如: 9C1221E32995
                    suffix = parts[1]  # 如: Aircle-17-03
                    
                    # Token算法: 后6位 + 分隔符 + 前6位
                    if len(prefix) >= 12:
                        stoken = prefix[6:12] + sep + prefix[:6]
                    else:
                        stoken = prefix + sep + prefix
                    
                    inner = hashlib.sha512(stoken.encode()).hexdigest()
                    return hashlib.sha512((inner + '_' + suffix).encode()).hexdigest()
            
            # 尝试通用分隔符 '_'
            if '_' in did:
                parts = did.split('_')
                if len(parts) >= 2:
                    # 重组为标准格式尝试
                    combined = '_'.join(parts)
                    return hashlib.sha512(combined.encode()).hexdigest()
            
            # 无法识别格式，使用简单hash
            return hashlib.sha512(did.encode()).hexdigest()
            
        except Exception:
            return None