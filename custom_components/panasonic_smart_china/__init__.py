import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_DEVICE_TYPE, DEVICE_TYPE_HUMIDIFIER, DEVICE_TYPE_AC

_LOGGER = logging.getLogger(__name__)

# 所有支持的平台
ALL_PLATFORMS = ["climate", "humidifier"]


async def async_setup(hass: HomeAssistant, config: dict):
    # 初始化全局数据存储，用于缓存 Session
    hass.data.setdefault(DOMAIN, {
        "session": None  # 结构: {'usrId': ..., 'ssid': ..., 'devices': ...}
    })
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    # 确保存储存在
    hass.data.setdefault(DOMAIN, {"session": None})
    
    # 根据设备类型选择加载的平台
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_AC)
    
    if device_type == DEVICE_TYPE_HUMIDIFIER:
        platforms = ["humidifier"]
    else:
        platforms = ["climate"]
    
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    # 根据设备类型卸载对应平台
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_AC)
    
    if device_type == DEVICE_TYPE_HUMIDIFIER:
        platforms = ["humidifier"]
    else:
        platforms = ["climate"]
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    return unload_ok