#!/usr/bin/env python3
"""HopBaleCount - record hop bales and summarise kiln batches by alpha mass.

A single-file, standard-library-only command line tool for a small hop yard:

  * ``add``      record one bale (bine row, harvest weight kg, kiln batch, alpha %)
  * ``list``     read the bales back as an aligned table
  * ``summary``  per-kiln-batch and grand-total alpha mass / weighted alpha
  * ``session``  create or verify the local operator passphrase

Everything lives in one versioned JSON document that is validated on every load
and written atomically (temp file + os.replace, mode 0600).
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__version__ = "1.0.0"

PROGRAM = "hopbalecount"
SCHEMA_VERSION = 1
DEFAULT_DATA_FILE = "hopbales.json"
DATA_FILE_ENV = "HOPBALECOUNT_DATA_FILE"
PASSPHRASE_ENV = "HOPBALECOUNT_PASSPHRASE"

# Documented exit codes.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_BAD_JSON = 3
EXIT_BAD_VERSION = 4
EXIT_AUTH = 5

# Field limits (see F6).
MAX_WEIGHT_KG = 10000.0
MIN_WEIGHT_KG = 0.0
MIN_ALPHA_PCT = 0.0
MAX_ALPHA_PCT = 100.0
MAX_TEXT_LENGTH = 40

# Passphrase gate (see F5 / D5).
PBKDF2_ALGO = "pbkdf2_hmac_sha256"
PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
MIN_PASSPHRASE_LENGTH = 8
AUTH_ATTEMPTS = 3
MAX_PASSPHRASE_ATTEMPTS = 3

PROGRESS_THRESHOLD = 200

FIELD_ORDER = ("bine_row", "harvest_weight_kg", "kiln_batch_id", "alpha_acid_pct")
FIELD_LABELS = {
    "bine_row": "bine row",
    "harvest_weight_kg": "weight",
    "kiln_batch_id": "kiln batch",
    "alpha_acid_pct": "alpha",
}
FIELD_PROMPTS = {
    "bine_row": "Bine row: ",
    "harvest_weight_kg": "Harvest weight (kg): ",
    "kiln_batch_id": "Kiln batch id: ",
    "alpha_acid_pct": "Alpha acid (%): ",
}

# ---------------------------------------------------------------------------
# Committed design direction (terminal dark, calm-precise, monospace, compact).
# Accent: oklch(0.58 0.12 191) == #00908b - used only for primary status, never
# as the sole carrier of meaning. See DESIGN.md.
# ---------------------------------------------------------------------------
ACCENT_HEX = "#00908b"
ACCENT_RGB = (0, 144, 139)
ACCENT_ANSI = "\x1b[38;2;%d;%d;%dm" % ACCENT_RGB
ANSI_RESET = "\x1b[0m"


# ---------------------------------------------------------------------------
# Errors - one class per documented exit code.
# ---------------------------------------------------------------------------
class HopError(Exception):
    """Base class for every anticipated, operator-facing failure."""

    exit_code = EXIT_ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UsageError(HopError):
    exit_code = EXIT_USAGE


class StoreReadError(HopError):
    exit_code = EXIT_BAD_JSON


class StoreVersionError(HopError):
    exit_code = EXIT_BAD_VERSION


class AuthError(HopError):
    exit_code = EXIT_AUTH


class FieldError(Exception):
    """A single field failed validation; the message names the fix."""


# ---------------------------------------------------------------------------
# Terminal presentation helpers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Glyphs:
    """Box-drawing characters, with an ASCII fallback for narrow locales."""

    rule: str
    dot: str
    check: str
    dash: str


UNICODE_GLYPHS = Glyphs(rule="\u2500", dot="\u00b7", check="\u2713", dash="\u2014")
ASCII_GLYPHS = Glyphs(rule="-", dot="-", check="ok", dash="-")


def glyphs_for(stream: object) -> Glyphs:
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "\u2500\u00b7\u2713\u2014".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return ASCII_GLYPHS
    return UNICODE_GLYPHS


def colour_enabled(stream: object) -> bool:
    """Accent colour only on a real terminal, and never against NO_COLOR."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, enabled: bool) -> str:
    return "%s%s%s" % (ACCENT_ANSI, text, ANSI_RESET) if enabled else text


def format_kg(value: float) -> str:
    return "%.2f kg" % value


def format_pct(value: float) -> str:
    return "%.2f%%" % value


def format_count(count: int, noun: str = "bale") -> str:
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: Sequence[str],
    glyphs: Glyphs,
    total_row: Optional[Sequence[str]] = None,
) -> List[str]:
    """Render aligned monospace columns with box-drawing rules.

    The accent is applied to the rules only, so no meaning is carried by colour.
    """
    widths = [len(header) for header in headers]
    for row in list(rows) + ([total_row] if total_row else []):
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: Sequence[str], cell_aligns: Sequence[str]) -> str:
        parts = []
        for cell, width, align in zip(cells, widths, cell_aligns):
            parts.append(cell.rjust(width) if align == "right" else cell.ljust(width))
        return "  ".join(parts).rstrip()

    accent = colour_enabled(sys.stdout)
    rule = paint("  ".join(glyphs.rule * width for width in widths), accent)
    rendered = [line(headers, ["left"] * len(headers)), rule]
    for row in rows:
        rendered.append(line(row, aligns))
    if total_row is not None:
        rendered.append(rule)
        rendered.append(line(total_row, aligns))
    return rendered


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Bale:
    bale_id: str
    bine_row: str
    harvest_weight_kg: float
    kiln_batch_id: str
    alpha_acid_pct: float
    created_at: str

    def to_record(self) -> Dict[str, object]:
        return {
            "bale_id": self.bale_id,
            "bine_row": self.bine_row,
            "harvest_weight_kg": self.harvest_weight_kg,
            "kiln_batch_id": self.kiln_batch_id,
            "alpha_acid_pct": self.alpha_acid_pct,
            "created_at": self.created_at,
        }


@dataclass
class Store:
    path: str
    schema_version: int
    auth: Optional[Dict[str, object]]
    bales: List[Bale]


@dataclass(frozen=True)
class BatchTotals:
    bale_count: int
    total_weight_kg: float
    alpha_mass_kg: float
    weighted_avg_alpha_pct: float


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_bale_id(bales: Sequence[Bale]) -> str:
    """Assign 'B' + zero-padded sequence from the highest existing id (D3)."""
    highest = 0
    for bale in bales:
        match = re.fullmatch(r"B(\d+)", bale.bale_id or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return "B%04d" % (highest + 1)


def resolve_data_file(args: argparse.Namespace) -> str:
    chosen = getattr(args, "data_file", None)
    if chosen:
        return str(chosen)
    from_env = os.environ.get(DATA_FILE_ENV, "").strip()
    if from_env:
        return from_env
    return DEFAULT_DATA_FILE


# ---------------------------------------------------------------------------
# Store load / save (F2)
# ---------------------------------------------------------------------------
def _reject_constant(token: str) -> None:
    raise ValueError("non-finite number %s" % token)


def _parse_json(text: str) -> object:
    return json.loads(text, parse_constant=_reject_constant)


def _record_error(path: str, reason: str) -> StoreReadError:
    return StoreReadError("%s is not a valid HopBaleCount data file: %s" % (path, reason))


def _bale_from_record(path: str, index: int, record: object) -> Bale:
    where = "bales[%d]" % index
    if not isinstance(record, dict):
        raise _record_error(path, "%s is not an object" % where)
    for key in ("bale_id", "bine_row", "kiln_batch_id", "created_at"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            raise _record_error(path, "%s.%s must be a non-empty string" % (where, key))
    for key in ("harvest_weight_kg", "alpha_acid_pct"):
        value = record.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise _record_error(path, "%s.%s must be a number" % (where, key))
        if not math.isfinite(float(value)):
            raise _record_error(path, "%s.%s must be finite" % (where, key))
    return Bale(
        bale_id=str(record["bale_id"]),
        bine_row=str(record["bine_row"]),
        harvest_weight_kg=float(record["harvest_weight_kg"]),
        kiln_batch_id=str(record["kiln_batch_id"]),
        alpha_acid_pct=float(record["alpha_acid_pct"]),
        created_at=str(record["created_at"]),
    )


def _validate_auth_block(path: str, auth: object) -> None:
    if not isinstance(auth, dict):
        raise _record_error(path, "auth must be an object")
    if auth.get("algo") != PBKDF2_ALGO:
        raise _record_error(path, "auth.algo must be %s" % PBKDF2_ALGO)
    iterations = auth.get("iterations")
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1000:
        raise _record_error(path, "auth.iterations must be an integer of at least 1000")
    for key in ("salt", "hash"):
        try:
            bytes.fromhex(str(auth.get(key, "")))
        except ValueError:
            raise _record_error(path, "auth.%s must be a hex string" % key)


def load_store(path: str) -> Store:
    """Load and validate the store. Never writes; raises on any guard failure."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        # A missing file is a fresh, empty store: nothing to reject or protect.
        return Store(path=path, schema_version=SCHEMA_VERSION, auth=None, bales=[])
    except OSError as exc:
        raise StoreReadError("could not read %s: %s" % (path, exc.strerror or exc))

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StoreReadError("%s is not valid UTF-8 at byte %d" % (path, exc.start))

    try:
        document = _parse_json(text)
    except json.JSONDecodeError as exc:
        raise StoreReadError(
            "%s is not valid JSON at line %d column %d" % (path, exc.lineno, exc.colno)
        )
    except ValueError as exc:
        raise StoreReadError("%s is not valid JSON: %s" % (path, exc))

    if not isinstance(document, dict):
        raise StoreReadError("%s is not a HopBaleCount data file (expected a JSON object)" % path)

    if "schema_version" not in document:
        raise StoreVersionError("data file version missing; this build supports %d" % SCHEMA_VERSION)
    version = document["schema_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise StoreVersionError(
            "data file version %s is not an integer; this build supports %d"
            % (json.dumps(version), SCHEMA_VERSION)
        )
    if version != SCHEMA_VERSION:
        raise StoreVersionError(
            "unsupported data file version %d; this build supports %d" % (version, SCHEMA_VERSION)
        )

    auth = document.get("auth")
    if auth is not None:
        _validate_auth_block(path, auth)

    bales_field = document.get("bales", [])
    if not isinstance(bales_field, list):
        raise _record_error(path, "bales must be an array")
    bales = [_bale_from_record(path, index, record) for index, record in enumerate(bales_field)]
    return Store(path=path, schema_version=version, auth=auth, bales=bales)


def save_store(store: Store) -> None:
    """Write the store atomically: temp file in the same directory, then replace."""
    document = {
        "schema_version": SCHEMA_VERSION,
        "auth": store.auth,
        "bales": [bale.to_record() for bale in store.bales],
    }
    payload = json.dumps(document, indent=2, sort_keys=False) + "\n"
    directory = os.path.dirname(os.path.abspath(store.path))
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise HopError("could not create %s: %s" % (directory, exc.strerror or exc))

    descriptor = -1
    temp_path = ""
    try:
        descriptor, temp_path = tempfile.mkstemp(prefix=".hopbales-", suffix=".tmp", dir=directory)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, store.path)
        temp_path = ""
    except OSError as exc:
        raise HopError("could not write %s: %s" % (store.path, exc.strerror or exc))
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if temp_path:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
    _fsync_directory(directory)


def _fsync_directory(directory: str) -> None:
    with contextlib.suppress(OSError):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


# ---------------------------------------------------------------------------
# Passphrase gate (F5)
# ---------------------------------------------------------------------------
def make_auth(passphrase: str) -> Dict[str, object]:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return {
        "algo": PBKDF2_ALGO,
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt.hex(),
        "hash": digest.hex(),
    }


def verify_passphrase(passphrase: str, auth: Dict[str, object]) -> bool:
    salt = bytes.fromhex(str(auth["salt"]))
    iterations = int(str(auth["iterations"]))
    expected = bytes.fromhex(str(auth["hash"]))
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


def _read_hidden(prompt: str) -> str:
    """Read a secret: getpass on a terminal, one stdin line when piped."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    if sys.stdin.isatty():
        sys.stderr.write("\n")
        try:
            return getpass.getpass("")
        except EOFError:
            raise AuthError("no passphrase supplied")
    line = sys.stdin.readline()
    sys.stderr.write("\n")
    if line == "":
        raise AuthError("no passphrase supplied on stdin")
    return line.rstrip("\r\n")


def read_passphrase(prompt: str) -> str:
    scripted = os.environ.get(PASSPHRASE_ENV, "")
    if scripted:
        return scripted
    return _read_hidden(prompt)


def _store_new_passphrase(store: Store, passphrase: str) -> None:
    print("Hashing passphrase...", file=sys.stderr)
    store.auth = make_auth(passphrase)
    save_store(store)
    print("Passphrase set.")


def create_passphrase(store: Store) -> None:
    """First run: choose a passphrase, confirm it, store only its PBKDF2 hash."""
    scripted = os.environ.get(PASSPHRASE_ENV, "")
    if scripted:
        if len(scripted) < MIN_PASSPHRASE_LENGTH:
            raise AuthError(
                "passphrase must be at least %d characters; got %d"
                % (MIN_PASSPHRASE_LENGTH, len(scripted))
            )
        _store_new_passphrase(store, scripted)
        return

    glyphs = glyphs_for(sys.stderr)
    print(
        "First run: this data file has no passphrase yet %s choose one for the operator."
        % glyphs.dash,
        file=sys.stderr,
    )
    print(
        "Minimum %d characters. It is stored only as a salted PBKDF2-HMAC-SHA256 hash."
        % MIN_PASSPHRASE_LENGTH,
        file=sys.stderr,
    )
    for _attempt in range(MAX_PASSPHRASE_ATTEMPTS):
        first = _read_hidden("Passphrase: ")
        if len(first) < MIN_PASSPHRASE_LENGTH:
            print(
                "Error: passphrase must be at least %d characters; got %d"
                % (MIN_PASSPHRASE_LENGTH, len(first)),
                file=sys.stderr,
            )
            continue
        second = _read_hidden("Confirm passphrase: ")
        if first != second:
            print("Error: passphrases did not match; try again", file=sys.stderr)
            continue
        _store_new_passphrase(store, first)
        return
    raise AuthError(
        "passphrase was not set (%d of %d attempts)"
        % (MAX_PASSPHRASE_ATTEMPTS, MAX_PASSPHRASE_ATTEMPTS)
    )


def ensure_authenticated(store: Store) -> str:
    """Gate every data command. Returns 'created' or 'verified'."""
    if store.auth is None:
        create_passphrase(store)
        return "created"
    for attempt in range(1, AUTH_ATTEMPTS + 1):
        supplied = read_passphrase("Passphrase: ")
        if verify_passphrase(supplied, store.auth):
            return "verified"
        message = "incorrect passphrase (%d of %d)" % (attempt, AUTH_ATTEMPTS)
        if attempt == AUTH_ATTEMPTS:
            raise AuthError(message)
        print("Error: %s" % message, file=sys.stderr)
    raise AuthError("incorrect passphrase (%d of %d)" % (AUTH_ATTEMPTS, AUTH_ATTEMPTS))


# ---------------------------------------------------------------------------
# Field validation (F6)
# ---------------------------------------------------------------------------
def echo_value(raw: str) -> str:
    """Echo what the operator sent, whitespace collapsed and length capped."""
    cleaned = " ".join(str(raw).split())
    if not cleaned:
        return "''"
    if len(cleaned) > 60:
        cleaned = cleaned[:57] + "..."
    return cleaned


def parse_text_field(name: str, raw: str) -> str:
    value = str(raw).strip()
    if not value:
        raise FieldError(
            "%s must not be empty; got '' (accepted: 1 to %d characters)"
            % (name, MAX_TEXT_LENGTH)
        )
    if len(value) > MAX_TEXT_LENGTH:
        raise FieldError(
            "%s must be at most %d characters; got %s (%d characters) (accepted: 1 to %d characters)"
            % (name, MAX_TEXT_LENGTH, echo_value(raw), len(value), MAX_TEXT_LENGTH)
        )
    return value


def parse_bine_row(raw: str) -> str:
    return parse_text_field("bine_row", raw)


def parse_kiln_batch(raw: str) -> str:
    return parse_text_field("kiln_batch_id", raw)


def _parse_number(name: str, raw: str, accepted: str) -> float:
    text = str(raw).strip()
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise FieldError(
            "%s must be a number; got %s (accepted: %s)" % (name, echo_value(text), accepted)
        )
    if not math.isfinite(value):
        raise FieldError(
            "%s must be a finite number; got %s (accepted: %s)"
            % (name, echo_value(text), accepted)
        )
    return value


def parse_weight(raw: str) -> float:
    accepted = "a number greater than 0 and at most 10000, in kg"
    value = _parse_number("harvest_weight_kg", raw, accepted)
    if value <= MIN_WEIGHT_KG:
        raise FieldError(
            "harvest_weight_kg must be greater than 0; got %s (accepted: %s)"
            % (echo_value(raw), accepted)
        )
    if value > MAX_WEIGHT_KG:
        raise FieldError(
            "harvest_weight_kg must be at most 10000; got %s (accepted: %s)"
            % (echo_value(raw), accepted)
        )
    return value


def parse_alpha(raw: str) -> float:
    accepted = "0 to 100, percent of bale weight"
    value = _parse_number("alpha_acid_pct", raw, accepted)
    if value < MIN_ALPHA_PCT or value > MAX_ALPHA_PCT:
        raise FieldError(
            "alpha_acid_pct must be between 0 and 100; got %s (accepted: %s)"
            % (echo_value(raw), accepted)
        )
    return value


PARSERS: Dict[str, Callable[[str], object]] = {
    "bine_row": parse_bine_row,
    "harvest_weight_kg": parse_weight,
    "kiln_batch_id": parse_kiln_batch,
    "alpha_acid_pct": parse_alpha,
}


def validate_flags(raw: Dict[str, Optional[str]]) -> Tuple[Dict[str, object], List[str]]:
    """Validate every flag-supplied field, collecting all problems at once."""
    parsed: Dict[str, object] = {}
    problems: List[str] = []
    for name in FIELD_ORDER:
        value = raw.get(name)
        if value is None:
            continue
        try:
            parsed[name] = PARSERS[name](value)
        except FieldError as exc:
            problems.append(str(exc))
    return parsed, problems


def ask_field(name: str) -> object:
    """Interactive prompt for one field: re-prompts until the value is accepted."""
    while True:
        sys.stderr.write(FIELD_PROMPTS[name])
        sys.stderr.flush()
        line = sys.stdin.readline()
        if not sys.stdin.isatty():
            sys.stderr.write("\n")
            sys.stderr.flush()
        if line == "":
            raise UsageError("input ended before all four fields were supplied")
        raw = line.rstrip("\r\n")
        try:
            return PARSERS[name](raw)
        except FieldError as exc:
            print("Error: %s" % exc, file=sys.stderr)


def display_field(name: str, value: object) -> str:
    if name == "harvest_weight_kg":
        return format_kg(float(str(value)))
    if name == "alpha_acid_pct":
        return format_pct(float(str(value)))
    return str(value)


def collect_interactive_fields(values: Dict[str, object], glyphs: Glyphs) -> Dict[str, object]:
    """Prompt for the fields not supplied as flags; echo each accepted value."""
    collected = dict(values)
    for name in FIELD_ORDER:
        if name in collected:
            continue
        parsed = ask_field(name)
        collected[name] = parsed
        print(
            "  %s %s %s" % (glyphs.check, FIELD_LABELS[name], display_field(name, parsed)),
            file=sys.stderr,
        )
    return collected


# ---------------------------------------------------------------------------
# Aggregation (F4 / D4)
# ---------------------------------------------------------------------------
def _totals(bale_count: int, weight_sum: float, alpha_weight_sum: float) -> BatchTotals:
    """alpha_mass = sum(w x pct / 100); weighted alpha = sum(w x pct) / sum(w)."""
    alpha_mass = alpha_weight_sum / 100.0
    weighted = alpha_weight_sum / weight_sum if weight_sum > 0 else 0.0
    return BatchTotals(
        bale_count=bale_count,
        total_weight_kg=weight_sum,
        alpha_mass_kg=alpha_mass,
        weighted_avg_alpha_pct=weighted,
    )


def summarise(bales: Sequence[Bale]) -> Tuple[List[Tuple[str, BatchTotals]], BatchTotals]:
    """Per-kiln-batch totals in first-seen order, plus the grand total."""
    order: List[str] = []
    counts: Dict[str, int] = {}
    weights: Dict[str, float] = {}
    alpha_weights: Dict[str, float] = {}
    for bale in bales:
        key = bale.kiln_batch_id
        if key not in counts:
            order.append(key)
            counts[key] = 0
            weights[key] = 0.0
            alpha_weights[key] = 0.0
        counts[key] += 1
        weights[key] += bale.harvest_weight_kg
        alpha_weights[key] += bale.harvest_weight_kg * bale.alpha_acid_pct

    per_batch = [(key, _totals(counts[key], weights[key], alpha_weights[key])) for key in order]
    grand = _totals(len(bales), sum(weights.values()), sum(alpha_weights.values()))
    return per_batch, grand


# ---------------------------------------------------------------------------
# Filters (F7)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Filters:
    kiln_batch: str = ""
    bine_row: str = ""

    def describe(self) -> str:
        parts = []
        if self.kiln_batch:
            parts.append("kiln-batch=%s" % self.kiln_batch)
        if self.bine_row:
            parts.append("bine-row=%s" % self.bine_row)
        return ", ".join(parts)


def build_filters(args: argparse.Namespace) -> Filters:
    kiln = (getattr(args, "kiln_batch", None) or "").strip()
    row = (getattr(args, "bine_row", None) or "").strip()
    return Filters(kiln_batch=kiln, bine_row=row)


def apply_filters(bales: Sequence[Bale], filters: Filters) -> List[Bale]:
    """Case-insensitive substring match on kiln batch and bine row, combined with AND."""
    rows = list(bales)
    if filters.kiln_batch:
        needle = filters.kiln_batch.lower()
        rows = [bale for bale in rows if needle in bale.kiln_batch_id.lower()]
    if filters.bine_row:
        needle = filters.bine_row.lower()
        rows = [bale for bale in rows if needle in bale.bine_row.lower()]
    return rows


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
LIST_HEADERS = ["ID", "BINE ROW", "HARVEST WEIGHT", "KILN BATCH", "ALPHA ACID"]
LIST_ALIGNS = ["left", "left", "right", "left", "right"]
SUMMARY_HEADERS = ["KILN BATCH", "BALES", "TOTAL WEIGHT", "ALPHA MASS", "WEIGHTED ALPHA"]
SUMMARY_ALIGNS = ["left", "right", "right", "right", "right"]


def bale_row(bale: Bale) -> List[str]:
    return [
        bale.bale_id,
        bale.bine_row,
        format_kg(bale.harvest_weight_kg),
        bale.kiln_batch_id,
        format_pct(bale.alpha_acid_pct),
    ]


def cmd_list(args: argparse.Namespace) -> int:
    store = load_store(resolve_data_file(args))
    ensure_authenticated(store)
    glyphs = glyphs_for(sys.stdout)
    filters = build_filters(args)
    matched = apply_filters(store.bales, filters)

    if getattr(args, "as_json", False):
        print(
            json.dumps(
                {
                    "path": store.path,
                    "count": len(matched),
                    "total_bales": len(store.bales),
                    "filters": {
                        "kiln_batch": filters.kiln_batch or None,
                        "bine_row": filters.bine_row or None,
                    },
                    "bales": [bale.to_record() for bale in matched],
                },
                indent=2,
            )
        )
        return EXIT_OK

    if len(store.bales) > PROGRESS_THRESHOLD:
        print("Reading %s..." % store.path, file=sys.stderr)

    if not store.bales:
        print("No bales recorded yet. Add one with: hopbalecount add")
        return EXIT_OK
    if not matched:
        print("No bales match kiln-batch/bine-row filter")
        print("Filter used: %s (%s in store)" % (filters.describe(), format_count(len(store.bales))))
        return EXIT_OK

    for line in render_table(LIST_HEADERS, [bale_row(bale) for bale in matched], LIST_ALIGNS, glyphs):
        print(line)
    print()
    footer = format_count(len(matched))
    if filters.describe():
        footer += " (filter: %s)" % filters.describe()
    print(footer)
    return EXIT_OK


def cmd_summary(args: argparse.Namespace) -> int:
    store = load_store(resolve_data_file(args))
    ensure_authenticated(store)
    glyphs = glyphs_for(sys.stdout)
    per_batch, grand = summarise(store.bales)

    if getattr(args, "as_json", False):
        print(
            json.dumps(
                {
                    "path": store.path,
                    "batches": [
                        {
                            "kiln_batch_id": key,
                            "bale_count": totals.bale_count,
                            "total_weight_kg": round(totals.total_weight_kg, 2),
                            "alpha_mass_kg": round(totals.alpha_mass_kg, 2),
                            "weighted_avg_alpha_pct": round(totals.weighted_avg_alpha_pct, 2),
                        }
                        for key, totals in per_batch
                    ],
                    "grand_total": {
                        "bale_count": grand.bale_count,
                        "total_weight_kg": round(grand.total_weight_kg, 2),
                        "alpha_mass_kg": round(grand.alpha_mass_kg, 2),
                        "weighted_avg_alpha_pct": round(grand.weighted_avg_alpha_pct, 2),
                    },
                },
                indent=2,
            )
        )
        return EXIT_OK

    if len(store.bales) > PROGRESS_THRESHOLD:
        print("Reading %s..." % store.path, file=sys.stderr)

    if not store.bales:
        print("No bales to summarise yet.")
        return EXIT_OK

    rows = [
        [
            key,
            str(totals.bale_count),
            format_kg(totals.total_weight_kg),
            format_kg(totals.alpha_mass_kg),
            format_pct(totals.weighted_avg_alpha_pct),
        ]
        for key, totals in per_batch
    ]
    total_row = [
        "GRAND TOTAL",
        str(grand.bale_count),
        format_kg(grand.total_weight_kg),
        format_kg(grand.alpha_mass_kg),
        format_pct(grand.weighted_avg_alpha_pct),
    ]
    for line in render_table(SUMMARY_HEADERS, rows, SUMMARY_ALIGNS, glyphs, total_row=total_row):
        print(line)
    print(
        "alpha mass = sum(weight x alpha%%) / 100 %s weighted alpha = sum(weight x alpha%%) / sum(weight)"
        % glyphs.dot,
        file=sys.stderr,
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# add / session commands
# ---------------------------------------------------------------------------
def cmd_add(args: argparse.Namespace) -> int:
    glyphs = glyphs_for(sys.stdout)
    raw: Dict[str, Optional[str]] = {
        "bine_row": getattr(args, "bine_row", None),
        "harvest_weight_kg": getattr(args, "weight_kg", None),
        "kiln_batch_id": getattr(args, "kiln_batch", None),
        "alpha_acid_pct": getattr(args, "alpha_pct", None),
    }
    # Flags are validated up front so a bad flag fails fast, before any read.
    parsed, problems = validate_flags(raw)
    if problems:
        for problem in problems:
            print("Error: %s" % problem, file=sys.stderr)
        return EXIT_USAGE

    store = load_store(resolve_data_file(args))
    ensure_authenticated(store)

    interactive = len(parsed) < len(FIELD_ORDER)
    if interactive and not store.bales:
        print("Store is empty %s this will be %s" % (glyphs.dash, next_bale_id(store.bales)))
    fields = collect_interactive_fields(parsed, glyphs)

    # The id is derived only after the whole store loaded successfully (D3).
    bale_id = next_bale_id(store.bales)
    bale = Bale(
        bale_id=bale_id,
        bine_row=str(fields["bine_row"]),
        harvest_weight_kg=float(str(fields["harvest_weight_kg"])),
        kiln_batch_id=str(fields["kiln_batch_id"]),
        alpha_acid_pct=float(str(fields["alpha_acid_pct"])),
        created_at=utc_now(),
    )
    store.bales.append(bale)
    save_store(store)
    print("Added %s to kiln %s" % (bale_id, bale.kiln_batch_id))
    return EXIT_OK


def cmd_session(args: argparse.Namespace) -> int:
    store = load_store(resolve_data_file(args))
    if getattr(args, "status", False):
        return print_status(store)
    outcome = ensure_authenticated(store)
    if outcome == "verified":
        print("Passphrase verified.")
    return EXIT_OK


def print_status(store: Store) -> int:
    exists = os.path.exists(store.path)
    print("Data file : %s%s" % (store.path, "" if exists else " (not created yet)"))
    print("Schema    : %d" % SCHEMA_VERSION)
    if store.auth:
        salt = bytes.fromhex(str(store.auth.get("salt")))
        print(
            "Passphrase: set (%s, %s iterations, %d-byte salt)"
            % (store.auth.get("algo"), store.auth.get("iterations"), len(salt))
        )
    else:
        print("Passphrase: not set")
    print("Bales     : %d" % len(store.bales))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
KNOWN_COMMANDS = ("session", "add", "list", "summary")
VALUE_TAKING_GLOBAL_OPTIONS = ("--data-file",)

EXAMPLES = """\
examples:
  # record a bale from flags (scriptable, no prompts)
  hopbalecount add --bine-row R-12 --weight-kg 12.5 --kiln-batch KB-07 --alpha-pct 6.4

  # read the bales back, narrowed to one kiln batch
  hopbalecount list --kiln-batch kb-07

  # per-kiln-batch alpha mass and weighted alpha percent
  hopbalecount summary

exit codes:
  0 success                              1 unexpected error
  2 invalid input or usage               3 unreadable or invalid JSON data file
  4 missing or unsupported schema version  5 authentication failure
"""


class HopArgumentParser(argparse.ArgumentParser):
    """argparse with HopBaleCount's documented error wording and exit code 2."""

    def error(self, message: str) -> None:
        self.exit(EXIT_USAGE, "Error: %s. Try --help.\n" % message.rstrip("."))


def add_no_color_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="disable the accent colour",
    )


def add_data_file_option(parser: argparse.ArgumentParser, *, suppress: bool) -> None:
    parser.add_argument(
        "--data-file",
        metavar="PATH",
        default=argparse.SUPPRESS if suppress else None,
        help="path to the JSON data store (default: %s, or $%s)"
        % (DEFAULT_DATA_FILE, DATA_FILE_ENV),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = HopArgumentParser(
        prog=PROGRAM,
        description=(
            "Record hop bales (bine row, harvest weight, drying kiln batch, alpha acid) "
            "and report per-kiln-batch alpha mass. One JSON store, no network, no server."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="%s %s" % (PROGRAM, __version__))
    parser.add_argument("--no-color", action="store_true", help="disable the accent colour")
    add_data_file_option(parser, suppress=False)

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    session_parser = subparsers.add_parser(
        "session",
        help="create or verify the operator passphrase",
        description="Create the local operator passphrase on first run, or verify it.",
    )
    session_parser.add_argument(
        "--status",
        action="store_true",
        help="show store path, schema, passphrase state and bale count (no passphrase needed)",
    )
    add_no_color_option(session_parser)
    add_data_file_option(session_parser, suppress=True)
    session_parser.set_defaults(handler=cmd_session)

    add_parser = subparsers.add_parser(
        "add",
        help="record one bale",
        description=(
            "Record one bale. Supply all four flags for scripted entry, or omit any of "
            "them to be prompted for the missing fields."
        ),
    )
    add_parser.add_argument("--bine-row", metavar="ROW", help="bine row label, 1-40 characters")
    add_parser.add_argument(
        "--weight-kg", metavar="KG", help="harvest weight in kg, greater than 0 and at most 10000"
    )
    add_parser.add_argument(
        "--kiln-batch", metavar="BATCH", help="drying kiln batch id, 1-40 characters"
    )
    add_parser.add_argument("--alpha-pct", metavar="PCT", help="alpha acid percent, 0 to 100")
    add_no_color_option(add_parser)
    add_data_file_option(add_parser, suppress=True)
    add_parser.set_defaults(handler=cmd_add)

    list_parser = subparsers.add_parser(
        "list",
        help="list recorded bales",
        description="Print every stored bale as an aligned table, optionally narrowed by filter.",
    )
    list_parser.add_argument(
        "--kiln-batch", metavar="BATCH", help="case-insensitive kiln batch substring"
    )
    list_parser.add_argument("--bine-row", metavar="ROW", help="case-insensitive bine row substring")
    list_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="machine-readable JSON output"
    )
    add_no_color_option(list_parser)
    add_data_file_option(list_parser, suppress=True)
    list_parser.set_defaults(handler=cmd_list)

    summary_parser = subparsers.add_parser(
        "summary",
        help="per-kiln-batch and grand-total alpha summary",
        description=(
            "One row per drying kiln batch plus a GRAND TOTAL row. "
            "alpha mass = sum(weight_kg x alpha_pct / 100); "
            "weighted alpha = sum(weight_kg x alpha_pct) / sum(weight_kg)."
        ),
    )
    summary_parser.add_argument(
        "--json", dest="as_json", action="store_true", help="machine-readable JSON output"
    )
    add_no_color_option(summary_parser)
    add_data_file_option(summary_parser, suppress=True)
    summary_parser.set_defaults(handler=cmd_summary)

    return parser


def first_command_token(argv: Sequence[str]) -> Optional[str]:
    """Find the subcommand token, skipping global options and their values."""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in VALUE_TAKING_GLOBAL_OPTIONS:
            index += 2
            continue
        if token.startswith("--data-file="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Line buffering keeps prompts, accepted-value echoes and results in order
    # even when stdout is a pipe.
    with contextlib.suppress(AttributeError, OSError):
        sys.stdout.reconfigure(line_buffering=True)
    try:
        command = first_command_token(arguments)
        if command is not None and command not in KNOWN_COMMANDS:
            print("Error: unknown command '%s'. Try --help." % command, file=sys.stderr)
            return EXIT_USAGE

        parser = build_parser()
        args = parser.parse_args(arguments)
        if getattr(args, "command", None) is None:
            parser.print_help()
            return EXIT_OK
        if getattr(args, "no_color", False):
            os.environ["NO_COLOR"] = "1"
        handler = getattr(args, "handler", None)
        if handler is None:
            parser.print_help()
            return EXIT_OK
        return int(handler(args))
    except HopError as exc:
        print("Error: %s" % exc.message, file=sys.stderr)
        return exc.exit_code
    except SystemExit as exc:  # argparse --help / --version / usage errors
        if isinstance(exc.code, int):
            return exc.code
        return EXIT_OK if exc.code is None else EXIT_ERROR
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_OK
    except KeyboardInterrupt:
        print("Error: interrupted", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - last resort, never a raw traceback
        print("Error: unexpected failure: %s" % exc, file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
