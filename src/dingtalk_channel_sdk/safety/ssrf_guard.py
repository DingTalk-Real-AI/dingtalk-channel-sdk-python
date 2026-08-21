"""SSRF 防护：下载前校验 URL 指向公网地址。"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse

from ..errors import ChannelError, ErrorCode


BLOCKED_NETWORKS = [
    ipaddress.ip_network(c)
    for c in [
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    ]
]


def _is_ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    if ip.version == 6:
        return (
            ip.is_loopback
            or ip.is_unspecified
            or ip.is_link_local
            or ip.is_private
            or ip.is_multicast
        )
    for net in BLOCKED_NETWORKS:
        if ip in net:
            return True
    return False


def _host_allowed(host: str, allowlist) -> bool:
    """白名单匹配：精确主机名或 *.suffix 通配。"""
    if not allowlist:
        return False
    host = host.lower().rstrip(".")
    for entry in allowlist:
        e = str(entry).lower().rstrip(".")
        if host == e:
            return True
        if e.startswith("*.") and host.endswith(e[1:]):
            return True
    return False


def assert_public_url(url: str, allowlist=None) -> None:
    """校验 URL 指向公网地址，命中内网/保留地址则抛 ChannelError(ssrf_blocked)。

    allowlist：豁免主机名列表（精确或 *.suffix 通配），用于企业内网 CDN/专有云场景。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ChannelError(
            ErrorCode.SSRF_BLOCKED,
            f"ssrf: disallowed scheme {parsed.scheme!r}",
        )
    host = parsed.hostname or ""
    if not host:
        raise ChannelError(
            ErrorCode.SSRF_BLOCKED,
            "ssrf: missing hostname",
        )

    # 白名单豁免
    if _host_allowed(host, allowlist):
        return

    # 直接 IP 字面量
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_ip_blocked(ip):
            raise ChannelError(
                ErrorCode.SSRF_BLOCKED,
                f"ssrf: blocked address {ip}",
            )
        return

    # DNS 解析，校验所有返回地址
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ChannelError(
            ErrorCode.SSRF_BLOCKED,
            f"ssrf: DNS resolution failed for {host}: {e}",
        )
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_ip_blocked(ip):
            raise ChannelError(
                ErrorCode.SSRF_BLOCKED,
                f"ssrf: {host} resolves to blocked address {ip}",
            )
