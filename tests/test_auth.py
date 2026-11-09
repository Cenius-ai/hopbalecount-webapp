"""F5 / T3 - the local operator passphrase gate."""

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

ADD_FLAGS = (
    "--bine-row",
    "R-12",
    "--weight-kg",
    "12.5",
    "--kiln-batch",
    "KB-07",
    "--alpha-pct",
    "6.4",
)


class PassphraseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.path = self.dir / "store.json"


class FirstRunCreationTests(PassphraseTestCase):
    def test_first_run_creates_a_salted_hash_and_continues_into_add(self) -> None:
        code, out, err = run_cli("add", "--data-file", self.path, *ADD_FLAGS)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("Passphrase set.", out)
        self.assertIn("Added B0001 to kiln KB-07", out)
        document = read_store(self.path)
        auth = document["auth"]
        self.assertEqual(auth["algo"], hbc.PBKDF2_ALGO)
        self.assertEqual(auth["iterations"], hbc.PBKDF2_ITERATIONS)
        self.assertEqual(len(bytes.fromhex(auth["salt"])), hbc.SALT_BYTES)
        self.assertEqual(len(bytes.fromhex(auth["hash"])), 32)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(DEMO_PASSPHRASE, text)

    def test_first_run_confirmation_flow_from_stdin(self) -> None:
        code, out, _err = run_cli(
            "session", "--data-file", self.path, stdin="longenough\nlongenough\n", passphrase=None
        )
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("Passphrase set.", out)
        self.assertNotIn("longenough", self.path.read_text(encoding="utf-8"))

    def test_retries_after_a_mismatched_confirmation(self) -> None:
        stdin = "longenough\nshort\nlongenough\nlongenough\n"
        code, out, err = run_cli("session", "--data-file", self.path, stdin=stdin, passphrase=None)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("passphrases did not match", err)
        self.assertIn("Passphrase set.", out)

    def test_too_short_passphrase_is_refused(self) -> None:
        code, _out, err = run_cli("session", "--data-file", self.path, passphrase="short")
        self.assertEqual(code, hbc.EXIT_AUTH)
        self.assertIn("at least 8 characters", err)
        self.assertFalse(self.path.exists())

    def test_too_short_passphrase_from_stdin_reprompts(self) -> None:
        stdin = "abcabc\nlongenough\nlongenough\n"
        code, out, err = run_cli("session", "--data-file", self.path, stdin=stdin, passphrase=None)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("Error: passphrase must be at least 8 characters", err)
        self.assertIn("Passphrase set.", out)


class VerificationTests(PassphraseTestCase):
    def test_correct_passphrase_reaches_the_list(self) -> None:
        write_store(self.path, bales=[bale_record()])
        code, out, err = run_cli("list", "--data-file", self.path)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("B0001", out)
        self.assertIn("1 bale", out)
        self.assertNotIn(DEMO_PASSPHRASE, out + err)

    def test_three_wrong_passphrases_exit_five_and_change_nothing(self) -> None:
        before = write_store(self.path, bales=[bale_record()])
        code, out, err = run_cli("list", "--data-file", self.path, passphrase="not-the-passphrase")
        self.assertEqual(code, hbc.EXIT_AUTH)
        self.assertIn("Error: incorrect passphrase (1 of 3)", err)
        self.assertIn("Error: incorrect passphrase (2 of 3)", err)
        self.assertIn("Error: incorrect passphrase (3 of 3)", err)
        self.assertEqual(out, "")
        self.assertEqual(self.path.read_bytes(), before)

    def test_stdin_attempts_are_counted(self) -> None:
        write_store(self.path, bales=[bale_record()])
        code, _out, err = run_cli(
            "list", "--data-file", self.path, stdin="wrong-one\nwrong-two\n" + DEMO_PASSPHRASE + "\n", passphrase=None
        )
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("incorrect passphrase (2 of 3)", err)

    def test_failure_message_does_not_leak_the_store_path(self) -> None:
        write_store(self.path, bales=[bale_record()])
        _code, _out, err = run_cli("list", "--data-file", self.path, passphrase="nope-nope")
        self.assertNotIn(str(self.path), err)

    def test_corrupt_file_is_reported_before_any_prompt(self) -> None:
        before = write_raw(self.path, "not json")
        code, _out, err = run_cli("list", "--data-file", self.path, passphrase=None, stdin="")
        self.assertEqual(code, hbc.EXIT_BAD_JSON)
        self.assertNotIn("incorrect passphrase", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_add_requires_the_passphrase_before_writing(self) -> None:
        before = write_store(self.path)
        code, out, _err = run_cli("add", "--data-file", self.path, *ADD_FLAGS, passphrase="bad-passphrase")
        self.assertEqual(code, hbc.EXIT_AUTH)
        self.assertNotIn("Added", out)
        self.assertEqual(self.path.read_bytes(), before)

    def test_session_status_needs_no_passphrase(self) -> None:
        write_store(self.path, bales=[bale_record()])
        code, out, _err = run_cli("session", "--data-file", self.path, "--status", passphrase=None)
        self.assertEqual(code, hbc.EXIT_OK)
        self.assertIn("Passphrase: set", out)
        self.assertIn("Bales     : 1", out)


if __name__ == "__main__":
    unittest.main()
