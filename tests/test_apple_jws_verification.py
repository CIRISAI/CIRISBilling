"""
Tests for Apple JWS chain validation (AV-4).

Validates the negative paths of `_verify_apple_jws`. Happy-path validation
requires a real Apple-signed JWS (only Apple has the private key for the
Apple Root CA - G3 chain), so it is not testable here without fixture
data captured from a real App Store Server notification.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.exceptions import WebhookVerificationError
from app.services.apple_storekit_provider import _verify_apple_jws


def _make_self_signed_chain(now: datetime | None = None) -> tuple[ec.EllipticCurvePrivateKey, list[x509.Certificate]]:
    """Build a 2-cert self-signed chain (NOT rooted at Apple) for negative tests."""
    now = now or datetime.now(UTC)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root")])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_subject)
        .issuer_name(root_subject)
        .public_key(root_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Leaf")])
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_subject)
        .issuer_name(root_subject)
        .public_key(leaf_key.public_key())
        .serial_number(2)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(root_key, hashes.SHA256())
    )

    return leaf_key, [leaf_cert, root_cert]


def _sign_jws_with_chain(
    payload: dict, leaf_key: ec.EllipticCurvePrivateKey, chain: list[x509.Certificate]
) -> str:
    """Sign a JWS with the given chain in the x5c header."""
    x5c = [
        base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()
        for c in chain
    ]
    leaf_pem = leaf_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(
        payload,
        leaf_pem,
        algorithm="ES256",
        headers={"alg": "ES256", "x5c": x5c},
    )


def test_rejects_unexpected_algorithm():
    """A JWS with alg != ES256 must be rejected."""
    # PyJWT requires the signing key to match the algorithm; HS256 + a string secret.
    token = jwt.encode({"foo": "bar"}, "secret", algorithm="HS256")
    with pytest.raises(WebhookVerificationError, match="algorithm"):
        _verify_apple_jws(token)


def test_rejects_missing_x5c():
    """A JWS without an x5c chain must be rejected."""
    leaf_key, _ = _make_self_signed_chain()
    leaf_pem = leaf_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode({"foo": "bar"}, leaf_pem, algorithm="ES256")
    with pytest.raises(WebhookVerificationError, match="x5c"):
        _verify_apple_jws(token)


def test_rejects_chain_not_rooted_at_apple():
    """A self-signed chain (correct format but wrong root) must be rejected."""
    leaf_key, chain = _make_self_signed_chain()
    token = _sign_jws_with_chain({"foo": "bar"}, leaf_key, chain)
    with pytest.raises(WebhookVerificationError, match="Apple Root CA"):
        _verify_apple_jws(token)


def test_rejects_expired_certificate():
    """An expired cert in the chain must be rejected."""
    expired_now = datetime.now(UTC) - timedelta(days=400)
    leaf_key, chain = _make_self_signed_chain(now=expired_now)
    token = _sign_jws_with_chain({"foo": "bar"}, leaf_key, chain)
    with pytest.raises(WebhookVerificationError, match="validity period"):
        _verify_apple_jws(token)


def test_rejects_malformed_x5c():
    """An x5c header containing non-base64 garbage must be rejected."""
    # Build a header manually with bad x5c bytes.
    header = {"alg": "ES256", "x5c": ["!!!not-base64!!!"]}
    payload = {"foo": "bar"}
    # Hand-construct a token shape; signature is irrelevant since x5c parse fails first.
    head_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig_b64 = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    token = f"{head_b64}.{payload_b64}.{sig_b64}"
    with pytest.raises(WebhookVerificationError, match="x5c"):
        _verify_apple_jws(token)
