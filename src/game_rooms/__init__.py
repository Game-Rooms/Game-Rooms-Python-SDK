__version__ = "1.0.1"

from .client import GameRoomsClient, GameRoomsConnection
from .errors import (
    ConnectionClosedError,
    GameRoomsError,
    GameRoomsHttpError,
    RequestTimeoutError,
    RoomFullError,
    RoomLockedError,
    RoomNotFoundError,
    WebSocketRejectedError,
)
from .models import (
    AppConfig,
    CreateRoomResponse,
    PresenceEntry,
    RoomEntity,
    RoomInfo,
    WelcomeState,
)

__all__ = [
    "__version__",
    "AppConfig",
    "ConnectionClosedError",
    "CreateRoomResponse",
    "GameRoomsClient",
    "GameRoomsConnection",
    "GameRoomsError",
    "GameRoomsHttpError",
    "PresenceEntry",
    "RequestTimeoutError",
    "RoomEntity",
    "RoomFullError",
    "RoomInfo",
    "RoomLockedError",
    "RoomNotFoundError",
    "WebSocketRejectedError",
    "WelcomeState",
]
