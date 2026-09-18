class GameRoomsError(Exception):
    """Base SDK error."""


class GameRoomsHttpError(GameRoomsError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class RoomNotFoundError(GameRoomsHttpError):
    def __init__(self, code: str):
        super().__init__(404, f"Room {code.upper()} was not found.")
        self.code = code.upper()


class RoomLockedError(GameRoomsHttpError):
    def __init__(self, code: str):
        super().__init__(403, f"Room {code.upper()} is locked.")
        self.code = code.upper()


class RoomFullError(GameRoomsHttpError):
    def __init__(self, code: str):
        super().__init__(403, f"Room {code.upper()} is full.")
        self.code = code.upper()


class WebSocketRejectedError(GameRoomsHttpError):
    def __init__(self, status_code: int, message: str):
        super().__init__(status_code, message)


class ConnectionClosedError(GameRoomsError):
    pass


class RequestTimeoutError(GameRoomsError):
    pass
