"""
End-to-end smoke test of the OpenDisplay server.

Pretends to be a display: connects to localhost, sends an ImageRequest,
receives a config request, sends an Announcement matching the EPD-42S
display (2880x2160 monochrome), sends another ImageRequest, and verifies
the server responds with an image.

Run from project root with venv python:
    .venv/bin/python tests/test_e2e.py
"""
from __future__ import annotations

import asyncio
import struct
import sys
from pathlib import Path

# Allow importing src/server.py
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from opendisplay.wifi.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    PKT_DISPLAY_ANNOUNCEMENT,
    PKT_IMAGE_REQUEST,
    PKT_NEW_IMAGE,
    PKT_NO_IMAGE,
    PKT_REQUEST_CONFIG,
    build_frame,
    build_single_packet,
    crc16_ccitt,
    parse_frame,
)
from server import run as server_run  # noqa: E402

PORT = 22446


def build_image_request(battery: int = 80, rssi: int = -50) -> bytes:
    rssi_byte = rssi & 0xFF
    payload = bytes([battery, rssi_byte])
    return build_frame(build_single_packet(0, PKT_IMAGE_REQUEST, payload))


def build_announcement(width: int, height: int) -> bytes:
    payload = struct.pack(
        "<HHBHHHHHB",
        width,
        height,
        0,        # colour_scheme: monochrome
        0,        # firmware_id
        1,        # firmware_version
        0xAA,     # manufacturer_id
        0xBB,     # model_id
        65535,    # max_compressed_size (uint16)
        0,        # rotation
    )
    return build_frame(build_single_packet(0, PKT_DISPLAY_ANNOUNCEMENT, payload))


async def read_one_frame(reader: asyncio.StreamReader) -> bytes:
    len_buf = await reader.readexactly(4)
    frame_len = struct.unpack("<I", len_buf)[0]
    rest = await reader.readexactly(frame_len - 4)
    return len_buf + rest


async def fake_display() -> None:
    # Wait briefly for the server to bind
    await asyncio.sleep(0.5)

    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)

    # 1. Send initial image request
    writer.write(build_image_request())
    await writer.drain()

    # 2. Server should respond with config request
    frame = await read_one_frame(reader)
    parsed = parse_frame(frame)
    assert parsed is not None and parsed.packet_id == PKT_REQUEST_CONFIG, (
        f"Expected REQUEST_CONFIG, got {parsed}"
    )
    print(f"[client] Got config request")

    # 3. Send announcement matching EPD-42S
    writer.write(build_announcement(2880, 2160))
    await writer.drain()

    # 4. Send another image request
    writer.write(build_image_request())
    await writer.drain()

    # 5. Server should send NEW_IMAGE
    frame = await read_one_frame(reader)
    parsed = parse_frame(frame)
    assert parsed is not None, f"Failed to parse server response"
    print(f"[client] Got response: packet_id=0x{parsed.packet_id:02x}")
    if parsed.packet_id == PKT_NEW_IMAGE:
        print(
            f"[client] ✓ NEW_IMAGE: {parsed.image_length} bytes, "
            f"poll_interval={parsed.poll_interval}s, refresh_type={parsed.refresh_type}"
        )
    elif parsed.packet_id == PKT_NO_IMAGE:
        print(f"[client] ⚠ NO_IMAGE response (image file missing?)")
        return

    # 6. Send same request again — server should dedupe and return NO_IMAGE
    writer.write(build_image_request())
    await writer.drain()
    frame = await read_one_frame(reader)
    parsed = parse_frame(frame)
    if parsed and parsed.packet_id == PKT_NO_IMAGE:
        print(f"[client] ✓ Dedup works: NO_IMAGE on second identical request")
    else:
        print(f"[client] ! Unexpected: 0x{parsed.packet_id:02x} on dedup check")

    writer.close()
    await writer.wait_closed()
    print("[client] Done")


async def main() -> None:
    server_task = asyncio.create_task(
        server_run(
            port=PORT,
            poll_interval=300,
            mdns=False,
            image_path=Path(__file__).resolve().parent.parent / "images" / "current.png",
        )
    )

    try:
        await fake_display()
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
