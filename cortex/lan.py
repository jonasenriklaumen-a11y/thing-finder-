"""Das eigene Netz erkunden -- ohne nmap, nur mit der Standardbibliothek.

cortex soll auch Fragen beantworten koennen, die nicht im Web stehen: "laeuft
mein Drucker noch", "welche Geraete haengen hier eigentlich drin", "ist Home
Assistant erreichbar". Dafuer reicht ein einfacher TCP-Verbindungstest ueber
das eigene Subnetz -- parallel, mit kurzem Zeitlimit.

Zwei Grenzen sind bewusst hart gezogen:

* **Nur private Netze.** 10/8, 172.16/12, 192.168/16 und der Tailscale-Bereich
  100.64/10. Ein Scan fremder Adressen waere weder gewollt noch in Ordnung.
* **Nur die Frage "antwortet da etwas".** cortex klopft an, liest den Titel
  einer Weboberflaeche und geht weiter. Keine Passwortversuche, keine
  Schwachstellensuche, keine Sicherheitspruefung.
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

#: Ports, die cortex kennt, mit dem, was dort ueblicherweise laeuft.
KNOWN_PORTS: dict[int, str] = {
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    53: "DNS",
    80: "Webseite",
    139: "Windows-Freigabe",
    443: "Webseite (verschluesselt)",
    445: "Windows-Freigabe",
    515: "Drucker",
    554: "Kamera (RTSP)",
    631: "Drucker (IPP)",
    1883: "MQTT",
    3000: "Weboberflaeche",
    3306: "MySQL",
    3389: "Windows-Fernwartung",
    5000: "Weboberflaeche (Synology/UPnP)",
    5432: "PostgreSQL",
    5900: "Bildschirmfreigabe (VNC)",
    6379: "Redis",
    8006: "Proxmox",
    8080: "Weboberflaeche",
    8096: "Jellyfin",
    8123: "Home Assistant",
    8443: "Weboberflaeche (verschluesselt)",
    9000: "Weboberflaeche (Portainer)",
    9100: "Drucker (RAW)",
    11434: "Ollama",
    32400: "Plex",
}

#: Beim schnellen Durchlauf gepruefte Ports -- deckt fast jedes Haushaltsgeraet
#: ab, ohne dass ein Scan Minuten dauert.
QUICK_PORTS: tuple[int, ...] = (22, 80, 443, 445, 631, 1883, 8006, 8080, 8123, 9100, 11434, 32400)

#: Beim gruendlichen Durchlauf alle bekannten Ports.
FULL_PORTS: tuple[int, ...] = tuple(sorted(KNOWN_PORTS))

#: So lange wartet ein einzelner Verbindungsversuch. Im lokalen Netz antwortet
#: alles in Millisekunden -- laenger warten heisst nur, auf Totes zu warten.
CONNECT_TIMEOUT = 0.35

#: So viele Verbindungsversuche gleichzeitig. Ein /24 mit zwoelf Ports sind
#: gut 3000 Versuche; mit 200 Faeden ist das in wenigen Sekunden durch.
WORKERS = 200

#: Groesstes Netz, das cortex am Stueck durchgeht. Ein /16 waere 65.000
#: Adressen -- das dauert zu lange und will niemand.
MAX_HOSTS = 512


@dataclass
class Device:
    """Ein Geraet, das im Netz geantwortet hat."""

    address: str
    name: str = ""
    ports: list[int] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    #: Titel einer gefundenen Weboberflaeche -- oft der beste Hinweis darauf,
    #: was das Geraet ueberhaupt ist.
    title: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "address": self.address,
            "name": self.name,
            "ports": self.ports,
            "services": self.services,
            "title": self.title,
        }

    @property
    def label(self) -> str:
        return self.title or self.name or self.address


class NotPrivate(ValueError):
    """Die Adresse liegt ausserhalb der privaten Netze."""


def is_private_net(network: ipaddress.IPv4Network) -> bool:
    """Liegt *network* vollstaendig in einem privaten Bereich?

    Tailscale (100.64/10) zaehlt mit: fuer den Nutzer ist das sein eigenes
    Netz, auch wenn die Adressen offiziell dem Provider-Bereich gehoeren.
    """
    if network.is_private:
        return True
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    return network.subnet_of(tailnet)


def in_container() -> bool:
    """Laeuft Cortex AI in einem Container?"""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "lxc"))


def container_hint(subnet: str = "") -> str:
    """Sagt es, wenn cortex im Container am eigenen Netz vorbeisieht.

    Ein Container haengt normalerweise in Dockers eigenem Bruecken-Netz. Von
    dort ist das Heimnetz nicht zu sehen -- die Suche findet dann schlicht
    nichts, und niemand weiss warum. Also sagen wir es.
    """
    if not in_container():
        return ""
    address = (subnet or own_subnet()).split("/")[0]
    docker_ranges = ipaddress.ip_network("172.16.0.0/12")
    try:
        inside = ipaddress.ip_address(address) in docker_ranges
    except ValueError:
        inside = False
    if not inside:
        return ""
    return (
        "Cortex AI laeuft in einem Container und sieht nur dessen eigenes Netz "
        f"({subnet or address}), nicht dein Heimnetz. Abhilfe: den Container mit "
        "CORTEX_NETWORK=host starten "
        "(docker compose) bzw. `--network host` (docker run) -- oder cortex direkt auf "
        "dem Rechner laufen lassen."
    )


def own_subnet() -> str:
    """Das eigene /24, abgeleitet aus der Adresse zur Standardroute.

    Gibt "" zurueck, wenn keine private Adresse gefunden wird -- dann kann
    cortex nicht raten, welches Netz gemeint ist, und fragt lieber nach.
    """
    from cortex.web import lan_address

    address = lan_address()
    if not address:
        return ""
    try:
        interface = ipaddress.ip_interface(f"{address}/24")
    except ValueError:
        return ""
    network = interface.network
    return str(network) if is_private_net(network) else ""


def parse_subnet(subnet: str) -> ipaddress.IPv4Network:
    """Nimmt "192.168.1.0/24", "192.168.1.5" oder "192.168.1." entgegen.

    Raises:
        NotPrivate: Bei oeffentlichen Adressen oder zu grossen Netzen.
        ValueError: Wenn sich daraus kein Netz lesen laesst.
    """
    text = (subnet or "").strip().rstrip(".")
    if not text:
        raise ValueError("Kein Netz angegeben.")
    if "/" not in text:
        parts = text.split(".")
        if len(parts) == 3:  # "192.168.1" -> ganzes /24
            text = f"{text}.0/24"
        elif len(parts) == 4:  # eine einzelne Adresse -> ihr /24
            text = f"{text}/24"
        else:
            raise ValueError(f"Damit kann Cortex AI nichts anfangen: {subnet}")
    network = ipaddress.ip_network(text, strict=False)
    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("Nur IPv4-Netze.")
    if not is_private_net(network):
        raise NotPrivate(
            f"{network} ist kein privates Netz. Cortex AI durchsucht nur das eigene "
            "Heimnetz (10.x, 172.16-31.x, 192.168.x) und das Tailnet."
        )
    if network.num_addresses > MAX_HOSTS:
        raise NotPrivate(
            f"{network} hat {network.num_addresses} Adressen -- das dauert zu lange. "
            f"Hoechstens {MAX_HOSTS} auf einmal, also etwa ein /24."
        )
    return network


def port_open(address: str, port: int, timeout: float = CONNECT_TIMEOUT) -> bool:
    """Nimmt jemand auf *port* eine Verbindung an?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((address, port)) == 0


def hostname_of(address: str) -> str:
    """Rueckwaerts-Aufloesung, wenn der Router sie anbietet."""
    try:
        name = socket.gethostbyaddr(address)[0]
    except (OSError, socket.herror):
        return ""
    return "" if name == address else name


def web_title(address: str, port: int, timeout: float = 1.5) -> str:
    """Holt den Titel einer Weboberflaeche -- der beste Hinweis aufs Geraet."""
    import httpx

    scheme = "https" if port in (443, 8443) else "http"
    url = f"{scheme}://{address}:{port}/"
    try:
        response = httpx.get(url, timeout=timeout, verify=False, follow_redirects=True)
    except Exception:
        return ""
    if response.status_code >= 500:
        return ""
    from selectolax.parser import HTMLParser

    try:
        node = HTMLParser(response.text).css_first("title")
    except Exception:
        return ""
    return " ".join((node.text() or "").split())[:80] if node else ""


def check_host(
    address: str, ports: tuple[int, ...] = FULL_PORTS, *, with_title: bool = True
) -> Device | None:
    """Prueft ein einzelnes Geraet. `None`, wenn kein Port antwortet."""
    with ThreadPoolExecutor(max_workers=min(len(ports), 32)) as pool:
        found = [
            port
            for port, is_open in zip(
                ports, pool.map(lambda p: port_open(address, p), ports), strict=True
            )
            if is_open
        ]
    if not found:
        return None
    device = Device(
        address=address,
        name=hostname_of(address),
        ports=found,
        services=[KNOWN_PORTS[port] for port in found if port in KNOWN_PORTS],
    )
    if with_title:
        for port in (8123, 80, 8080, 443, 8443, 3000, 5000, 8006, 9000):
            if port in found:
                device.title = web_title(address, port)
                if device.title:
                    break
    return device


def scan(
    subnet: str = "",
    *,
    quick: bool = True,
    with_titles: bool = True,
    on_device: object = None,
) -> list[Device]:
    """Durchsucht *subnet* (leer = das eigene Netz) nach erreichbaren Geraeten.

    Zuerst wird nur geklopft -- ein Port je Adresse reicht, um zu wissen, dass
    da etwas ist. Erst fuer die Geraete, die geantwortet haben, wird genauer
    hingesehen. Das spart bei einem /24 den Grossteil der Versuche.

    Raises:
        NotPrivate: Bei oeffentlichen oder zu grossen Netzen.
        ValueError: Wenn kein Netz erkennbar ist.
    """
    network = parse_subnet(subnet or own_subnet())
    ports = QUICK_PORTS if quick else FULL_PORTS
    addresses = [str(host) for host in network.hosts()] or [str(network.network_address)]

    # Jede Adresse/Port-Kombination ist eine eigene Aufgabe. Der naheliegende
    # Weg -- je Adresse ein Faden, der die Ports der Reihe nach durchgeht --
    # ist in genau dem Fall langsam, der am haeufigsten vorkommt: eine
    # Firewall, die Pakete verschluckt statt abzulehnen. Dann wartet dieser
    # eine Faden zwoelfmal das volle Zeitlimit ab. Flach verteilt trifft das
    # Warten viele Faeden gleichzeitig.
    pairs = [(address, port) for address in addresses for port in ports]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        answered = pool.map(lambda pair: port_open(*pair), pairs)
        living = list(
            dict.fromkeys(
                address
                for (address, _), is_open in zip(pairs, answered, strict=True)
                if is_open
            )
        )

    devices: list[Device] = []
    with ThreadPoolExecutor(max_workers=min(max(len(living), 1), 32)) as pool:
        for device in pool.map(
            lambda address: check_host(address, ports, with_title=with_titles), living
        ):
            if device is None:
                continue
            devices.append(device)
            if callable(on_device):
                on_device(device)
    devices.sort(key=lambda item: tuple(int(part) for part in item.address.split(".")))
    return devices


def find_port(port: int, subnet: str = "") -> list[str]:
    """Alle Adressen im Netz, auf denen *port* offen ist.

    Damit findet cortex Home Assistant (8123) oder Ollama (11434), ohne das
    ganze Netz einzusammeln.
    """
    network = parse_subnet(subnet or own_subnet())
    addresses = [str(host) for host in network.hosts()]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return [
            address
            for address, is_open in zip(
                addresses, pool.map(lambda a: port_open(a, port), addresses), strict=True
            )
            if is_open
        ]
