"""Formato e tamanho de uma imagem pelo cabecalho (o backend nao tem Pillow)."""
from __future__ import annotations


def image_info(content: bytes) -> tuple[str, int, int] | None:
    """(formato, largura, altura) de PNG, JPEG ou WEBP; None se nao for um deles."""
    if content[:8] == b"\x89PNG\r\n\x1a\n" and len(content) >= 24:
        return "png", int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    if content[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(content):
            if content[i] != 0xFF:
                i += 1
                continue
            marker = content[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                return "jpeg", int.from_bytes(content[i + 7:i + 9], "big"), int.from_bytes(content[i + 5:i + 7], "big")
            i += 2 + int.from_bytes(content[i + 2:i + 4], "big")
        return None
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP" and len(content) >= 30:
        chunk = content[12:16]
        if chunk == b"VP8X":
            return "webp", 1 + int.from_bytes(content[24:27], "little"), 1 + int.from_bytes(content[27:30], "little")
        if chunk == b"VP8 ":
            return "webp", int.from_bytes(content[26:28], "little") & 0x3FFF, int.from_bytes(content[28:30], "little") & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(content[21:25], "little")
            return "webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None
