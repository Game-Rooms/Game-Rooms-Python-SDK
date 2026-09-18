import json
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
