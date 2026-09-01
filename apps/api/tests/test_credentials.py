"""The credential path, which only ever runs in the deployed environment.

Railway supplies a service-account key as JSON in an environment variable; Google's
libraries expect GOOGLE_APPLICATION_CREDENTIALS to name a *file*. This is the seam
between those two facts, and it is not exercised by anything else in the test suite —
locally the variable is unset and everything falls through to ADC.
"""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import credentials


def _throwaway_key_json() -> str:
    """A syntactically real service-account key, signed by a key generated right here.

    Nothing about it is a secret: it authenticates to nothing, and it exists so the test
    proves the whole parse-and-build path rather than only the None branch.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return json.dumps({
        "type": "service_account",
        "project_id": "pulso-madrid",
        "private_key_id": "0" * 40,
        "private_key": pem,
        "client_email": "pulso-test@pulso-madrid.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


def test_unset_falls_back_to_adc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local development configures nothing and must keep working."""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", raising=False)
    assert credentials() is None


def test_json_in_the_environment_builds_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", _throwaway_key_json())
    creds = credentials()
    assert creds is not None
    assert creds.service_account_email == "pulso-test@pulso-madrid.iam.gserviceaccount.com"


def test_a_file_path_gives_a_useful_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mistake this is most likely to meet: someone pastes a path, not the key."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "/secrets/key.json")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        credentials()
