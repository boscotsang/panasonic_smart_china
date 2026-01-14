from homeassistant.components.climate.const import (
    HVACMode,
    FAN_AUTO,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
)

DOMAIN = "panasonic_smart_china"

CONF_USR_ID = "usrId"
CONF_DEVICE_ID = "deviceId"
CONF_TOKEN = "token"
CONF_SSID = "SSID"
CONF_SENSOR_ID = "sensor_entity_id"
CONF_CONTROLLER_MODEL = "controller_model"
CONF_DEVICE_TYPE = "device_type"

# 设备类型常量
DEVICE_TYPE_AC = "ac"
DEVICE_TYPE_HUMIDIFIER = "humidifier"

# 自定义风速常量
FAN_MIN = "Min"    # 最低
FAN_MAX = "Max"    # 最高
FAN_MUTE = "Quiet" # 静音

# 加湿器模式常量
HUM_MODE_AUTO = "auto"       # 自动
HUM_MODE_CONTINUOUS = "continuous"  # 连续
HUM_MODE_SLEEP = "sleep"     # 睡眠
HUM_MODE_INTERVAL = "interval"  # 间歇

# 加湿器湿度档位
HUM_HUMIDITY_40 = 40
HUM_HUMIDITY_50 = 50
HUM_HUMIDITY_60 = 60
HUM_HUMIDITY_70 = 70

# === 控制器配置数据库 ===
SUPPORTED_CONTROLLERS = {
    "CZ-RD501DW2": {
        "name": "松下风管机线控器 CZ-RD501DW2",
        "temp_scale": 2,
        "hvac_mapping": {
            HVACMode.COOL: 3,
            HVACMode.HEAT: 4,
            HVACMode.DRY: 2,
            HVACMode.AUTO: 0,
        },
        # 基础风速映射 (windSet 数值)
        "fan_mapping": {
            FAN_AUTO: 10,   # 自动
            FAN_MIN: 3,     # 最低
            FAN_LOW: 4,     # 低
            FAN_MEDIUM: 5,  # 中
            FAN_HIGH: 6,    # 高
            FAN_MAX: 7,     # 最高
        },
        # 特殊模式覆盖 (仅定义静音即可，其他走通用逻辑)
        "fan_payload_overrides": {
            FAN_MUTE: {"windSet": 10, "muteMode": 1}
        }
    }
}

# === 加湿器模式映射 ===
HUMIDIFIER_MODE_MAPPING = {
    HUM_MODE_AUTO: 0,       # 自动模式
    HUM_MODE_CONTINUOUS: 1, # 连续模式
    HUM_MODE_SLEEP: 2,      # 睡眠模式  
    HUM_MODE_INTERVAL: 3,   # 间歇模式
}

# 加湿器湿度设置映射
HUMIDIFIER_HUMIDITY_MAPPING = {
    40: 0,
    50: 1,
    60: 2,
    70: 3,
}