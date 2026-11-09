#!/usr/bin/env bash
# HopBaleCount - non-interactive end-to-end demo.
#
# Runs the real CLI against a scratch store in ./.demo, with no prompts and no
# network, and prints a transcript of every command surface: the passphrase
# gate, add (flags and prompts), list (plain, filtered, no match), summary and
# the documented error exits 2 / 3 / 4 / 5.
#
# Usage:  bash demo.sh
set -uo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
DATA="./.demo/hopbales.json"
DEMO_PASSPHRASE="ceniusdemo"

run() {
  printf '\n$ %s\n' "$*"
  "$@"
  local status=$?
  [ "$status" -ne 0 ] && printf '(exit %d)\n' "$status"
  return 0
}

cli() { "$PYTHON" hopbalecount.py "$@"; }

rm -rf ./.demo
mkdir -p ./.demo

printf 'HopBaleCount %s - bale weights, kiln batches and alpha mass\n' \
  "$("$PYTHON" -c 'import hopbalecount; print(hopbalecount.__version__)')"
printf 'Data file for this demo: %s\n' "$DATA"

run "$PYTHON" hopbalecount.py --help

run "$PYTHON" hopbalecount.py --data-file "$DATA" session --status

printf '\n$ printf "<passphrase>\\n<passphrase>\\n" | %s hopbalecount.py --data-file %s session\n' \
  "$PYTHON" "$DATA"
printf '%s\n%s\n' "$DEMO_PASSPHRASE" "$DEMO_PASSPHRASE" | cli --data-file "$DATA" session
printf '(passphrase set for the demo store - only its salted PBKDF2 hash is stored)\n'

export HOPBALECOUNT_PASSPHRASE="$DEMO_PASSPHRASE"

run "$PYTHON" hopbalecount.py --data-file "$DATA" add \
  --bine-row R-12 --weight-kg 14.60 --kiln-batch KB-07 --alpha-pct 6.4
run "$PYTHON" hopbalecount.py --data-file "$DATA" add \
  --bine-row R-12 --weight-kg 11.25 --kiln-batch KB-07 --alpha-pct 5.9
run "$PYTHON" hopbalecount.py --data-file "$DATA" add \
  --bine-row R-14 --weight-kg 12.10 --kiln-batch KB-09 --alpha-pct 8.2
run "$PYTHON" hopbalecount.py --data-file "$DATA" add \
  --bine-row R-15 --weight-kg 9.80 --kiln-batch KB-09 --alpha-pct 7.1
run "$PYTHON" hopbalecount.py --data-file "$DATA" add \
  --bine-row R-18 --weight-kg 13.40 --kiln-batch KB-07 --alpha-pct 6.8

printf '\n$ printf "R-21\\n10.75\\nKB-11\\n5.5\\n" | %s hopbalecount.py --data-file %s add\n' \
  "$PYTHON" "$DATA"
printf 'R-21\n10.75\nKB-11\n5.5\n' | cli --data-file "$DATA" add

run "$PYTHON" hopbalecount.py --data-file "$DATA" list
run "$PYTHON" hopbalecount.py --data-file "$DATA" list --kiln-batch kb-07
run "$PYTHON" hopbalecount.py --data-file "$DATA" list --kiln-batch kb-07 --bine-row r-12
run "$PYTHON" hopbalecount.py --data-file "$DATA" list --bine-row zz
run "$PYTHON" hopbalecount.py --data-file "$DATA" summary
run "$PYTHON" hopbalecount.py --data-file "$DATA" session --status

printf '\n-- validation: a rejected field (exit 2, nothing written) --\n'
run "$PYTHON" hopbalecount.py --data-file "$DATA" add \
  --bine-row R-30 --weight-kg 0 --kiln-batch KB-07 --alpha-pct 6.0
run "$PYTHON" hopbalecount.py --data-file "$DATA" add \
  --bine-row R-30 --weight-kg 12 --kiln-batch KB-07 --alpha-pct 140

printf '\n-- guards: a rejected load leaves the data file untouched --\n'
printf '{"schema_version": 1, "bales": [' > ./.demo/corrupt.json
run "$PYTHON" hopbalecount.py --data-file ./.demo/corrupt.json list
printf '{"schema_version": 2, "bales": []}\n' > ./.demo/v2.json
run "$PYTHON" hopbalecount.py --data-file ./.demo/v2.json list
printf '{"bales": []}\n' > ./.demo/noversion.json
run "$PYTHON" hopbalecount.py --data-file ./.demo/noversion.json list

printf '\n-- gate: three wrong passphrases exit 5 --\n'
printf '\n$ HOPBALECOUNT_PASSPHRASE=wrong-passphrase %s hopbalecount.py --data-file %s list\n' \
  "$PYTHON" "$DATA"
HOPBALECOUNT_PASSPHRASE=wrong-passphrase "$PYTHON" hopbalecount.py --data-file "$DATA" list
printf '(exit %d)\n' "$?"

printf '\n-- empty store: the empty-state lines --\n'
run "$PYTHON" hopbalecount.py --data-file ./.demo/empty.json session
run "$PYTHON" hopbalecount.py --data-file ./.demo/empty.json list
run "$PYTHON" hopbalecount.py --data-file ./.demo/empty.json summary

printf '\n-- a brand new store: prompted entry for all four fields --\n'
printf '\n$ printf "R-01\\n10.5\\nKB-01\\n4.5\\n" | %s hopbalecount.py --data-file %s add\n' \
  "$PYTHON" ./.demo/fresh.json
printf 'R-01\n10.5\nKB-01\n4.5\n' | cli --data-file ./.demo/fresh.json add

printf '\nDemo complete. The store it built is at %s\n' "$DATA"
exit 0
