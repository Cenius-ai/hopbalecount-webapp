# Install HopBaleCount

## 1. Prerequisites

* Python 3.9 or newer on `PATH` (`python3 -V`)
* `pip` for that interpreter (`python3 -m pip -V`)
* A POSIX shell (`bash`) for the helper scripts

Nothing else: HopBaleCount has no third-party dependencies and never touches
the network at run time. Do not install system packages; none are needed.

## 2. Install the console command

```bash
bash install.sh
```

The script upgrades `pip`/`setuptools`/`wheel` when it can, installs the single
module as an editable package, prints the version it installed and **exits** -
it never starts anything long-running.

Manual equivalent:

```bash
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r requirements.txt
python3 -m pip install --no-build-isolation -e .
```

If you would rather not install anything, skip this step entirely: everything
works as `python3 hopbalecount.py <command>` from the project directory.

## 3. Create demo data (optional, idempotent)

```bash
bash demo.sh
```

`demo.sh` does not start a server either: it rebuilds `./.demo`, creates a demo
store with the passphrase `ceniusdemo`, records six bales across three kiln
batches, and prints a transcript of every command, including the guards and
error exits. Re-running it wipes and re-seeds `./.demo`.

## 4. Run

```bash
hopbalecount --help
hopbalecount --data-file ./.demo/hopbales.json list      # passphrase: ceniusdemo
```

or, without installing:

```bash
python3 hopbalecount.py --help
python3 hopbalecount.py --data-file ./.demo/hopbales.json summary
```

The default store is `./hopbales.json`; override it with `--data-file PATH` or
`HOPBALECOUNT_DATA_FILE`.

## 5. Test

```bash
python3 -m unittest discover -s tests -t .
```

`python3 -m pytest -q` runs the same 67 cases if pytest is available.

## 6. Uninstall

```bash
python3 -m pip uninstall -y hopbalecount
```

Your data file is not touched by install or uninstall.

## Troubleshooting

* `hopbalecount: command not found` - the console script is on a different
  `PATH`; use `python3 hopbalecount.py <command>` (identical behaviour), or
  `python3 -m hopbalecount <command>`.
* `Error: incorrect passphrase (3 of 3)` - the operator passphrase is wrong
  three times in a row; rerun with the right one. Nothing was modified.
* `Error: <path> is not valid JSON at line N column M` - restore the file from
  a backup. HopBaleCount never rewrites a store it could not load.
