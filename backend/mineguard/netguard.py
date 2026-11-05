"""Application-level egress guard (CLAUDE.md rule 10: no internet access by the application).

Refuses any socket connect that is not loopback or a unix socket. This is defense in depth, not a firewall.
"""
import ipaddress
import socket

_installed = False
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex


class NetworkBlocked(OSError):
    pass


def _allowed(address) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except (ValueError, IndexError, TypeError):
        return address[0] == "localhost"


def _connect(self, address):
    if not _allowed(address):
        raise NetworkBlocked(f"mg.refusal.network_blocked: {address[0]}")
    return _orig_connect(self, address)


def _connect_ex(self, address):
    if not _allowed(address):
        raise NetworkBlocked(f"mg.refusal.network_blocked: {address[0]}")
    return _orig_connect_ex(self, address)


def install() -> None:
    global _installed
    if _installed:
        return
    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    _installed = True


def is_installed() -> bool:
    return _installed
