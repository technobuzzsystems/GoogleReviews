"""Helpers for local network URLs used by the feedback QR code."""

from __future__ import annotations

import socket


def get_lan_ip(fallback: str = "127.0.0.1") -> str:
    """Return the IPv4 address other devices on the LAN should use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = fallback
    finally:
        sock.close()

    return ip


def build_lan_url(port: int | str, path: str = "") -> str:
    """Build an HTTP URL using this machine's current LAN IPv4 address."""
    clean_path = f"/{path.lstrip('/')}" if path else ""
    return f"http://{get_lan_ip()}:{port}{clean_path}"
