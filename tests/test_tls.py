from pathlib import Path

from spacenav_ws.runtime import SYSTEM_STATE_DIR
from spacenav_ws.tls import _alt_name_line, cert_paths_for_host, resolve_tls_paths


def test_alt_name_line_uses_ip_for_ip_hosts():
    assert _alt_name_line("127.51.68.120") == "IP.1 = 127.51.68.120"


def test_alt_name_line_uses_dns_for_dns_hosts():
    assert _alt_name_line("cad-bridge.local") == "DNS.1 = cad-bridge.local"


def test_cert_paths_follow_requested_directory(tmp_path: Path):
    cert_path, key_path = cert_paths_for_host("127.51.68.120", cert_dir=tmp_path)
    assert cert_path == tmp_path / "127.51.68.120.crt"
    assert key_path == tmp_path / "127.51.68.120.key"


def test_resolve_tls_paths_accepts_explicit_material(tmp_path: Path):
    cert_file = tmp_path / "bridge.crt"
    key_file = tmp_path / "bridge.key"
    assert resolve_tls_paths("127.51.68.120", cert_file, key_file) == (cert_file.resolve(), key_file.resolve())


def test_system_state_dir_constant_is_system_scoped():
    assert SYSTEM_STATE_DIR == Path("/var/lib/spacenav-ws")
