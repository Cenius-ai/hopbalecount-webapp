"""Shared fixtures and helpers for the HopBaleCount test suite."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import sys
from typing import Dict, Iterable, Optional, Tuple
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import hopbalecount as hbc  # noqa: E402  (path bootstrap must run first)

DEMO_PASSPHRASE = "kiln-demo-pass"
# Test stores carry their own iteration count so a full suite stays fast; the
# real first-run path is exercised at PBKDF2_ITERATIONS elsewhere.
TEST_ITERATIONS = 1000
SALT = bytes.fromhex("00112233445566778899aabbccddeeff")


def auth_block(
    passphrase: str = DEMO_PASSPHRASE,
    iterations: int = TEST_ITERATIONS,
    salt: bytes = SALT,
) -> Dict[str, object]:
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations)
    return {
        "algo": hbc.PBKDF2_ALGO,
        "iterations": iterations,
        "salt": salt.hex(),
        "hash": digest.hex(),
    }


def bale_record(
    bale_id: str = "B0001",
    bine_row: str = "R-12",
    weight: float = 10.0,
    kiln: str = "KB-07",
    alpha: float = 5.0,
    created_at: str = "2025-08-01T09:00:00Z",
) -> Dict[str, object]:
    return {
        "bale_id": bale_id,
        "bine_row": bine_row,
        "harvest_weight_kg": weight,
        "kiln_batch_id": kiln,
        "alpha_acid_pct": alpha,
        "created_at": created_at,
    }


def write_store(
    path: pathlib.Path,
    bales: Iterable[Dict[str, object]] = (),
    auth: Optional[object] = True,
    version: object = 1,
) -> bytes:
    """Write a store document by hand; returns the exact bytes written."""
    document: Dict[str, object] = {}
    if version != "omit":
        document["schema_version"] = version
    document["auth"] = auth_block() if auth is True else auth
    document["bales"] = list(bales)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path.read_bytes()


def write_raw(path: pathlib.Path, text: str) -> bytes:
    path.write_text(text, encoding="utf-8")
    return path.read_bytes()


def read_store(path: pathlib.Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_cli(
    *argv: object,
    stdin: str = "",
    passphrase: Optional[str] = DEMO_PASSPHRASE,
) -> Tuple[int, str, str]:
    """Run hopbalecount.main in-process. Returns (exit code, stdout, stderr)."""
    environment = {
        "HOPBALECOUNT_PASSPHRASE": passphrase if passphrase is not None else "",
        "HOPBALECOUNT_DATA_FILE": "",
        "NO_COLOR": "1",
    }
    out, err = io.StringIO(), io.StringIO()
    original_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(stdin)
        with mock.patch.dict(os.environ, environment):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = hbc.main([str(item) for item in argv])
    finally:
        sys.stdin = original_stdin
    return code, out.getvalue(), err.getvalue()
