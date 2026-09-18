# Game Rooms Python SDK

Python SDK for the Game Rooms Worker 1.0.0 protocol.

## Installation

```bash
pip install game-rooms
```

For local development from this repository:

```bash
pip install -e .
```

## Usage

```python
from game_rooms import GameRoomsClient

client = GameRoomsClient("https://your-worker.example.workers.dev")

room = client.create_room(app_id="my-game", app_tag="v1.0", max_players=8)

with client.connect_as_host(room.code) as host:
    host.create_object("number", "score", 0)
    host.lock_room()
```

Joining as a player:

```python
from game_rooms import GameRoomsClient, RequestTimeoutError

client = GameRoomsClient("https://your-worker.example.workers.dev")

try:
    with client.connect_as_player("WXYZ", name="Bob") as player:
        score = player.get_object("number", "score", timeout_ms=1000)
        print(score.val)
        player.send({"action": "ready"})
except RequestTimeoutError:
    print("The object was not readable or did not exist.")
```

## Surface area

The SDK implements:

- `create_room`, `get_app_config`, and `get_room_info`
- `connect_as_host` and `connect_as_player`
- request/reply sequencing for WebSocket messages
- object helpers for `create`, `update`, `get`, `lock`, and `drop`
- room helpers for `lock_room`, `exit_room`, and `get_audience`
- player relay support via `send`

## Events

Use `connection.on(event_name, callback)` with these exact event names:

- `"object"`, `"text"`, `"number"` → callback receives a `RoomEntity`
- `"lock"` → callback receives the raw lock payload, e.g. `{"key": "score", "from": 2}`
- `"client/connected"` → callback receives the raw protocol payload for the joined player
- `"client/send"` → callback receives the relayed JSON payload sent by a player
- `"close"` → callback receives the close exception object, or `None` on a clean reader shutdown
- `"error"` → callback receives the raised exception object
