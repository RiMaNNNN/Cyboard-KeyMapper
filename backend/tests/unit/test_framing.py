"""Unit tests for the Studio RPC frame codec."""

from __future__ import annotations

import pytest

from entities import Framing


def test_encode_wraps_payload_in_sof_eof() -> None:
    assert Framing.encode(b"\x01\x02") == bytes([Framing.SOF, 0x01, 0x02, Framing.EOF])


@pytest.mark.parametrize("special", [0xAB, 0xAC, 0xAD])
def test_encode_escapes_reserved_bytes(special: int) -> None:
    framed = Framing.encode(bytes([special]))
    assert framed == bytes([Framing.SOF, Framing.ESC, special, Framing.EOF])


def test_decoder_roundtrip_all_byte_values() -> None:
    payload = bytes(range(256))
    decoder = Framing.Decoder()
    frames = decoder.feed(Framing.encode(payload))
    assert frames == [payload]


def test_decoder_handles_split_chunks() -> None:
    payload = b"\xab\xac\xad hello \x00"
    framed = Framing.encode(payload)
    decoder = Framing.Decoder()
    collected: list[bytes] = []
    for i in range(len(framed)):
        collected.extend(decoder.feed(framed[i : i + 1]))
    assert collected == [payload]


def test_decoder_multiple_frames_in_one_chunk() -> None:
    decoder = Framing.Decoder()
    frames = decoder.feed(Framing.encode(b"one") + Framing.encode(b"two"))
    assert frames == [b"one", b"two"]


def test_decoder_ignores_noise_between_frames() -> None:
    decoder = Framing.Decoder()
    frames = decoder.feed(b"\x00\x11" + Framing.encode(b"data") + b"\x99")
    assert frames == [b"data"]


def test_decoder_restarts_frame_on_unexpected_sof() -> None:
    decoder = Framing.Decoder()
    stream = bytes([Framing.SOF, 0x01, 0x02, Framing.SOF, 0x03, Framing.EOF])
    assert decoder.feed(stream) == [b"\x03"]
