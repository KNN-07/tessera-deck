"""Host library for the Tessera Deck."""
from .deck import Deck, DeckError, DeckTimeout
from .protocol import (
    DeckConfig,
    DeckInfo,
    DeckState,
    EncoderButtonEvent,
    EncoderRotateEvent,
    KeyEvent,
    PowerEvent,
    TouchEvent,
)
from .transport import find_serial_ports

__version__ = "0.1.0"
__all__ = [
    "Deck", "DeckError", "DeckTimeout", "DeckConfig", "DeckInfo", "DeckState",
    "KeyEvent", "EncoderRotateEvent", "EncoderButtonEvent", "TouchEvent", "PowerEvent",
    "find_serial_ports",
]
