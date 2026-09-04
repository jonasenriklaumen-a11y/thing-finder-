"""Tests fuer den Netzdurchlauf -- echte Sockets auf 127.0.0.1, kein Mock."""

from __future__ import annotations

import ipaddress
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cortex import lan


class TitlePage(BaseHTTPRequestHandler):
    title = "Home Assistant"

    def log_message(self, *args: object) -> None:
        return

    def do_GET(self) -> None:
        body = f"<html><head><title>{self.title}</title></head><body>x</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def listener():
    server = ThreadingHTTPServer(("127.0.0.1", 0), TitlePage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# -- Grenzen --------------------------------------------------------------
def test_only_private_networks_are_scanned() -> None:
    """Fremde Netze durchsucht cortex nicht -- weder gewollt noch in Ordnung."""
    for public in ("8.8.8.0/24", "1.1.1.1", "93.184.216.0/24"):
        with pytest.raises(lan.NotPrivate):
            lan.parse_subnet(public)


def test_private_ranges_are_accepted() -> None:
    for private in ("192.168.1.0/24", "10.0.0.0/24", "172.16.5.0/24"):
        assert lan.parse_subnet(private)


def test_the_tailnet_counts_as_home() -> None:
    """Fuer den Nutzer ist sein Tailnet das eigene Netz."""
    assert lan.is_private_net(ipaddress.ip_network("100.100.5.0/24"))
    assert lan.parse_subnet("100.100.5.0/24")


def test_huge_networks_are_refused() -> None:
    """Ein /16 waere 65.000 Adressen -- das will niemand abwarten."""
    with pytest.raises(lan.NotPrivate) as exc:
        lan.parse_subnet("192.168.0.0/16")
    assert "dauert zu lange" in str(exc.value)


def test_shorthand_forms_are_understood() -> None:
    assert str(lan.parse_subnet("192.168.1")) == "192.168.1.0/24"
    assert str(lan.parse_subnet("192.168.1.")) == "192.168.1.0/24"
    assert str(lan.parse_subnet("192.168.1.50")) == "192.168.1.0/24"


def test_nonsense_is_rejected_clearly() -> None:
    for bad in ("", "quatsch", "999.999.999.999"):
        with pytest.raises(ValueError):
            lan.parse_subnet(bad)


# -- Der eigentliche Durchlauf --------------------------------------------
def test_an_open_port_is_found(listener: int) -> None:
    assert lan.port_open("127.0.0.1", listener)


def test_a_closed_port_is_not(listener: int) -> None:
    assert not lan.port_open("127.0.0.1", 1)


def test_the_web_title_identifies_the_device(listener: int) -> None:
    """Der Titel der Weboberflaeche sagt oft mehr als jede Portnummer."""
    assert lan.web_title("127.0.0.1", listener) == "Home Assistant"


def test_a_title_from_a_dead_port_is_empty() -> None:
    assert lan.web_title("127.0.0.1", 1, timeout=0.2) == ""


def test_check_host_reports_what_answers(listener: int) -> None:
    device = lan.check_host("127.0.0.1", (listener, 1, 2), with_title=False)
    assert device is not None
    assert device.ports == [listener]
    assert device.address == "127.0.0.1"


def test_check_host_returns_nothing_for_silence() -> None:
    assert lan.check_host("127.0.0.1", (1, 2, 3), with_title=False) is None


def test_known_ports_get_a_plain_name() -> None:
    assert lan.KNOWN_PORTS[8123] == "Home Assistant"
    assert lan.KNOWN_PORTS[9100].startswith("Drucker")
    assert lan.KNOWN_PORTS[11434] == "Ollama"


def test_quick_ports_are_a_subset_of_all() -> None:
    assert set(lan.QUICK_PORTS) <= set(lan.FULL_PORTS)
    assert 8123 in lan.QUICK_PORTS  # Home Assistant muss im schnellen Lauf dabei sein


def test_a_device_describes_itself() -> None:
    device = lan.Device(address="192.168.1.5", name="nas.local", ports=[80], title="Synology")
    assert device.label == "Synology"
    assert lan.Device(address="192.168.1.6", name="drucker").label == "drucker"
    assert lan.Device(address="192.168.1.7").label == "192.168.1.7"


def test_scanning_a_single_address_network(listener: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein /32 hat keine Hosts -- der Durchlauf darf trotzdem nicht leer ausgehen."""
    monkeypatch.setattr(lan, "QUICK_PORTS", (listener,))
    found = lan.scan("127.0.0.1/32", with_titles=False)
    assert [device.address for device in found] == ["127.0.0.1"]


def test_scan_refuses_public_space() -> None:
    with pytest.raises(lan.NotPrivate):
        lan.scan("8.8.8.0/24")


def test_own_subnet_is_private_or_empty() -> None:
    subnet = lan.own_subnet()
    assert subnet == "" or lan.is_private_net(ipaddress.ip_network(subnet))


# -- Im Container -----------------------------------------------------------
def test_no_hint_outside_a_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lan, "in_container", lambda: False)
    assert lan.container_hint("172.17.0.0/24") == ""


def test_the_hint_names_the_way_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sonst sucht cortex im Docker-Netz und niemand versteht, warum nichts kommt."""
    monkeypatch.setattr(lan, "in_container", lambda: True)
    hint = lan.container_hint("172.17.0.0/24")
    assert "Container" in hint
    assert "CORTEX_NETWORK=host" in hint


def test_a_real_home_network_in_a_container_gets_no_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wer den Container mit --network host startet, sieht sein echtes Netz."""
    monkeypatch.setattr(lan, "in_container", lambda: True)
    assert lan.container_hint("192.168.1.0/24") == ""
    assert lan.container_hint("10.0.0.0/24") == ""


def test_container_detection_survives_a_missing_cgroup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lan.Path, "exists", lambda self: False)

    def boom(self, *args, **kwargs):
        raise OSError("kein /proc")

    monkeypatch.setattr(lan.Path, "read_text", boom)
    assert lan.in_container() is False
