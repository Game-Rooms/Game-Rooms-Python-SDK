from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CreateRoomResponse:
    host: str
    code: str
    token: str


@dataclass(frozen=True)
class AppConfig:
    server_url: str


@dataclass(frozen=True)
class RoomInfo:
    app_id: str
    app_tag: str
    audience_enabled: bool
    code: str
    host: str
    audience_host: str
    locked: bool
    full: bool
    moderation_enabled: bool
    password_required: bool
    twitch_locked: bool
    locale: str
    keepalive: bool


@dataclass(frozen=True)
class PresenceEntry:
    id: str
    role: str
    name: str | None = None


@dataclass(frozen=True)
class RoomEntity:
    kind: str
    key: str
    val: Any
    version: int
    locked: bool = False
    acl: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WelcomeState:
    id: int
    secret: str
    reconnect: bool
    device_id: str
    entities: dict[str, RoomEntity]
    here: dict[str, PresenceEntry]
    profile: PresenceEntry | None
    name: str | None = None
