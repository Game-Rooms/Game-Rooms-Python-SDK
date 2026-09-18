import json
import time
import unittest
from queue import Empty, Queue
from unittest.mock import Mock
from urllib.error import HTTPError

from game_rooms import GameRoomsClient, RequestTimeoutError, RoomFullError, RoomLockedError, RoomNotFoundError


class FakeHttpResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeWebSocket:
    def __init__(self, messages, send_hook=None):
        self.messages = Queue()
        for message in messages:
            self.messages.put(message)
        self.sent = []
        self.closed = False
        self.send_hook = send_hook

    def send(self, payload):
        decoded = json.loads(payload)
        self.sent.append(decoded)
        if self.send_hook is not None:
            self.send_hook(decoded, self)

    def recv(self):
        while True:
            try:
                return self.messages.get(timeout=0.1)
            except Empty:
                if self.closed:
                    return None

    def close(self):
        self.closed = True

    def push(self, payload):
        self.messages.put(payload)


class ErrorWebSocket(FakeWebSocket):
    def __init__(self, messages):
        super().__init__(messages)
        self.raise_error = False

    def send(self, payload):
        super().send(payload)
        self.raise_error = True

    def recv(self):
        try:
            return self.messages.get(timeout=0.1)
        except Empty:
            if self.raise_error:
                raise RuntimeError("boom")
            if self.closed:
                return None
            time.sleep(0.01)
            return self.recv()


class SilentWebSocket(FakeWebSocket):
    def __init__(self):
        super().__init__([])

    def recv(self):
        while not self.closed:
            time.sleep(0.01)
        return None


class GameRoomsClientTests(unittest.TestCase):
    def test_create_room_posts_expected_payload(self):
        opener = Mock(return_value=FakeHttpResponse({"ok": True, "body": {"host": "example.com", "code": "WXYZ", "token": "0" * 24}}))
        client = GameRoomsClient("https://example.com", opener=opener)

        room = client.create_room(app_id="my-game", app_tag="v1.0", max_players=8)

        self.assertEqual(room.code, "WXYZ")
        request = opener.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"appId": "my-game", "appTag": "v1.0", "maxPlayers": 8})

    def test_get_room_info_raises_not_found(self):
        def opener(req, timeout):
            raise HTTPError(req.full_url, 404, "Not Found", {}, None)

        client = GameRoomsClient("https://example.com", opener=opener)

        with self.assertRaises(RoomNotFoundError):
            client.get_room_info("wxyz")

    def test_connect_as_player_surfaces_locked_and_full(self):
        locked_client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body(locked=True, full=False)})),
        )
        full_client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body(locked=False, full=True)})),
        )

        with self.assertRaises(RoomLockedError):
            locked_client.connect_as_player("WXYZ")
        with self.assertRaises(RoomFullError):
            full_client.connect_as_player("WXYZ")

    def test_connection_tracks_welcome_events_and_get_replies(self):
        def send_hook(message, socket):
            if message["opcode"] == "number/get":
                socket.push(json.dumps({"pc": 4, "opcode": "number", "result": {"key": "score", "val": 12, "version": 1}, "re": message["seq"]}))
                socket.push(json.dumps({"pc": 5, "opcode": "client/connected", "result": {"profile": {"id": 3, "roles": {"player": {"name": "Eve"}}}}}))

        websocket = FakeWebSocket(
            [
                json.dumps(
                    {
                        "pc": 3,
                        "opcode": "client/welcome",
                        "result": {
                            "id": 2,
                            "name": "Bob",
                            "secret": "secret",
                            "reconnect": False,
                            "deviceId": "device",
                            "entities": {
                                "score": ["number", {"key": "score", "val": 10, "version": 0}, {"locked": False}]
                            },
                            "here": {
                                "1": {"id": "1", "roles": {"host": {}}},
                                "2": {"id": "2", "roles": {"player": {"name": "Bob"}}},
                            },
                            "profile": {"id": 2, "roles": {"player": {"name": "Bob"}}},
                        },
                    }
                )
            ]
            ,
            send_hook=send_hook,
        )
        client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=lambda *args, **kwargs: websocket,
        )

        connection = client.connect_as_player("WXYZ", name="Bob")
        connected = []
        connection.on("client/connected", connected.append)
        entity = connection.get_object("number", "score", timeout_ms=1000)

        self.assertEqual(connection.welcome.name, "Bob")
        self.assertEqual(connection.entities["score"].val, 12)
        self.assertEqual(entity.version, 1)
        self.assertEqual(websocket.sent[0]["opcode"], "number/get")
        self.assertEqual(connected[0]["profile"]["roles"]["player"]["name"], "Eve")

    def test_event_listeners_can_be_removed(self):
        websocket = FakeWebSocket(
            [
                json.dumps(
                    {
                        "pc": 3,
                        "opcode": "client/welcome",
                        "result": {
                            "id": 1,
                            "secret": "secret",
                            "reconnect": False,
                            "deviceId": "device",
                            "entities": {},
                            "here": {"1": {"id": "1", "roles": {"host": {}}}},
                            "profile": None,
                        },
                    }
                )
            ]
        )
        client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=lambda *args, **kwargs: websocket,
        )

        connection = client.connect_as_host("WXYZ")
        received = []

        def handler(payload):
            received.append(payload)

        connection.on("client/send", handler)
        connection.off("client/send", handler)
        websocket.push(json.dumps({"pc": 4, "opcode": "client/send", "result": {"ignored": True}}))
        time.sleep(0.05)

        self.assertEqual(received, [])

    def test_get_object_times_out_when_server_never_replies(self):
        websocket = FakeWebSocket(
            [
                json.dumps(
                    {
                        "pc": 3,
                        "opcode": "client/welcome",
                        "result": {
                            "id": 1,
                            "secret": "secret",
                            "reconnect": False,
                            "deviceId": "device",
                            "entities": {},
                            "here": {"1": {"id": "1", "roles": {"host": {}}}},
                            "profile": None,
                        },
                    }
                )
            ]
        )
        client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=lambda *args, **kwargs: websocket,
        )

        connection = client.connect_as_host("WXYZ")

        with self.assertRaises(RequestTimeoutError):
            connection.get_object("number", "missing", timeout_ms=10)

    def test_connect_times_out_when_welcome_never_arrives(self):
        websocket = SilentWebSocket()
        client = GameRoomsClient(
            "https://example.com",
            timeout=0.05,
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=lambda *args, **kwargs: websocket,
        )

        with self.assertRaises(RequestTimeoutError):
            client.connect_as_host("WXYZ")
        self.assertTrue(websocket.closed)

    def test_get_audience_uses_documented_opcode(self):
        def send_hook(message, socket):
            if message["opcode"] == "room/get-audience":
                socket.push(json.dumps({"pc": 4, "opcode": "room/get-audience", "result": {"connections": 0}, "re": message["seq"]}))

        websocket = FakeWebSocket(
            [
                json.dumps(
                    {
                        "pc": 3,
                        "opcode": "client/welcome",
                        "result": {
                            "id": 1,
                            "secret": "secret",
                            "reconnect": False,
                            "deviceId": "device",
                            "entities": {},
                            "here": {"1": {"id": "1", "roles": {"host": {}}}},
                            "profile": None,
                        },
                    }
                )
            ],
            send_hook=send_hook,
        )
        client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=lambda *args, **kwargs: websocket,
        )

        connection = client.connect_as_host("WXYZ")

        self.assertEqual(connection.get_audience(), 0)
        self.assertEqual(websocket.sent[0]["opcode"], "room/get-audience")

    def test_get_audience_accepts_direct_payload_shape(self):
        websocket = FakeWebSocket(
            [
                json.dumps(
                    {
                        "pc": 3,
                        "opcode": "client/welcome",
                        "result": {
                            "id": 1,
                            "secret": "secret",
                            "reconnect": False,
                            "deviceId": "device",
                            "entities": {},
                            "here": {"1": {"id": "1", "roles": {"host": {}}}},
                            "profile": None,
                        },
                    }
                )
            ]
        )
        client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=lambda *args, **kwargs: websocket,
        )

        connection = client.connect_as_host("WXYZ")
        connection._request = Mock(return_value={"connections": 0})

        self.assertEqual(connection.get_audience(), 0)

    def test_connect_uses_base_path_prefix_for_websocket_url(self):
        websocket = FakeWebSocket(
            [
                json.dumps(
                    {
                        "pc": 3,
                        "opcode": "client/welcome",
                        "result": {
                            "id": 1,
                            "secret": "secret",
                            "reconnect": False,
                            "deviceId": "device",
                            "entities": {},
                            "here": {"1": {"id": "1", "roles": {"host": {}}}},
                            "profile": None,
                        },
                    }
                )
            ]
        )
        websocket_factory = Mock(return_value=websocket)
        client = GameRoomsClient(
            "https://example.com/worker",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=websocket_factory,
        )

        client.connect_as_host("WXYZ")

        self.assertEqual(
            websocket_factory.call_args.args[0],
            "wss://example.com/worker/api/v2/rooms/WXYZ/ws?role=host",
        )

    def test_reader_error_emits_events_and_fails_pending_requests(self):
        websocket = ErrorWebSocket(
            [
                json.dumps(
                    {
                        "pc": 3,
                        "opcode": "client/welcome",
                        "result": {
                            "id": 1,
                            "secret": "secret",
                            "reconnect": False,
                            "deviceId": "device",
                            "entities": {},
                            "here": {"1": {"id": "1", "roles": {"host": {}}}},
                            "profile": None,
                        },
                    }
                )
            ]
        )
        client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=lambda *args, **kwargs: websocket,
        )

        connection = client.connect_as_host("WXYZ")
        errors = []
        closes = []
        connection.on("error", errors.append)
        connection.on("close", closes.append)

        with self.assertRaises(RuntimeError):
            connection.get_object("number", "score", timeout_ms=1000)

        self.assertEqual(str(errors[0]), "boom")
        self.assertEqual(str(closes[0]), "boom")

    def test_client_send_event_is_emitted(self):
        websocket = FakeWebSocket(
            [
                json.dumps(
                    {
                        "pc": 3,
                        "opcode": "client/welcome",
                        "result": {
                            "id": 1,
                            "secret": "secret",
                            "reconnect": False,
                            "deviceId": "device",
                            "entities": {},
                            "here": {"1": {"id": "1", "roles": {"host": {}}}},
                            "profile": None,
                        },
                    }
                )
            ]
        )
        client = GameRoomsClient(
            "https://example.com",
            opener=Mock(return_value=FakeHttpResponse({"ok": True, "body": self._room_info_body()})),
            websocket_factory=lambda *args, **kwargs: websocket,
        )

        connection = client.connect_as_host("WXYZ")
        received = []
        connection.on("client/send", received.append)
        websocket.push(json.dumps({"pc": 4, "opcode": "client/send", "result": {"hello": "world"}}))
        time.sleep(0.05)

        self.assertEqual(received, [{"hello": "world"}])

    @staticmethod
    def _room_info_body(locked=False, full=False):
        return {
            "appId": "my-game",
            "appTag": "v1.0",
            "audienceEnabled": False,
            "code": "WXYZ",
            "host": "example.com",
            "audienceHost": "example.com",
            "locked": locked,
            "full": full,
            "moderationEnabled": False,
            "passwordRequired": False,
            "twitchLocked": False,
            "locale": "en",
            "keepalive": False,
        }


if __name__ == "__main__":
    unittest.main()
