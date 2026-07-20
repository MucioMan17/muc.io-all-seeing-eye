"""Find a camera's current IP address by its MAC address.

For networks where a DHCP reservation isn't possible (ISP router with no
admin access, etc.), a camera can be configured with its hardware MAC
address. Before connecting — and again whenever the stream drops — the
worker resolves the MAC to whatever IP the camera currently has:

  1. Check the kernel ARP table (/proc/net/arp).
  2. If absent, sweep the local subnet with cheap TCP probes to the RTSP
     port, which populates the ARP table, then check again.

Only IPv4 subnets up to 1024 addresses are swept (home networks are /24).
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import re
import socket
import subprocess
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 0.5
SWEEP_WORKERS = 64
MAX_SWEEP_HOSTS = 1024


def normalize_mac(mac: str) -> str:
    return mac.strip().lower().replace("-", ":")


def parse_arp_table(text: str, mac: str) -> Optional[str]:
    """Find `mac` in /proc/net/arp content; returns its IP or None."""
    mac = normalize_mac(mac)
    for line in text.splitlines()[1:]:
        parts = line.split()
        # columns: IP, HW type, Flags, HW address, Mask, Device
        if len(parts) >= 4 and normalize_mac(parts[3]) == mac:
            if parts[2] != "0x0":  # 0x0 = incomplete entry
                return parts[0]
    return None


def _arp_lookup(mac: str) -> Optional[str]:
    try:
        with open("/proc/net/arp") as f:
            return parse_arp_table(f.read(), mac)
    except OSError:
        return None


def _local_networks() -> List[ipaddress.IPv4Network]:
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    nets = []
    for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+([\d./]+)", out, re.MULTILINE):
        ifname, cidr = m.group(1), m.group(2)
        if ifname == "lo":
            continue
        try:
            net = ipaddress.ip_interface(cidr).network
        except ValueError:
            continue
        if net.num_addresses <= MAX_SWEEP_HOSTS:
            nets.append(net)
    return nets


def _probe(ip: str, port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(PROBE_TIMEOUT)
    try:
        s.connect((ip, port))
    except OSError:
        pass
    finally:
        s.close()


def find_ip_for_mac(mac: str, probe_port: int = 554) -> Optional[str]:
    """Resolve a MAC to its current IPv4 address on the local network."""
    mac = normalize_mac(mac)
    ip = _arp_lookup(mac)
    if ip:
        return ip
    for net in _local_networks():
        log.info("sweeping %s for camera %s", net, mac)
        with concurrent.futures.ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
            pool.map(lambda h: _probe(str(h), probe_port), net.hosts())
        ip = _arp_lookup(mac)
        if ip:
            return ip
    return None


def substitute_host(url: str, new_host: str) -> str:
    """Replace the host in a URL, preserving credentials and port."""
    parts = urlsplit(url)
    userinfo, sep, hostport = parts.netloc.rpartition("@")
    _, colon, port = hostport.partition(":")
    netloc = userinfo + sep + new_host + colon + port
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
