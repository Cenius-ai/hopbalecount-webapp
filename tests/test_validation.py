"""F6 - per-field validation: ranges, echoed values and actionable messages."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _candidate in (str(_HERE), str(_HERE.parent)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from support import hbc, run_cli, write_store  # noqa: E402


class WeightTests(unittest.TestCase):
    def test_accepts_the_documented_range(self) -> None:
        self.assertEqual(hbc.parse_weight("12.5"), 12.5)
        self.assertEqual(hbc.parse_weight(" 0.01 "), 0.01)
        self.assertEqual(hbc.parse_weight("10000"), 10000.0)

    def test_zero_names_the_field_echoes_the_value_and_states_the_range(self) -> None:
        with self.assertRaises(hbc.FieldError) as caught:
            hbc.parse_weight("0")
        message = str(caught.exception)
        self.assertIn("harvest_weight_kg must be greater than 0; got 0", message)
        self.assertIn("accepted:", message)
        self.assertIn("10000", message)

    def test_negative_weight(self) -> None:
        with self.assertRaises(hbc.FieldError) as caught:
            hbc.parse_weight("-2.5")
        self.assertIn("greater than 0; got -2.5", str(caught.exception))

    def test_above_the_ceiling(self) -> None:
        with self.assertRaises(hbc.FieldError) as caught:
            hbc.parse_weight("10001")
        self.assertIn("must be at most 10000; got 10001", str(caught.exception))

    def test_not_a_number(self) -> None:
        with self.assertRaises(hbc.FieldError) as caught:
            hbc.parse_weight("banana")
        self.assertIn("harvest_weight_kg must be a number; got banana", str(caught.exception))

    def test_non_finite_numbers_are_refused(self) -> None:
        for raw in ("inf", "nan", "-inf"):
            with self.assertRaises(hbc.FieldError):
                hbc.parse_weight(raw)


class AlphaTests(unittest.TestCase):
    def test_closed_range_is_accepted(self) -> None:
        self.assertEqual(hbc.parse_alpha("0"), 0.0)
        self.assertEqual(hbc.parse_alpha("100"), 100.0)
        self.assertEqual(hbc.parse_alpha("6.4"), 6.4)

    def test_out_of_range(self) -> None:
        with self.assertRaises(hbc.FieldError) as caught:
            hbc.parse_alpha("100.1")
        self.assertIn("alpha_acid_pct must be between 0 and 100; got 100.1", str(caught.exception))
        with self.assertRaises(hbc.FieldError):
            hbc.parse_alpha("-0.1")

    def test_not_a_number(self) -> None:
        with self.assertRaises(hbc.FieldError) as caught:
            hbc.parse_alpha("high")
        self.assertIn("alpha_acid_pct must be a number; got high", str(caught.exception))


class TextFieldTests(unittest.TestCase):
    def test_trims_and_counts_characters(self) -> None:
        self.assertEqual(hbc.parse_bine_row("  R-12  "), "R-12")
        self.assertEqual(hbc.parse_bine_row("R" * 40), "R" * 40)

    def test_empty_is_named(self) -> None:
        for raw in ("", "    "):
            with self.assertRaises(hbc.FieldError) as caught:
                hbc.parse_kiln_batch(raw)
            self.assertIn("kiln_batch_id must not be empty; got ''", str(caught.exception))
            self.assertIn("1 to 40 characters", str(caught.exception))

    def test_over_forty_characters(self) -> None:
        with self.assertRaises(hbc.FieldError) as caught:
            hbc.parse_bine_row("R" * 41)
        self.assertIn("bine_row must be at most 40 characters", str(caught.exception))
        self.assertIn("41 characters", str(caught.exception))


class FlagModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / "store.json"
        self.before = write_store(self.path)

    def test_invalid_flag_exits_two_and_writes_nothing(self) -> None:
        code, out, err = run_cli(
            "add",
            "--data-file",
            self.path,
            "--bine-row",
            "R-12",
            "--weight-kg",
            "0",
            "--kiln-batch",
            "KB-07",
            "--alpha-pct",
            "6.4",
        )
        self.assertEqual(code, hbc.EXIT_USAGE)
        self.assertIn("harvest_weight_kg must be greater than 0; got 0", err)
        self.assertIn("accepted:", err)
        self.assertNotIn("Added", out)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_every_invalid_field_is_reported(self) -> None:
        code, _out, err = run_cli(
            "add",
            "--data-file",
            self.path,
            "--bine-row",
            "",
            "--weight-kg",
            "abc",
            "--kiln-batch",
            "K" * 41,
            "--alpha-pct",
            "500",
        )
        self.assertEqual(code, hbc.EXIT_USAGE)
        self.assertIn("bine_row must not be empty", err)
        self.assertIn("harvest_weight_kg must be a number", err)
        self.assertIn("kiln_batch_id must be at most 40 characters", err)
        self.assertIn("alpha_acid_pct must be between 0 and 100", err)

    def test_interactive_mode_reprompts_after_a_rejected_field(self) -> None:
        code, out, err = run_cli(
            "add",
            "--data-file",
            self.path,
            "--bine-row",
            "R-12",
            stdin="kiln-demo-pass\n0\n12.5\nKB-07\n6.4\n",
            passphrase=None,
        )
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("Error: harvest_weight_kg must be greater than 0; got 0", err)
        self.assertIn("Added B0001 to kiln KB-07", out)


if __name__ == "__main__":
    unittest.main()
