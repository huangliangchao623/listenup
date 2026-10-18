"""Advertise the service on the LAN via mDNS, so phones don't need an IP.

Why this exists
---------------
The laptop's IP changes every time the hotspot is re-created, which means the
address you typed into the phone yesterday stops working today.  mDNS (the
protocol behind Apple's Bonjour) lets the service publish a *name* on the local
network instead, and the name stays valid regardless of the address.

With this running, phones on the same Wi-Fi can use:

    http://listenup.local:8765

iOS and Android Chrome both resolve `.local` names via mDNS, so this works
without installing anything on the phone.

If zeroconf is not installed the service still works - it just falls back to
IP-based access.
"""
from __future__ import annotations

import socket

SERVICE_TYPE = "_http._tcp.local."
SERVICE_NAME = "listenup._http._tcp.local."
HOST_ALIAS = "listenup.local."

_zeroconf = None
_info = None


def lan_ip() -> str | None:
    """The address other devices on the LAN should use to reach us."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is actually sent; this only selects the outbound interface.
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def start(port: int, hostname_alias: str = "listenup") -> bool:
    """Publish `<hostname_alias>.local:<port>`.  Returns True on success."""
    global _zeroconf, _info

    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        return False

    ip = lan_ip()
    if not ip:
        return False

    try:
        _zeroconf = Zeroconf()
        _info = ServiceInfo(
            SERVICE_TYPE,
            SERVICE_NAME,
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={"path": "/", "app": "listenup"},
            server=f"{hostname_alias}.local.",
        )
        _zeroconf.register_service(_info)
        return True
    except Exception:  # noqa: BLE001
        stop()
        return False


def stop() -> None:
    """Withdraw the advertisement (called on shutdown)."""
    global _zeroconf, _info
    try:
        if _zeroconf is not None and _info is not None:
            _zeroconf.unregister_service(_info)
            _zeroconf.close()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _zeroconf = None
        _info = None


def access_urls(port: int) -> dict[str, str]:
    """Every way a client might reach the service, best first."""
    urls: dict[str, str] = {}
    h = socket.gethostname()
    urls["local"] = f"http://127.0.0.1:{port}"
    urls["mdns"] = f"http://listenup.local:{port}"
    urls["hostname"] = f"http://{h}:{port}"
    ip = lan_ip()
    if ip:
        urls["ip"] = f"http://{ip}:{port}"
    return urls
