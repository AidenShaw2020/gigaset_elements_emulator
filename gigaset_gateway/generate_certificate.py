from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main() -> None:
    parser = argparse.ArgumentParser(description="Vytvoří lokální certifikát pro Gigaset Base")
    # Zakladna se pripojuje pres jmeno, ne pres IP, a self-signed certifikat
    # prijima bez overeni retezce.  IP adresy jsou tedy jen pojistka - vypisuji
    # se vsechny, na kterych brana muze bezet, aby se certifikat nemusel po
    # kazdem prestehovani generovat znovu.  Wildcard lze pouzit jen pro DNS
    # jmeno; pro IP adresu zadny wildcard v X.509 neexistuje.
    parser.add_argument(
        "--ip",
        action="append",
        default=None,
        help="IP adresa do SAN, lze uvest vicekrat",
    )
    parser.add_argument(
        "--dns",
        action="append",
        default=None,
        help="DNS jmeno do SAN, lze uvest vicekrat",
    )
    parser.add_argument("--cert", default="lab.cert.pem")
    parser.add_argument("--key", default="lab.key.pem")
    args = parser.parse_args()

    dns_names = args.dns or [
        "api-bs.gigaset-elements.de",
        "*.gigaset-elements.de",
        "gigaset-elements.de",
    ]
    ip_addresses = args.ip or []

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, dns_names[0])]
    )
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(item) for item in dns_names]
                + [
                    x509.IPAddress(ipaddress.ip_address(item))
                    for item in ip_addresses
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    Path(args.cert).write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    Path(args.key).write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    print(f"Vytvořeno: {args.cert}, {args.key}")


if __name__ == "__main__":
    main()
