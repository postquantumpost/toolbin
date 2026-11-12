#!/usr/bin/env python3

import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pi_multicast as multicast


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[2] / "tmp"
        temporary_root.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.history_file = self.home / "pi_multicast_history.json"
        self.record = {
            "timestamp": 123.0,
            "data": ["test", "00:11:22:33:44:55", "::1", "127.0.0.1"],
        }

    def make_app(self, gui=False):
        with (
            patch.object(multicast.Path, "home", return_value=self.home),
            patch.object(
                multicast, "get_network_info",
                return_value=("00:11:22:33:44:55", "::1", "127.0.0.1", 1),
            ),
        ):
            return multicast.MulticastApp("test", False, gui)

    def test_missing_history(self):
        self.assertEqual(self.make_app().history, [])
        self.assertFalse(self.history_file.exists())

    def test_valid_history_is_deduplicated_and_bounded(self):
        records = [
            {"timestamp": index, "data": [str(index), "mac", "::1", "127.0.0.1"]}
            for index in range(20)
        ]
        records.append(dict(records[-1], timestamp=100))
        self.history_file.write_text(json.dumps(records), encoding="utf-8")
        history = self.make_app().history
        self.assertEqual(len(history), multicast.history_length)
        self.assertEqual(history[0]["timestamp"], 4)
        self.assertEqual(history[-1]["timestamp"], 100)

    def test_corrupt_history_is_preserved_and_reported(self):
        valid = json.dumps([self.record]).encode("utf-8")
        for contents in (valid + valid, valid[:-3], b"", b"\xff", b"{}", b"[{}]"):
            with self.subTest(contents=contents):
                self.history_file.write_bytes(contents)
                previous = set(self.home.glob("*.corrupt-*.json"))
                with contextlib.redirect_stderr(io.StringIO()) as errors:
                    app = self.make_app()
                backups = set(self.home.glob("*.corrupt-*.json")) - previous
                self.assertEqual(app.history, [])
                self.assertEqual(len(backups), 1)
                backup = backups.pop()
                self.assertEqual(backup.read_bytes(), contents)
                self.assertIn(str(backup), errors.getvalue())
                self.assertIn("Starting with empty history", errors.getvalue())
                self.assertFalse(self.history_file.exists())
                app.record_packet(123.0, self.record["data"])
                self.assertEqual(json.loads(self.history_file.read_text()), [self.record])
                self.assertEqual(backup.read_bytes(), contents)

    def test_gui_defers_corrupt_history_warning(self):
        self.history_file.write_text("[] trailing data", encoding="utf-8")
        app = self.make_app(gui=True)
        self.assertEqual(app.history, [])
        self.assertIn("Original preserved", app.history_error)
        self.assertFalse(app.history_error_reported)

    def test_backup_failure_does_not_discard_original(self):
        self.history_file.write_bytes(b"[] extra data")
        with patch.object(Path, "replace", side_effect=PermissionError("backup denied")):
            with self.assertRaises(PermissionError):
                self.make_app()
        self.assertEqual(self.history_file.read_bytes(), b"[] extra data")

    def test_atomic_save_and_temporary_directory_cleanup(self):
        app = self.make_app()
        app.record_packet(123.0, self.record["data"])
        self.assertEqual(json.loads(self.history_file.read_text()), [self.record])
        self.assertEqual(list(self.home.iterdir()), [self.history_file])

    def test_partial_write_does_not_damage_previous_history(self):
        self.history_file.write_text("[]", encoding="utf-8")
        app = self.make_app()

        def partial_write(history, file, **kwargs):
            file.write("[")
            raise OSError("disk full")

        with (
            patch.object(multicast.json, "dump", side_effect=partial_write) as dump,
            patch.object(multicast.time, "sleep"),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            app.save_history()
        self.assertEqual(dump.call_count, 5)
        self.assertEqual(self.history_file.read_text(), "[]")
        self.assertIn("disk full", errors.getvalue())
        self.assertEqual(list(self.home.iterdir()), [self.history_file])

    def test_replace_failure_preserves_previous_history(self):
        self.history_file.write_text("[]", encoding="utf-8")
        app = self.make_app()
        with (
            patch.object(Path, "replace", side_effect=PermissionError("replace denied")) as replace,
            patch.object(multicast.time, "sleep") as sleep,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            app.save_history()
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(self.history_file.read_text(), "[]")
        self.assertIn("replace denied", errors.getvalue())
        self.assertIn("after 5 attempts", errors.getvalue())
        self.assertEqual(errors.getvalue().count("[ERROR HISTORY]"), 1)
        self.assertEqual(list(self.home.iterdir()), [self.history_file])

    def test_save_succeeds_on_fifth_attempt(self):
        self.history_file.write_text("[]", encoding="utf-8")
        app = self.make_app()
        app.history = [self.record]
        original_replace = Path.replace
        attempts = 0

        def replace_after_failures(path, target):
            nonlocal attempts
            attempts += 1
            if attempts < 5:
                raise PermissionError("temporarily denied")
            return original_replace(path, target)

        with (
            patch.object(Path, "replace", replace_after_failures),
            patch.object(multicast.time, "sleep") as sleep,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            app.save_history()
        self.assertEqual(attempts, 5)
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(json.loads(self.history_file.read_text()), [self.record])
        self.assertEqual(list(self.home.iterdir()), [self.history_file])


class InterfaceTests(unittest.TestCase):
    def test_socket_interface_name(self):
        with patch.object(multicast.socket, "if_nametoindex", return_value=7):
            self.assertEqual(multicast.get_interface_index("eth0"), 7)

    @unittest.skipUnless(os.name == "nt", "Windows native adapter API")
    def test_windows_alias_resolves_to_existing_socket_index(self):
        import psutil

        indexes = {index for index, name in socket.if_nameindex()}
        for name in psutil.net_if_addrs():
            with self.subTest(interface=name):
                self.assertIn(multicast.get_interface_index(name), indexes)

    def test_active_dual_stack_interface_is_selected(self):
        import psutil

        addresses = [
            SimpleNamespace(family=psutil.AF_LINK, address="00:11:22:33:44:55"),
            SimpleNamespace(family=socket.AF_INET, address="192.0.2.1"),
            SimpleNamespace(family=socket.AF_INET6, address="fe80::1%7"),
        ]
        with (
            patch.object(psutil, "net_if_addrs", return_value={
                "offline": addresses, "no-index": addresses,
                "zero-index": addresses, "active": addresses,
            }),
            patch.object(psutil, "net_if_stats", return_value={
                "offline": SimpleNamespace(isup=False),
                "no-index": SimpleNamespace(isup=True),
                "zero-index": SimpleNamespace(isup=True),
                "active": SimpleNamespace(isup=True),
            }),
            patch.object(multicast, "get_interface_index",
                         side_effect=[OSError("missing"), 0, 7]) as lookup,
        ):
            self.assertEqual(
                multicast.get_network_info(),
                ("00:11:22:33:44:55", "fe80::1", "192.0.2.1", 7),
            )
            self.assertEqual(lookup.call_count, 3)

    def test_missing_interface_is_explicit_error(self):
        import psutil

        with patch.object(psutil, "net_if_addrs", return_value={}):
            with self.assertRaisesRegex(OSError, "No active IPv4/IPv6"):
                multicast.get_network_info()


class MachineNameTests(unittest.TestCase):
    def test_linux_hostname_is_used_when_environment_name_is_unset(self):
        with (
            patch.object(multicast.os, "getenv", return_value=None),
            patch.object(multicast.sys, "platform", "linux"),
            patch.object(multicast.socket, "gethostname", return_value="linux-host"),
        ):
            self.assertEqual(multicast.get_default_machine_name(), "linux-host")

    def test_linux_hostname_failure_falls_back_to_unset(self):
        with (
            patch.object(multicast.os, "getenv", return_value=None),
            patch.object(multicast.sys, "platform", "linux"),
            patch.object(multicast.socket, "gethostname", side_effect=OSError),
        ):
            self.assertEqual(multicast.get_default_machine_name(), "unset")


if __name__ == "__main__":
    unittest.main()
