"""松下智能加湿器 Home Assistant 集成"""
import logging
import async_timeout
from datetime import timedelta

from homeassistant.components.humidifier import (
    HumidifierEntity,
    HumidifierEntityFeature,
    HumidifierDeviceClass,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_USR_ID, CONF_DEVICE_ID, CONF_TOKEN, CONF_SSID, CONF_DEVICE_TYPE,
    DEVICE_TYPE_HUMIDIFIER, HUMIDIFIER_MODE_MAPPING, HUMIDIFIER_HUMIDITY_MAPPING,
    HUM_MODE_AUTO, HUM_MODE_CONTINUOUS, HUM_MODE_SLEEP, HUM_MODE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# 加湿器API端点 (基于松下云API命名模式推测)
URL_HUM_SET = "https://app.psmartcloud.com/App/HumDevSetStatusInfo"
URL_HUM_GET = "https://app.psmartcloud.com/App/HumDevGetStatusInfo"
# 备用端点 (如果专用端点不可用，使用通用端点)
URL_HUM_SET_AW = "https://app.psmartcloud.com/App/HumDevSetStatusInfoAW"
URL_HUM_GET_AW = "https://app.psmartcloud.com/App/HumDevGetStatusInfoAW"
# 通用设备端点 (最后尝试)
URL_DEV_SET = "https://app.psmartcloud.com/App/DevSetStatusInfo"
URL_DEV_GET = "https://app.psmartcloud.com/App/DevGetStatusInfo"

POLLING_INTERVAL = timedelta(seconds=30)

# 加湿器模式列表
AVAILABLE_MODES = [HUM_MODE_AUTO, HUM_MODE_CONTINUOUS, HUM_MODE_SLEEP, HUM_MODE_INTERVAL]


async def async_setup_entry(hass, entry, async_add_entities):
    """设置加湿器实体"""
    config = entry.data
    
    # 仅为加湿器类型设备创建实体
    if config.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_HUMIDIFIER:
        return
    
    async_add_entities([PanasonicHumidifierEntity(hass, config, entry.title)])


class PanasonicHumidifierEntity(HumidifierEntity):
    """松下智能加湿器实体"""
    
    _attr_device_class = HumidifierDeviceClass.HUMIDIFIER
    _attr_supported_features = HumidifierEntityFeature.MODES
    _attr_available_modes = AVAILABLE_MODES
    _attr_min_humidity = 40
    _attr_max_humidity = 70
    
    def __init__(self, hass, config, name):
        self._hass = hass
        self._usr_id = config[CONF_USR_ID]
        self._device_id = config[CONF_DEVICE_ID]
        self._token = config[CONF_TOKEN]
        self._ssid = config[CONF_SSID]
        self._attr_name = name
        self._attr_unique_id = f"panasonic_{self._device_id}"
        
        # 内部状态
        self._is_on = False
        self._mode = HUM_MODE_AUTO
        self._target_humidity = 50
        self._current_humidity = None
        self._last_params = {}
        
        # 定时器句柄
        self._unsub_polling = None
        
        # API端点选择 (运行时确定)
        self._url_get = None
        self._url_set = None

    @property
    def should_poll(self):
        """关闭 HA 默认慢速轮询"""
        return False

    async def async_added_to_hass(self):
        """实体添加时启动定时轮询"""
        await super().async_added_to_hass()
        # 首次更新时自动探测正确的API端点
        await self._detect_api_endpoints()
        self._unsub_polling = async_track_time_interval(
            self._hass,
            self._async_update_interval_wrapper,
            POLLING_INTERVAL
        )

    async def async_will_remove_from_hass(self):
        """实体移除时销毁定时器"""
        if self._unsub_polling:
            self._unsub_polling()
            self._unsub_polling = None
        await super().async_will_remove_from_hass()

    async def _async_update_interval_wrapper(self, now):
        """定时器回调"""
        await self.async_update()
        self.async_write_ha_state()

    @property
    def is_on(self):
        return self._is_on

    @property
    def mode(self):
        return self._mode

    @property
    def target_humidity(self):
        return self._target_humidity

    @property
    def current_humidity(self):
        return self._current_humidity

    async def _detect_api_endpoints(self):
        """自动探测可用的API端点"""
        headers = self._get_headers()
        payload = {
            "id": 100,
            "usrId": self._usr_id,
            "deviceId": self._device_id,
            "token": self._token
        }
        
        # 尝试不同的API端点 (按优先级排序)
        # 松下云可能对所有设备使用统一的AC端点，也可能有专用的加湿器端点
        endpoints_to_try = [
            # 加湿器专用端点
            (URL_HUM_GET, URL_HUM_SET),
            (URL_HUM_GET_AW, URL_HUM_SET_AW),
            # 空调端点 (松下云可能使用统一接口)
            ("https://app.psmartcloud.com/App/ACDevGetStatusInfoAW", 
             "https://app.psmartcloud.com/App/ACDevSetStatusInfoAW"),
            # 通用设备端点
            (URL_DEV_GET, URL_DEV_SET),
        ]
        
        session = async_get_clientsession(self._hass)
        
        for get_url, set_url in endpoints_to_try:
            try:
                async with async_timeout.timeout(5):
                    response = await session.post(get_url, json=payload, headers=headers, ssl=False)
                    json_data = await response.json()
                    
                    error_code = json_data.get('errorCode')
                    # 检查是否为有效响应
                    if error_code not in ['3003', '3004', '404', '500', None] or 'results' in json_data:
                        if 'results' in json_data:
                            self._url_get = get_url
                            self._url_set = set_url
                            _LOGGER.info(f"加湿器API端点探测成功: GET={get_url}")
                            
                            # 解析初始状态
                            self._update_local_state(json_data['results'])
                            return
            except Exception as e:
                _LOGGER.debug(f"尝试端点 {get_url} 失败: {e}")
                continue
        
        # 所有端点都失败，使用最常见的空调API作为默认
        _LOGGER.warning("所有API端点探测失败，使用空调API作为默认")
        self._url_get = "https://app.psmartcloud.com/App/ACDevGetStatusInfoAW"
        self._url_set = "https://app.psmartcloud.com/App/ACDevSetStatusInfoAW"

    async def async_update(self):
        """轮询更新状态"""
        await self._fetch_status(update_internal_state=True)

    async def _fetch_status(self, update_internal_state=True):
        """获取设备当前状态"""
        if not self._url_get:
            await self._detect_api_endpoints()
            return None
            
        headers = self._get_headers()
        payload = {
            "id": 100,
            "usrId": self._usr_id,
            "deviceId": self._device_id,
            "token": self._token
        }
        
        try:
            session = async_get_clientsession(self._hass)
            async with async_timeout.timeout(5):
                response = await session.post(self._url_get, json=payload, headers=headers, ssl=False)
                json_data = await response.json()
                
                if json_data.get('errorCode') in ['3003', '3004']:
                    _LOGGER.error("SSID expired for humidifier.")
                    return None
                
                if 'results' in json_data:
                    res = json_data['results']
                    self._last_params = res
                    
                    if update_internal_state:
                        self._update_local_state(res)
                    
                    return res
        except Exception as e:
            _LOGGER.debug(f"获取加湿器状态失败: {e}")
            return None
        return None

    def _update_local_state(self, res):
        """更新HA实体状态 (兼容空调API返回格式)"""
        if not res:
            return
            
        # 运行状态
        self._is_on = res.get('runStatus', 0) == 1
        
        # 运行模式
        run_mode = res.get('runMode', 0)
        mode_found = False
        for mode_name, mode_val in HUMIDIFIER_MODE_MAPPING.items():
            if mode_val == run_mode:
                self._mode = mode_name
                mode_found = True
                break
        if not mode_found:
            self._mode = HUM_MODE_AUTO
        
        # 目标湿度 - 支持两种格式：档位值(0-3)或直接湿度值(40-70)
        humidity_val = res.get('setHumidity', 1)
        if humidity_val in HUMIDIFIER_HUMIDITY_MAPPING.values():
            # 档位值格式
            for humidity, level in HUMIDIFIER_HUMIDITY_MAPPING.items():
                if level == humidity_val:
                    self._target_humidity = humidity
                    break
        elif 40 <= humidity_val <= 70:
            # 直接湿度值格式
            self._target_humidity = humidity_val
        
        # 当前湿度 (API可能使用不同的字段名)
        current = (
            res.get('currentHumidity') or 
            res.get('insideHumidity') or 
            res.get('humidity')
        )
        if current is not None:
            try:
                self._current_humidity = int(current)
            except (ValueError, TypeError):
                pass

    async def async_turn_on(self, **kwargs):
        """开启加湿器"""
        await self._send_command({"runStatus": 1})

    async def async_turn_off(self, **kwargs):
        """关闭加湿器"""
        await self._send_command({"runStatus": 0})

    async def async_set_humidity(self, humidity: int):
        """设置目标湿度"""
        # 将湿度值映射到API档位
        target_level = HUMIDIFIER_HUMIDITY_MAPPING.get(humidity)
        if target_level is None:
            # 找最接近的档位
            closest = min(HUMIDIFIER_HUMIDITY_MAPPING.keys(), key=lambda x: abs(x - humidity))
            target_level = HUMIDIFIER_HUMIDITY_MAPPING[closest]
        
        await self._send_command({"setHumidity": target_level})

    async def async_set_mode(self, mode: str):
        """设置运行模式"""
        mode_val = HUMIDIFIER_MODE_MAPPING.get(mode, 0)
        await self._send_command({"runMode": mode_val})

    async def _send_command(self, changes: dict):
        """发送控制命令 (Read-Modify-Write)"""
        if not self._url_set:
            _LOGGER.error("加湿器API端点未初始化")
            return
        
        # 1. Read
        latest_params = await self._fetch_status(update_internal_state=False)
        
        if latest_params:
            current_params = latest_params.copy()
        else:
            _LOGGER.warning("无法获取最新状态，使用缓存参数")
            current_params = self._last_params.copy()
        
        # 2. Modify
        current_params.update(changes)
        
        # 3. Filter - 加湿器参数
        safe_keys = [
            "runStatus", "runMode", "setHumidity", "windSet",
            "muteMode", "nanoe", "nanoeG", "childLock",
            "waterLevel", "filterReset", "buzzer", "lightMode",
            "timerOn", "timerOff", "currentHumidity", "insideHumidity",
        ]
        params = {k: v for k, v in current_params.items() if k in safe_keys}
        
        # 4. Write
        headers = self._get_headers()
        try:
            session = async_get_clientsession(self._hass)
            async with async_timeout.timeout(10):
                await session.post(self._url_set, json={
                    "id": 200,
                    "usrId": self._usr_id,
                    "deviceId": self._device_id,
                    "token": self._token,
                    "params": params
                }, headers=headers, ssl=False)
                
                # 5. 更新本地状态 (乐观更新)
                self._update_local_state(current_params)
                self._last_params = current_params
                
                # 6. 强制刷新HA界面
                self.async_write_ha_state()
                
        except Exception as e:
            _LOGGER.error(f"加湿器控制命令发送失败: {e}")

    def _get_headers(self):
        return {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X)',
            'xtoken': f'SSID={self._ssid}',
            'DNT': '1',
            'Origin': 'https://app.psmartcloud.com',
            'X-Requested-With': 'XMLHttpRequest'
        }
