# Using HopBaleCount

Four command surfaces: `session`, `add`, `list`, `summary`. Global options:
`--data-file PATH`, `--no-color`, `--version`, `--help`.

## The passphrase gate

The first data command run against a store without an `auth` block asks you to
choose the operator passphrase (minimum 8 characters, typed twice). Only a
salted PBKDF2-HMAC-SHA256 hash - algorithm, 200000 iterations, 16-byte salt,
32-byte digest - is written to the store; the passphrase itself is never stored
or echoed. Every later command prompts for it first (`getpass`, so nothing is
shown). Three wrong attempts print `Error: incorrect passphrase (3 of 3)` and
exit 5 without touching the data file.

```bash
hopbalecount session            # create on first run, otherwise verify
hopbalecount session --status   # store path, schema, passphrase state, bale count
```

`session --status` reads no secrets, so it needs no passphrase. For scripted
runs set `HOPBALECOUNT_PASSPHRASE` - when it is set there is no prompt at all.

## Recording a bale

```bash
# all four flags: fully non-interactive, exits 2 with a message if a field is bad
hopbalecount add --bine-row R-12 --weight-kg 14.6 --kiln-batch KB-07 --alpha-pct 6.4
# -> Added B0001 to kiln KB-07

# omit any flag and you are prompted for the missing fields
hopbalecount add --kiln-batch KB-07
# Bine row: R-13
#   ✓ bine row R-13
# Harvest weight (kg): 11.2
# ...
# -> Added B0002 to kiln KB-07
```

Field rules, each rejection naming the field, echoing the value received and
stating the accepted range:

| Field | Accepted |
| --- | --- |
| `--bine-row` | non-empty after trimming, at most 40 characters |
| `--weight-kg` | a number greater than 0 and at most 10000 (kilograms) |
| `--kiln-batch` | non-empty after trimming, at most 40 characters |
| `--alpha-pct` | a number from 0 to 100 (percent of bale weight) |

Interactive entry re-prompts the same field; flag entry prints every problem and
exits 2 having written nothing. Bale ids are `B` + a zero-padded 4-digit
sequence derived from the highest id already stored - and only after the whole
store loaded successfully, so a corrupt or wrong-version file never consumes an
id.

## Listing bales

```bash
hopbalecount list
hopbalecount list --kiln-batch kb-07            # 3 bales (filter: kiln-batch=kb-07)
hopbalecount list --kiln-batch kb-07 --bine-row r-12   # both filters, AND
hopbalecount list --bine-row zz                 # No bales match kiln-batch/bine-row filter
hopbalecount list --json                        # machine-readable equivalent
```

Matching is case-insensitive substring on each field and the two filters combine
with AND. An empty store prints
`No bales recorded yet. Add one with: hopbalecount add` and exits 0.

## The alpha formulas

One canonical pair, applied identically per kiln batch and to the grand total:

```
alpha_mass_kg           = sum over bales of (harvest_weight_kg x alpha_acid_pct / 100)
weighted_avg_alpha_pct  = sum over bales of (harvest_weight_kg x alpha_acid_pct)
                          / sum over bales of harvest_weight_kg
```

Both are computed from the stored raw values; two-decimal rounding happens only
at print time and never touches the store. If a batch's weight sum is 0 the
weighted average is defined as 0.00.

Worked example - `KB-07` holds 10.0 kg @ 5.0% and 20.0 kg @ 8.0%, `KB-09` holds
10.0 kg @ 10.0%:

```
KILN BATCH   BALES  TOTAL WEIGHT  ALPHA MASS  WEIGHTED ALPHA
───────────  ─────  ────────────  ──────────  ──────────────
KB-07            2      30.00 kg     2.10 kg           7.00%
KB-09            1      10.00 kg     1.00 kg          10.00%
───────────  ─────  ────────────  ──────────  ──────────────
GRAND TOTAL      3      40.00 kg     3.10 kg           7.75%
```

Batches print in first-seen order. `summary --json` returns the same numbers as
`{"batches": [...], "grand_total": {...}}`.

## The data file

```json
{
  "schema_version": 1,
  "auth": {
    "algo": "pbkdf2_hmac_sha256",
    "iterations": 200000,
    "salt": "…32 hex chars…",
    "hash": "…64 hex chars…"
  },
  "bales": [
    {
      "bale_id": "B0001",
      "bine_row": "R-12",
      "harvest_weight_kg": 14.6,
      "kiln_batch_id": "KB-07",
      "alpha_acid_pct": 6.4,
      "created_at": "2025-08-30T09:12:44Z"
    }
  ]
}
```

Every load validates the file before anything is read or written:

* invalid JSON -> `Error: <path> is not valid JSON at line N column M`, exit 3
* no `schema_version` -> `Error: data file version missing; this build supports 1`, exit 4
* another version -> `Error: unsupported data file version 2; this build supports 1`, exit 4

All three leave the file byte-for-byte unchanged. Saves go to a temp file in the
same directory, are `fsync`ed, chmodded `0600` and moved into place with
`os.replace`, so an interrupted write can never truncate the store.

## Output conventions

Results go to stdout, diagnostics and prompts to stderr, so `hopbalecount list`
pipes cleanly. Weights always print with two decimals and a `kg` unit, alpha
values with two decimals and `%`. The single accent colour (`#00908b`) is used
only for table rules and disappears when stdout is not a TTY, when `NO_COLOR` is
set, or with `--no-color` - no meaning depends on it. On a terminal that cannot
encode box-drawing characters, ASCII rules are used instead.
