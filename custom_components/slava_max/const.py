DOMAIN = "slava_max"

CONF_TOKEN = "token"
CONF_TARGET_TYPE = "target_type"
CONF_TARGET_ID = "target_id"
CONF_POLLING = "polling"
CONF_ALLOWED_USERS = "allowed_users"
CONF_EMERGENCY_CHAT_ID = "emergency_chat_id"

# Multi-user access control
CONF_USERS = "users"
CONF_USER_NAME = "name"
CONF_USER_ENABLED = "enabled"
CONF_USER_PERMISSIONS = "permissions"

PERM_ALL = "*"
PERM_NOTIFICATIONS = "notifications"
PERM_LIGHTS = "lights"
PERM_CLIMATE = "climate"
PERM_DEVICES = "devices"
PERM_WATER = "water"
PERM_VACUUM = "vacuum"
PERM_BRAGA = "braga"
PERM_BRAGA_EMERGENCY = "braga_emergency"
PERM_STATUS = "status"
PERM_INTERCOM = "intercom"
PERM_INTERCOM_OPEN = "intercom_open"
PERM_CAMERAS = "cameras"
PERM_SCENES = "scenes"

PERMISSIONS = [
    PERM_NOTIFICATIONS,
    PERM_LIGHTS,
    PERM_CLIMATE,
    PERM_DEVICES,
    PERM_WATER,
    PERM_VACUUM,
    PERM_BRAGA,
    PERM_BRAGA_EMERGENCY,
    PERM_STATUS,
    PERM_INTERCOM,
    PERM_INTERCOM_OPEN,
    PERM_CAMERAS,
    PERM_SCENES,
]

TARGET_CHAT = "chat_id"
TARGET_USER = "user_id"

EVENT_NAME = "slava_max_event"
EVENT_ACCESS_REQUEST = "slava_max_access_request"

SERVICE_SEND_MESSAGE = "send_message"
SERVICE_BROADCAST = "broadcast"
SERVICE_ANSWER_CALLBACK = "answer_callback"
SERVICE_SEND_IMAGE = "send_image"
SERVICE_BROADCAST_IMAGE = "broadcast_image"
SERVICE_SEND_VIDEO = "send_video"
SERVICE_BROADCAST_VIDEO = "broadcast_video"
SERVICE_SEND_EMERGENCY = "send_emergency"
SERVICE_SEND_EMERGENCY_IMAGE = "send_emergency_image"

# Актуальный домен MAX API с июля 2026.
API_BASE = "https://platform-api2.max.ru"
