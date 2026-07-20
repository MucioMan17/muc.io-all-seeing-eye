from allseeingeye.discover import normalize_mac, parse_arp_table, substitute_host

ARP = """\
IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         a4:91:b1:11:22:33     *        wlan0
192.168.1.50     0x1         0x2         AA:BB:CC:DD:EE:FF     *        wlan0
192.168.1.99     0x1         0x0         00:00:00:00:00:00     *        wlan0
"""


def test_normalize_mac_variants():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac(" aa:bb:cc:dd:ee:ff ") == "aa:bb:cc:dd:ee:ff"


def test_parse_arp_finds_mac_case_insensitive():
    assert parse_arp_table(ARP, "aa:bb:cc:dd:ee:ff") == "192.168.1.50"
    assert parse_arp_table(ARP, "AA-BB-CC-DD-EE-FF") == "192.168.1.50"


def test_parse_arp_ignores_incomplete_entries():
    # Flags 0x0 = incomplete; must not match even if MAC matched.
    assert parse_arp_table(ARP, "00:00:00:00:00:00") is None


def test_parse_arp_unknown_mac():
    assert parse_arp_table(ARP, "de:ad:be:ef:00:01") is None


def test_substitute_host_keeps_credentials_and_port():
    url = "rtsp://camuser:campass@192.168.1.50:554/stream2"
    assert substitute_host(url, "192.168.1.73") == \
        "rtsp://camuser:campass@192.168.1.73:554/stream2"


def test_substitute_host_password_with_at_sign():
    url = "rtsp://user:p@ss@oldhost:554/stream1"
    assert substitute_host(url, "10.0.0.9") == "rtsp://user:p@ss@10.0.0.9:554/stream1"


def test_substitute_host_no_credentials_no_port():
    assert substitute_host("rtsp://oldhost/stream1", "10.0.0.9") == "rtsp://10.0.0.9/stream1"
