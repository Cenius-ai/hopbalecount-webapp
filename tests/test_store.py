"""T2 - the versioned store: corruption guards, version guards, atomic writes."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _candidate in (str(_HERE), str(_HERE.parent)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from support import (  # noqa: E402
    DEMO_PASSPHRASE,
    bale_record,
    hbc,
    read_store,
    run_cli,
    write_raw,
    write_store,
)


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.path = self.dir / "store.json"


class CorruptFileTests(StoreTestCase):
    def test_corrupt_json_reports_line_and_column_and_leaves_file_alone(self) -> None:
        before = write_raw(self.path, '{"schema_version": 1, "bales": [')
        code, out, err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_BAD_JSON)
        self.assertIn("%s is not valid JSON at line 1 column 33" % self.path, err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(out, "")

    def test_corrupt_json_blocks_add_without_assigning_an_id(self) -> None:
        before = write_raw(self.path, "this is not json at all")
        code, out, err = run_cli(
            "add",
            "--data-file",
            self.path,
            "--bine-row",
            "R-1",
            "--weight-kg",
            "5",
            "--kiln-batch",
            "KB-07",
            "--alpha-pct",
            "5",
        )
        self.assertEqual(code, hbc.EXIT_BAD_JSON)
        self.assertIn("is not valid JSON at line 1 column 1", err)
        self.assertNotIn("Added", out)
        self.assertEqual(self.path.read_bytes(), before)

    def test_non_object_document_is_rejected(self) -> None:
        before = write_raw(self.path, "[1, 2, 3]")
        code, _out, err = run_cli("summary", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_BAD_JSON)
        self.assertIn("expected a JSON object", err)
        self.assertEqual(self.path.read_bytes(), before)


class SchemaVersionTests(StoreTestCase):
    def test_missing_schema_version(self) -> None:
        before = write_store(self.path, version="omit")
        code, _out, err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_BAD_VERSION)
        self.assertIn("Error: data file version missing; this build supports 1", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unsupported_schema_version_reports_the_version_found(self) -> None:
        before = write_store(self.path, version=2)
        code, _out, err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_BAD_VERSION)
        self.assertIn("Error: unsupported data file version 2; this build supports 1", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unsupported_version_blocks_add(self) -> None:
        before = write_store(self.path, version=9)
        code, out, err = run_cli(
            "add",
            "--data-file",
            self.path,
            "--bine-row",
            "R-1",
            "--weight-kg",
            "5",
            "--kiln-batch",
            "KB-07",
            "--alpha-pct",
            "5",
        )
        self.assertEqual(code, hbc.EXIT_BAD_VERSION)
        self.assertIn("unsupported data file version 9; this build supports 1", err)
        self.assertNotIn("Added", out)
        self.assertEqual(self.path.read_bytes(), before)

    def test_non_integer_version_is_rejected(self) -> None:
        before = write_store(self.path, version="1")
        code, _out, err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_BAD_VERSION)
        self.assertIn("this build supports 1", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_bale_record_is_rejected(self) -> None:
        before = write_store(self.path, bales=[{"bale_id": "B0001"}])
        code, _out, err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_BAD_JSON)
        self.assertIn("is not a valid HopBaleCount data file", err)
        self.assertEqual(self.path.read_bytes(), before)


class LoadAndSaveTests(StoreTestCase):
    def test_missing_file_is_an_empty_store(self) -> None:
        store = hbc.load_store(str(self.path))
        self.assertEqual(store.bales, [])
        self.assertIsNone(store.auth)
        self.assertFalse(self.path.exists())

    def test_save_is_atomic_and_private(self) -> None:
        store = hbc.load_store(str(self.path))
        store.bales.append(hbc.Bale("B0001", "R-12", 12.5, "KB-07", 6.4, "2025-08-01T09:00:00Z"))
        hbc.save_store(store)
        mode = self.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual([entry.name for entry in self.dir.iterdir()], [self.path.name])
        document = read_store(self.path)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(len(document["bales"]), 1)

    def test_failed_replace_leaves_the_old_document_intact(self) -> None:
        before = write_store(self.path, bales=[bale_record()])
        store = hbc.load_store(str(self.path))
        store.bales.append(hbc.Bale("B0002", "R-13", 8.0, "KB-07", 4.0, "2025-08-02T09:00:00Z"))
        from unittest import mock

        with mock.patch.object(hbc.os, "replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(hbc.HopError):
                hbc.save_store(store)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([entry.name for entry in self.dir.iterdir()], [self.path.name])

    def test_round_trip_preserves_values(self) -> None:
        write_store(self.path, bales=[bale_record(weight=12.5, alpha=6.4)])
        store = hbc.load_store(str(self.path))
        self.assertEqual(store.bales[0].harvest_weight_kg, 12.5)
        self.assertEqual(store.bales[0].alpha_acid_pct, 6.4)
        hbc.save_store(store)
        self.assertEqual(hbc.load_store(str(self.path)).bales[0].bine_row, "R-12")

    def test_passphrase_hash_survives_a_save(self) -> None:
        write_store(self.path, auth=True)
        store = hbc.load_store(str(self.path))
        original = dict(store.auth or {})
        store.bales.append(hbc.Bale("B0002", "R-13", 8.0, "KB-07", 4.0, "2025-08-02T09:00:00Z"))
        hbc.save_store(store)
        self.assertEqual(read_store(self.path)["auth"], original)


class BaleIdTests(unittest.TestCase):
    def test_first_id(self) -> None:
        self.assertEqual(hbc.next_bale_id([]), "B0001")

    def test_sequences_from_the_highest_existing_id(self) -> None:
        bales = [
            hbc.Bale("B0001", "R-1", 1.0, "KB-1", 5.0, "2025-01-01T00:00:00Z"),
            hbc.Bale("B0007", "R-2", 2.0, "KB-2", 5.0, "2025-01-02T00:00:00Z"),
        ]
        self.assertEqual(hbc.next_bale_id(bales), "B0008")

    def test_ignores_ids_that_are_not_sequential(self) -> None:
        bales = [hbc.Bale("X-9", "R-1", 1.0, "KB-1", 5.0, "2025-01-01T00:00:00Z")]
        self.assertEqual(hbc.next_bale_id(bales), "B0001")


if __name__ == "__main__":
    unittest.main()
