"""
Network protocol for multiplayer tank game.
Defines message types and serialization/deserialization functions.
"""

import json
from enum import Enum
from typing import Dict, Any


class MessageType(Enum):
    """Message types for client-server communication."""
    # Client to Server
    JOIN = "join"
    READY = "ready"
    PLAYER_INPUT = "player_input"
    DISCONNECT = "disconnect"
    REQUEST_START = "request_start"

    # Server to Client
    PLAYER_JOINED = "player_joined"
    PLAYER_LEFT = "player_left"
    PLAYER_READY = "player_ready"
    GAME_START = "game_start"
    GAME_STATE = "game_state"
    GAME_OVER = "game_over"
    CONNECTION_ACCEPTED = "connection_accepted"


def encode_message(msg_type: MessageType, data: Dict[str, Any] = None) -> bytes:
    """Encode a message to send over the network."""
    message = {
        "type": msg_type.value,
        "data": data or {}
    }
    json_str = json.dumps(message)
    # Add length prefix (4 bytes) + newline delimiter
    return json_str.encode('utf-8') + b'\n'


def decode_message(msg_bytes: bytes) -> tuple[MessageType, Dict[str, Any]]:
    """Decode a received message."""
    msg_str = msg_bytes.decode('utf-8').strip()
    message = json.loads(msg_str)
    msg_type = MessageType(message["type"])
    data = message.get("data", {})
    return msg_type, data
