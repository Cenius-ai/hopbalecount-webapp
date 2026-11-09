"""F1 / F3 / F4 / F7 - add, list, filters, summary and the CLI surface."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _candidate in (str(_HERE), str(_HERE.parent)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from support import (  # noqa: E402
    bale_record,
    hbc,
    read_store,
    run_cli,
    write_store,
)

FORMULA_BALES = [
    bale_record("B0001", "R-12", 10.0, "KB-07", 5.0),
    bale_record("B0002", "R-12", 20.0, "KB-07", 8.0),
    bale_record("B0003", "R-14", 10.0, "KB-09", 10.0),
]


class CommandTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / "store.json"


class AddTests(CommandTestCase):
    def test_first_bale_persists_across_a_restart(self) -> None:
        code, out, _err = run_cli(
            "add",
            "--data-file",
            self.path,
            "--bine-row",
            "R-12",
            "--weight-kg",
            "12.5",
            "--kiln-batch",
            "KB-07",
            "--alpha-pct",
            "6.4",
        )
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("Added B0001 to kiln KB-07", out)
        self.assertIn("Passphrase set.", out)
        stored = read_store(self.path)["bales"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["bine_row"], "R-12")
        self.assertEqual(stored[0]["harvest_weight_kg"], 12.5)
        self.assertEqual(stored[0]["created_at"][-1], "Z")

        code, listing, _err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("B0001", listing)

    def test_second_bale_gets_the_next_id(self) -> None:
        write_store(self.path, bales=[bale_record("B0001")])
        code, out, _err = run_cli(
            "add",
            "--data-file",
            self.path,
            "--bine-row",
            "R-13",
            "--weight-kg",
            "8",
            "--kiln-batch",
            "KB-07",
            "--alpha-pct",
            "4",
        )
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("Added B0002 to kiln KB-07", out)
        self.assertEqual(len(read_store(self.path)["bales"]), 2)

    def test_interactive_entry_of_all_four_fields(self) -> None:
        stdin = "kiln-demo-pass\nkiln-demo-pass\nR-22\n14.75\nKB-11\n7.25\n"
        code, out, _err = run_cli("add", "--data-file", self.path, stdin=stdin, passphrase=None)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("Store is empty", out)
        self.assertIn("this will be B0001", out)
        self.assertIn("Added B0001 to kiln KB-11", out)
        self.assertEqual(read_store(self.path)["bales"][0]["alpha_acid_pct"], 7.25)

    def test_data_file_permissions_are_private(self) -> None:
        run_cli(
            "add",
            "--data-file",
            self.path,
            "--bine-row",
            "R-1",
            "--weight-kg",
            "1",
            "--kiln-batch",
            "KB-1",
            "--alpha-pct",
            "1",
        )
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


class ListTests(CommandTestCase):
    def test_two_bales_print_a_table_and_a_count_footer(self) -> None:
        write_store(self.path, bales=[bale_record("B0001"), bale_record("B0002", "R-13", 8.25, "KB-09", 5.1)])
        code, out, _err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("ID", out)
        self.assertIn("BINE ROW", out)
        self.assertIn("HARVEST WEIGHT", out)
        self.assertIn("KILN BATCH", out)
        self.assertIn("ALPHA ACID", out)
        self.assertIn("B0001", out)
        self.assertIn("B0002", out)
        self.assertIn("10.00 kg", out)
        self.assertIn("5.00%", out)
        self.assertIn("2 bales", out)

    def test_singular_footer(self) -> None:
        write_store(self.path, bales=[bale_record()])
        _code, out, _err = run_cli("list", "--data-file", self.path)
        self.assertIn("1 bale", out)

    def test_empty_state_names_the_add_command(self) -> None:
        write_store(self.path, bales=[])
        code, out, _err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("No bales recorded yet. Add one with: hopbalecount add", out)

    def test_kiln_batch_filter_is_case_insensitive(self) -> None:
        write_store(self.path, bales=FORMULA_BALES)
        code, out, _err = run_cli("list", "--data-file", self.path, "--kiln-batch", "kb-07")
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("B0001", out)
        self.assertIn("B0002", out)
        self.assertNotIn("B0003", out)
        self.assertIn("2 bales (filter: kiln-batch=kb-07)", out)

    def test_filters_combine_with_and(self) -> None:
        write_store(self.path, bales=FORMULA_BALES)
        _code, out, _err = run_cli(
            "list", "--data-file", self.path, "--kiln-batch", "kb-07", "--bine-row", "R-12"
        )
        self.assertIn("2 bales (filter: kiln-batch=kb-07, bine-row=R-12)", out)
        _code, out, _err = run_cli(
            "list", "--data-file", self.path, "--kiln-batch", "kb-09", "--bine-row", "r-12"
        )
        self.assertIn("No bales match kiln-batch/bine-row filter", out)

    def test_no_match_line_echoes_the_filter(self) -> None:
        write_store(self.path, bales=FORMULA_BALES)
        code, out, _err = run_cli("list", "--data-file", self.path, "--bine-row", "zz")
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("No bales match kiln-batch/bine-row filter", out)
        self.assertIn("bine-row=zz", out)

    def test_json_output_contract(self) -> None:
        write_store(self.path, bales=FORMULA_BALES)
        code, out, _err = run_cli("list", "--data-file", self.path, "--json")
        self.assertEqual(code, hbc.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["total_bales"], 3)
        self.assertEqual(
            sorted(payload["bales"][0]),
            ["alpha_acid_pct", "bale_id", "bine_row", "created_at", "harvest_weight_kg", "kiln_batch_id"],
        )

    def test_long_bine_row_keeps_the_columns_aligned(self) -> None:
        write_store(
            self.path,
            bales=[bale_record("B0001", "R" * 40, 12.5, "KB-07", 6.4), bale_record("B0002", "R-1", 9.0, "KB-07", 5.0)],
        )
        _code, out, _err = run_cli("list", "--data-file", self.path)
        data_lines = [line for line in out.splitlines() if line.startswith("B000")]
        self.assertEqual(len(data_lines), 2)
        self.assertEqual(data_lines[0].index("kg"), data_lines[1].index("kg"))
        self.assertEqual(data_lines[0].index("%"), data_lines[1].index("%"))


class SummaryTests(CommandTestCase):
    def test_per_batch_and_grand_total_figures(self) -> None:
        write_store(self.path, bales=FORMULA_BALES)
        code, out, _err = run_cli("summary", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("KB-07", out)
        self.assertIn("30.00 kg", out)
        self.assertIn("2.10 kg", out)
        self.assertIn("7.00%", out)
        self.assertIn("KB-09", out)
        self.assertIn("10.00 kg", out)
        self.assertIn("GRAND TOTAL", out)
        self.assertIn("3", out)
        self.assertIn("40.00 kg", out)
        self.assertIn("3.10 kg", out)
        self.assertIn("7.75%", out)

    def test_batches_appear_in_first_seen_order(self) -> None:
        write_store(self.path, bales=FORMULA_BALES)
        _code, out, _err = run_cli("summary", "--data-file", self.path)
        self.assertLess(out.index("KB-07"), out.index("KB-09"))
        self.assertLess(out.index("KB-09"), out.index("GRAND TOTAL"))

    def test_empty_state(self) -> None:
        write_store(self.path, bales=[])
        code, out, _err = run_cli("summary", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("No bales to summarise yet.", out)

    def test_summary_json_contract(self) -> None:
        write_store(self.path, bales=FORMULA_BALES)
        _code, out, _err = run_cli("summary", "--data-file", self.path, "--json")
        payload = json.loads(out)
        self.assertEqual(payload["grand_total"]["bale_count"], 3)
        self.assertEqual(payload["grand_total"]["alpha_mass_kg"], 3.1)
        self.assertEqual(payload["grand_total"]["weighted_avg_alpha_pct"], 7.75)
        self.assertEqual(payload["batches"][0]["kiln_batch_id"], "KB-07")


class FormulaTests(unittest.TestCase):
    def test_alpha_mass_and_weighted_average(self) -> None:
        bales = [hbc.Bale(**{**bale_record("B0001", "R-1", 10.0, "KB-07", 5.0)})]
        per_batch, grand = hbc.summarise(bales)
        self.assertEqual(per_batch[0][0], "KB-07")
        self.assertAlmostEqual(grand.alpha_mass_kg, 0.5)
        self.assertAlmostEqual(grand.weighted_avg_alpha_pct, 5.0)

    def test_weighted_average_is_weight_weighted_not_a_plain_mean(self) -> None:
        bales = [
            hbc.Bale("B0001", "R-1", 10.0, "KB-07", 5.0, "2025-01-01T00:00:00Z"),
            hbc.Bale("B0002", "R-2", 90.0, "KB-07", 20.0, "2025-01-02T00:00:00Z"),
        ]
        _per_batch, grand = hbc.summarise(bales)
        self.assertAlmostEqual(grand.alpha_mass_kg, 18.5)
        self.assertAlmostEqual(grand.weighted_avg_alpha_pct, 18.5)

    def test_zero_total_weight_yields_zero_average(self) -> None:
        totals = hbc._totals(1, 0.0, 0.0)
        self.assertEqual(totals.weighted_avg_alpha_pct, 0.0)
        self.assertEqual(totals.alpha_mass_kg, 0.0)

    def test_grand_total_matches_the_sum_of_batches(self) -> None:
        bales = [hbc.Bale(*row) for row in [
            ("B0001", "R-1", 12.5, "KB-07", 6.4, "2025-01-01T00:00:00Z"),
            ("B0002", "R-2", 8.25, "KB-09", 4.1, "2025-01-02T00:00:00Z"),
            ("B0003", "R-3", 22.0, "KB-07", 9.3, "2025-01-03T00:00:00Z"),
        ]]
        per_batch, grand = hbc.summarise(bales)
        self.assertAlmostEqual(sum(totals.total_weight_kg for _k, totals in per_batch), grand.total_weight_kg)
        self.assertAlmostEqual(sum(totals.alpha_mass_kg for _k, totals in per_batch), grand.alpha_mass_kg)


class CommandSurfaceTests(CommandTestCase):
    def test_help_lists_the_commands_the_data_file_option_and_examples(self) -> None:
        code, out, _err = run_cli("--help")
        self.assertEqual(code, hbc.EXIT_OK)
        for expected in ("add", "list", "summary", "session", "--data-file", "examples:"):
            self.assertIn(expected, out)
        self.assertIn("hopbalecount add --bine-row", out)
        self.assertIn("hopbalecount list", out)
        self.assertIn("hopbalecount summary", out)

    def test_bare_invocation_prints_usage_and_exits_zero(self) -> None:
        code, out, _err = run_cli()
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("usage:", out)

    def test_unknown_command(self) -> None:
        code, _out, err = run_cli("foo")
        self.assertEqual(code, hbc.EXIT_USAGE)
        self.assertIn("Error: unknown command 'foo'. Try --help.", err)

    def test_version_flag(self) -> None:
        code, out, _err = run_cli("--version")
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("hopbalecount %s" % hbc.__version__, out)


if __name__ == "__main__":
    unittest.main()
