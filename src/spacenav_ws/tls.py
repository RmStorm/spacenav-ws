from __future__ import annotations

import ipaddress
import shutil
import subprocess
from pathlib import Path

from spacenav_ws.runtime import certs_dir


def cert_paths_for_host(host: str, cert_dir: Path | None = None) -> tuple[Path, Path]:
    directory = cert_dir or certs_dir()
    return directory / f"{host}.crt", directory / f"{host}.key"


def ensure_self_signed_cert(host: str, cert_dir: Path | None = None) -> tuple[Path, Path]:
    cert_path, key_path = cert_paths_for_host(host, cert_dir=cert_dir)
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    if cert_path.exists() != key_path.exists():
        raise RuntimeError(f"Incomplete TLS material for {host}: expected both {cert_path} and {key_path}")

    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError(
            "OpenSSL is required to auto-generate TLS material. Install openssl or pass --cert-file/--key-file explicitly."
        )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    config_path = cert_path.with_suffix(".cnf")
    config_path.write_text(_openssl_config(host), encoding="ascii")
    try:
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-days",
                "3650",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-config",
                str(config_path),
                "-extensions",
                "req_ext",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        cert_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)
        stderr = exc.stderr.strip() if exc.stderr else "unknown OpenSSL error"
        raise RuntimeError(f"Failed to generate TLS material for {host}: {stderr}") from exc
    finally:
        config_path.unlink(missing_ok=True)
    return cert_path, key_path


def resolve_tls_paths(host: str, cert_file: Path | None, key_file: Path | None) -> tuple[Path, Path]:
    if bool(cert_file) != bool(key_file):
        raise RuntimeError("Pass both --cert-file and --key-file together, or neither.")
    if cert_file and key_file:
        return cert_file.expanduser().resolve(), key_file.expanduser().resolve()
    return ensure_self_signed_cert(host)


def _openssl_config(host: str) -> str:
    return "\n".join(
        [
            "[ req ]",
            "default_bits = 2048",
            "distinguished_name = dn",
            "req_extensions = req_ext",
            "x509_extensions = req_ext",
            "prompt = no",
            "",
            "[ dn ]",
            f"CN = {host}",
            "",
            "[ req_ext ]",
            "subjectAltName = @alt_names",
            "",
            "[ alt_names ]",
            _alt_name_line(host),
            "",
        ]
    )


def _alt_name_line(host: str) -> str:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return f"DNS.1 = {host}"
    return f"IP.1 = {host}"
