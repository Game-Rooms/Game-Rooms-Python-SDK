from __future__ import annotations

import json
import threading
from dataclasses import replace
from queue import Empty, Queue
from typing import Any, Callable
from urllib import error, parse, request

from websocket import WebSocketBadStatusException, WebSocketConnectionClosedException, WebSocketTimeoutException
from websocket import create_connection

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
from .models import AppConfig, CreateRoomResponse, PresenceEntry, RoomEntity, RoomInfo, WelcomeState

_UNSET = object()
_OBJECT_KINDS = {"object", "text", "number"}


def _normalize_code(code: str) -> str:
    return code.upper()


def _presence_from_wire(data: dict[str, Any]) -> PresenceEntry:
    roles = data.get("roles", {})
    if "player" in roles:
        return PresenceEntry(id=str(data["id"]), role="player", name=roles["player"].get("name"))
    return PresenceEntry(id=str(data["id"]), role="host", name=None)


def _entity_from_wire(kind: str, payload: dict[str, Any], locked: bool = False, acl: Any = None) -> RoomEntity:
    extra = {
        key: value
        for key, value in payload.items()
        if key not in {"key", "val", "version", "acl"}
    }
    return RoomEntity(
        kind=kind,
        key=payload["key"],
        val=payload.get("val"),
        version=payload["version"],
        locked=locked,
        acl=payload.get("acl", acl),
        extra=extra,
    )


class GameRoomsClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        opener: Callable[..., Any] | None = None,
        websocket_factory: Callable[..., Any] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener or request.urlopen
        self._websocket_factory = websocket_factory or self._default_websocket_factory

    def create_room(self, app_id: str = "", app_tag: str = "", max_players: int = 0) -> CreateRoomResponse:
        body = self._request_json(
            "POST",
            "/api/v2/rooms",
            {"appId": app_id, "appTag": app_tag, "maxPlayers": max_players},
        )
        return CreateRoomResponse(host=body["host"], code=body["code"], token=body["token"])

    def get_app_config(self, app_id: str) -> AppConfig:
        body = self._request_json("GET", f"/api/v2/app-configs/{parse.quote(app_id, safe='')}")
        return AppConfig(server_url=body["settings"]["serverUrl"])

    def get_room_info(self, code: str) -> RoomInfo:
        normalized = _normalize_code(code)
        body = self._request_json("GET", f"/api/v2/rooms/{parse.quote(normalized, safe='')}", room_code=normalized)
        return RoomInfo(
            app_id=body["appId"],
            app_tag=body["appTag"],
            audience_enabled=body["audienceEnabled"],
            code=body["code"],
            host=body["host"],
            audience_host=body["audienceHost"],
            locked=body["locked"],
            full=body["full"],
            moderation_enabled=body["moderationEnabled"],
            password_required=body["passwordRequired"],
            twitch_locked=body["twitchLocked"],
            locale=body["locale"],
            keepalive=body["keepalive"],
        )

    def connect_as_host(self, code: str) -> "GameRoomsConnection":
        self.get_room_info(code)
        return self._connect(_normalize_code(code), "host")

    def connect_as_player(self, code: str, name: str | None = None) -> "GameRoomsConnection":
        room = self.get_room_info(code)
        if room.locked:
            raise RoomLockedError(room.code)
        if room.full:
            raise RoomFullError(room.code)
        return self._connect(room.code, "player", name=name)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        room_code: str | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            response = self._opener(req, timeout=self.timeout)
            with response:
                raw = response.read().decode("utf-8")
                status = getattr(response, "status", 200)
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8") if exc.fp else ""
            status = exc.code
            if status == 404 and room_code is not None:
                raise RoomNotFoundError(room_code) from exc
            raise self._translate_http_error(status, raw) from exc

        try:
            decoded = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise GameRoomsHttpError(status, "Invalid JSON response from server.") from exc

        if not decoded.get("ok", False):
            raise GameRoomsHttpError(status, decoded.get("error", "Request failed."))
        return decoded.get("body", {})

    def _connect(self, code: str, role: str, *, name: str | None = None) -> "GameRoomsConnection":
        ws_url = self._build_ws_url(code, role, name=name)
        try:
            websocket = self._websocket_factory(ws_url, timeout=self.timeout)
        except WebSocketBadStatusException as exc:
            raise self._translate_ws_error(code, role, exc) from exc
        connection = GameRoomsConnection(websocket, role=role, timeout=self.timeout)
        connection.await_welcome(self.timeout)
        return connection

    def _build_ws_url(self, code: str, role: str, *, name: str | None = None) -> str:
        parsed = parse.urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = {"role": role}
        if name is not None:
            query["name"] = name
        base_path = parsed.path.rstrip("/")
        path = f"{base_path}/api/v2/rooms/{parse.quote(code, safe='')}/ws"
        return parse.urlunparse((scheme, parsed.netloc, path, "", parse.urlencode(query), ""))

    def _translate_http_error(self, status_code: int, raw_body: str) -> GameRoomsHttpError:
        try:
            decoded = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            decoded = {}
        return GameRoomsHttpError(status_code, decoded.get("error", f"HTTP {status_code}"))

    def _translate_ws_error(self, code: str, role: str, exc: WebSocketBadStatusException) -> GameRoomsHttpError:
        status_code = getattr(exc, "status_code", None) or 400
        if status_code == 404:
            return RoomNotFoundError(code)
        if role == "player" and status_code == 403:
            try:
                room = self.get_room_info(code)
            except GameRoomsError:
                room = None
            if room is not None and room.locked:
                return RoomLockedError(code)
            if room is not None and room.full:
                return RoomFullError(code)
        return WebSocketRejectedError(status_code, f"WebSocket connection rejected with status {status_code}.")

    @staticmethod
    def _default_websocket_factory(url: str, *, timeout: float) -> Any:
        return create_connection(url, timeout=timeout, enable_multithread=True)


class GameRoomsConnection:
    def __init__(self, websocket: Any, *, role: str, timeout: float = 10.0):
        self.role = role
        self.timeout = timeout
        self._websocket = websocket
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._pending: dict[int, Queue[Any]] = {}
        self._pending_lock = threading.Lock()
        self._listeners: dict[str, list[Callable[[Any], None]]] = {}
        self._listeners_lock = threading.Lock()
        self._welcome_event = threading.Event()
        self._closed_event = threading.Event()
        self._close_error: Exception | None = None
        self.welcome: WelcomeState | None = None
        self.entities: dict[str, RoomEntity] = {}
        self.here: dict[str, PresenceEntry] = {}
        self._reader = threading.Thread(target=self._reader_loop, name="game-rooms-reader", daemon=True)
        self._reader.start()

    def __enter__(self) -> "GameRoomsConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def await_welcome(self, timeout: float | None = None) -> WelcomeState:
        if not self._welcome_event.wait(timeout):
            if self._close_error is not None:
                raise self._close_error
            if self._closed_event.is_set():
                raise ConnectionClosedError("Connection closed before client/welcome.")
            self.close()
            raise RequestTimeoutError("Timed out waiting for client/welcome.")
        assert self.welcome is not None
        return self.welcome

    def on(self, event: str, callback: Callable[[Any], None]) -> Callable[[Any], None]:
        with self._listeners_lock:
            self._listeners.setdefault(event, []).append(callback)
        return callback

    def off(self, event: str, callback: Callable[[Any], None]) -> None:
        with self._listeners_lock:
            listeners = self._listeners.get(event, [])
            if callback in listeners:
                listeners.remove(callback)
            if not listeners and event in self._listeners:
                self._listeners.pop(event, None)

    def create_object(self, kind: str, key: str, val: Any, acl: Any = None, **extra: Any) -> None:
        self._require_kind(kind)
        params = {"key": key, "val": val, "acl": acl, **extra}
        self._request(f"{kind}/create", params)
        if self.role == "host":
            self._apply_local_write(kind, params)

    def update_object(self, kind: str, key: str, val: Any, *, acl: Any = _UNSET, **extra: Any) -> None:
        self._require_kind(kind)
        params = {"key": key, "val": val, **extra}
        if acl is not _UNSET:
            params["acl"] = acl
        self._request(f"{kind}/update", params)
        if self.role == "host":
            self._apply_local_write(kind, params)

    def get_object(self, kind: str, key: str, *, timeout_ms: int | None = None, **extra: Any) -> RoomEntity:
        self._require_kind(kind)
        message = self._request(f"{kind}/get", {"key": key, **extra}, timeout=(timeout_ms / 1000.0) if timeout_ms else None)
        payload = message.get("result", message)
        current = self.entities.get(key)
        entity = _entity_from_wire(
            kind,
            payload,
            locked=payload.get("locked", current.locked if current else False),
            acl=current.acl if current else None,
        )
        self.entities[key] = entity
        return entity

    def lock(self, key: str) -> None:
        self._request("lock", {"key": key})
        if self.role == "host" and key in self.entities:
            self.entities[key] = replace(self.entities[key], locked=True)

    def drop(self, key: str) -> None:
        self._request("drop", {"key": key})
        if self.role == "host":
            self.entities.pop(key, None)

    def send(self, params: Any) -> None:
        self._request("client/send", params)

    def lock_room(self) -> None:
        self._request("room/lock", {})

    def exit_room(self) -> None:
        self._request("room/exit", {})

    def get_audience(self) -> int:
        message = self._request("room/get-audience", {})
        payload = message.get("result", message)
        return int(payload.get("connections", 0))

    def close(self) -> None:
        if self._closed_event.is_set():
            return
        self._closed_event.set()
        try:
            self._websocket.close()
        finally:
            self._fail_pending(ConnectionClosedError("Connection closed."))

    def _request(self, opcode: str, params: Any = _UNSET, *, timeout: float | None = None) -> dict[str, Any]:
        seq = self._next_seq()
        queue: Queue[Any] = Queue(maxsize=1)
        with self._pending_lock:
            self._pending[seq] = queue
        try:
            self._websocket.send(json.dumps({"opcode": opcode, "seq": seq, "params": params if params is not _UNSET else {}}))
        except Exception:
            with self._pending_lock:
                self._pending.pop(seq, None)
            raise
        try:
            result = queue.get(timeout=timeout or self.timeout)
        except Empty as exc:
            with self._pending_lock:
                self._pending.pop(seq, None)
            raise RequestTimeoutError(f"Timed out waiting for reply to {opcode}.") from exc
        if isinstance(result, Exception):
            raise result
        return result

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _reader_loop(self) -> None:
        try:
            while not self._closed_event.is_set():
                try:
                    raw = self._websocket.recv()
                except WebSocketTimeoutException:
                    continue
                except WebSocketConnectionClosedException:
                    break
                if raw is None:
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._handle_message(message)
        except Exception as exc:
            self._close_error = exc
            self._emit("error", exc)
        finally:
            self._closed_event.set()
            self._fail_pending(self._close_error or ConnectionClosedError("Connection closed."))
            self._emit("close", self._close_error)

    def _handle_message(self, message: dict[str, Any]) -> None:
        reply_to = message.get("re")
        if reply_to is not None:
            with self._pending_lock:
                queue = self._pending.pop(reply_to, None)
            if queue is not None:
                queue.put(message)
            return

        opcode = message.get("opcode")
        result = message.get("result", {})
        if opcode == "client/welcome":
            self.welcome = self._welcome_from_wire(result)
            self.entities = dict(self.welcome.entities)
            self.here = dict(self.welcome.here)
            self._welcome_event.set()
            return
        if opcode == "client/connected":
            entry = _presence_from_wire(result["profile"])
            self.here[entry.id] = entry
            self._emit(opcode, result)
            return
        if opcode == "client/send":
            self._emit(opcode, result)
            return
        if opcode == "lock":
            key = result.get("key")
            if key in self.entities:
                self.entities[key] = replace(self.entities[key], locked=True)
            self._emit(opcode, result)
            return
        if opcode in _OBJECT_KINDS:
            key = result["key"]
            current = self.entities.get(key)
            self.entities[key] = _entity_from_wire(
                opcode,
                result,
                locked=False,
                acl=result.get("acl", current.acl if current else None) if current else result.get("acl"),
            )
            self._emit(opcode, self.entities[key])
            return
        self._emit(opcode, result)

    def _welcome_from_wire(self, result: dict[str, Any]) -> WelcomeState:
        entities = {}
        for key, entry in result.get("entities", {}).items():
            kind = entry[0]
            payload = entry[1] if len(entry) > 1 else {"key": key, "version": 0}
            state: dict[str, Any] = {}
            acl = None
            for extra in entry[2:]:
                if isinstance(extra, dict):
                    state.update(extra)
                elif acl is None:
                    acl = extra
            entities[key] = _entity_from_wire(kind, payload, locked=state.get("locked", False), acl=acl)
        here = {key: _presence_from_wire(value) for key, value in result.get("here", {}).items()}
        profile = _presence_from_wire(result["profile"]) if result.get("profile") else None
        return WelcomeState(
            id=result["id"],
            secret=result["secret"],
            reconnect=result["reconnect"],
            device_id=result["deviceId"],
            entities=entities,
            here=here,
            profile=profile,
            name=result.get("name"),
        )

    def _apply_local_write(self, kind: str, params: dict[str, Any]) -> None:
        current = self.entities.get(params["key"])
        version = (current.version + 1) if current else 0
        payload = dict(params)
        payload["version"] = version
        self.entities[params["key"]] = _entity_from_wire(kind, payload, locked=False)

    def _emit(self, event: str, payload: Any) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners.get(event, []))
        for callback in listeners:
            callback(payload)

    def _fail_pending(self, exc: Exception) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for queue in pending:
            queue.put(exc)

    def _require_kind(self, kind: str) -> None:
        if kind not in _OBJECT_KINDS:
            raise ValueError(f"Unsupported object kind: {kind}")
