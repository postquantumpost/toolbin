#!/usr/bin/env python3

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pi_multicast as multicast


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[2] / "tmp"
        temporary_root.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.history_file = self.home / "pi_multicast_history.json"
        self.lock_file = self.home / "pi_multicast_history.json.lock"
        self.now = multicast.time.time()
        self.record = {
            "timestamp": self.now,
            "data": ["test", "00:11:22:33:44:55", "::1", "127.0.0.1"],
        }

    def make_app(self, gui=False):
        with (
            patch.object(multicast.Path, "home", return_value=self.home),
            patch.object(
                multicast, "get_network_interfaces", return_value=[],
            ),
        ):
            return multicast.MulticastApp("test", False, gui)

    def test_missing_history(self):
        self.assertEqual(self.make_app().history, [])
        self.assertFalse(self.history_file.exists())

    def test_valid_history_is_deduplicated_and_bounded(self):
        records = [
            {
                "timestamp": self.now - 20 + index,
                "data": [str(index), "mac", "::1", "127.0.0.1"],
            }
            for index in range(20)
        ]
        records.append(dict(records[-1], timestamp=self.now))
        self.history_file.write_text(json.dumps(records), encoding="utf-8")
        history = self.make_app().history
        self.assertEqual(len(history), multicast.history_length)
        self.assertEqual(history[0]["timestamp"], self.now - 16)
        self.assertEqual(history[-1]["timestamp"], self.now)

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
                app.record_packet(self.now, self.record["data"])
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
        app.record_packet(self.now, self.record["data"])
        self.assertEqual(json.loads(self.history_file.read_text()), [self.record])
        self.assertEqual(set(self.home.iterdir()), {self.history_file, self.lock_file})

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
        self.assertEqual(set(self.home.iterdir()), {self.history_file, self.lock_file})

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
        self.assertEqual(set(self.home.iterdir()), {self.history_file, self.lock_file})

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
        self.assertEqual(set(self.home.iterdir()), {self.history_file, self.lock_file})

    def test_record_packet_merges_updates_from_other_app_instances(self):
        first_app = self.make_app()
        second_app = self.make_app()
        first_record = [*self.record["data"]]
        second_record = ["other", "00:11:22:33:44:66", "::2", "192.0.2.2"]

        first_app.record_packet(self.now - 1, first_record)
        second_app.record_packet(self.now, second_record)

        saved = json.loads(self.history_file.read_text(encoding="utf-8"))
        self.assertEqual([record["data"] for record in saved], [first_record, second_record])

    def test_record_packet_prunes_expired_future_duplicates_and_oldest(self):
        app = self.make_app()
        data = ["incoming", "00:11:22:33:44:55", "::1", "127.0.0.1"]
        records = [
            {"timestamp": self.now - 1, "data": data},
            {
                "timestamp": self.now - multicast.HISTORY_MAX_AGE_SECONDS - 1,
                "data": ["expired", "mac-expired", "::1", "127.0.0.1"],
            },
            {
                "timestamp": self.now + 1,
                "data": ["future", "mac-future", "::1", "127.0.0.1"],
            },
        ]
        records.extend(
            {
                "timestamp": self.now - 30 + index,
                "data": [f"recent-{index}", f"mac-{index}", "::1", "127.0.0.1"],
            }
            for index in range(20)
        )
        self.history_file.write_text(json.dumps(records), encoding="utf-8")

        with patch.object(multicast.time, "time", return_value=self.now):
            app.record_packet(self.now, data)

        saved = json.loads(self.history_file.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), multicast.history_length)
        self.assertEqual(sum(record["data"] == data for record in saved), 1)
        self.assertEqual(saved[-1], {"timestamp": self.now, "data": data})
        self.assertFalse(any(record["data"][0] in {"expired", "future"} for record in saved))
        self.assertFalse(any(
            record["data"][0] == "recent-0" for record in saved
        ))

    def test_record_waits_for_another_process_lock_before_touching_history(self):
        app = self.make_app()
        completed = threading.Event()

        def record():
            app.record_packet(self.now, self.record["data"])
            completed.set()

        with multicast.history_file_lock(self.lock_file):
            worker = threading.Thread(target=record)
            worker.start()
            self.assertFalse(completed.wait(0.1))
            self.assertFalse(self.history_file.exists())

        worker.join(timeout=2)
        self.assertTrue(completed.is_set())
        self.assertEqual(json.loads(self.history_file.read_text()), [self.record])


class InterfaceTests(unittest.TestCase):
    def make_app(self):
        temporary_root = Path(__file__).resolve().parents[2] / "tmp"
        temporary_root.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(directory.cleanup)
        with patch.object(multicast.Path, "home", return_value=Path(directory.name)):
            return multicast.MulticastApp("test", False, False)

    def test_report_lists_all_interfaces_and_addresses(self):
        import psutil

        ipv4 = SimpleNamespace(family=socket.AF_INET, address="192.0.2.1")
        ipv4_second = SimpleNamespace(family=socket.AF_INET, address="192.0.2.2")
        ipv6 = SimpleNamespace(family=socket.AF_INET6, address="fe80::1%7")
        link = SimpleNamespace(family=psutil.AF_LINK, address="00:11:22:33:44:55")
        with (
            patch.object(psutil, "net_if_addrs", return_value={
                "v4-only": [link, ipv4, ipv4_second],
                "v6-only": [ipv6],
                "both": [ipv4, ipv6],
                "no-ip": [link],
            }),
            patch.object(psutil, "net_if_stats", return_value={
                "v4-only": SimpleNamespace(isup=True),
                "v6-only": SimpleNamespace(isup=False),
                "both": SimpleNamespace(isup=True),
            }),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            multicast.report_network_interfaces()
        self.assertEqual(
            output.getvalue(),
            "v4-only (up)\n  IPv4: 192.0.2.1, 192.0.2.2\n  IPv6: none\n"
            "v6-only (down)\n  IPv4: none\n  IPv6: fe80::1%7\n"
            "both (up)\n  IPv4: 192.0.2.1\n  IPv6: fe80::1%7\n"
            "no-ip (unknown)\n  IPv4: none\n  IPv6: none\n",
        )

    def test_report_when_no_interfaces_found(self):
        import psutil

        with (
            patch.object(psutil, "net_if_addrs", return_value={}),
            patch.object(psutil, "net_if_stats", return_value={}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            multicast.report_network_interfaces()
        self.assertEqual(output.getvalue(), "No network interfaces found.\n")

    def test_report_option_exits_before_starting_application(self):
        import psutil

        with (
            patch.object(psutil, "net_if_addrs", return_value={}),
            patch.object(psutil, "net_if_stats", return_value={}),
            patch.object(multicast.sys, "argv", ["pi_multicast.py", "--report", "-g"]),
            patch.object(multicast.threading, "Thread") as thread,
            patch.object(Path, "home", side_effect=AssertionError("app must not start")),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(multicast.__file__, run_name="__main__")
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(output.getvalue(), "No network interfaces found.\n")
        thread.assert_not_called()

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

    def test_active_single_and_dual_stack_interfaces_are_discovered(self):
        import psutil

        link = SimpleNamespace(family=psutil.AF_LINK, address="00:11:22:33:44:55")
        ipv4 = SimpleNamespace(family=socket.AF_INET, address="192.0.2.1")
        ipv6 = SimpleNamespace(family=socket.AF_INET6, address="fe80::1%7")
        with (
            patch.object(psutil, "net_if_addrs", return_value={
                "offline": [ipv4],
                "v4-only": [link, ipv4],
                "v6-only": [ipv6],
                "dual": [link, ipv4, ipv6],
                "no-ip": [link],
            }),
            patch.object(psutil, "net_if_stats", return_value={
                "offline": SimpleNamespace(isup=False),
                "v4-only": SimpleNamespace(isup=True),
                "v6-only": SimpleNamespace(isup=True),
                "dual": SimpleNamespace(isup=True),
                "no-ip": SimpleNamespace(isup=True),
            }),
            patch.object(multicast, "get_interface_index",
                         side_effect=[7, 8]) as lookup,
        ):
            self.assertEqual(
                multicast.get_network_interfaces(),
                [
                    multicast.NetworkInterface(
                        "v4-only", "00:11:22:33:44:55", "192.0.2.1", None, None
                    ),
                    multicast.NetworkInterface(
                        "v6-only", "00:00:00:00:00:00", None, "fe80::1", 7
                    ),
                    multicast.NetworkInterface(
                        "dual", "00:11:22:33:44:55", "192.0.2.1", "fe80::1", 8
                    ),
                ],
            )
            self.assertEqual(lookup.call_count, 2)

    def test_no_active_multicast_interfaces_returns_empty_list(self):
        import psutil

        with (
            patch.object(psutil, "net_if_addrs", return_value={}),
            patch.object(psutil, "net_if_stats", return_value={}),
        ):
            self.assertEqual(multicast.get_network_interfaces(), [])

    def test_failed_ipv6_index_lookup_keeps_ipv4_usable(self):
        import psutil

        addresses = [
            SimpleNamespace(family=socket.AF_INET, address="192.0.2.1"),
            SimpleNamespace(family=socket.AF_INET6, address="fe80::1%7"),
        ]
        with (
            patch.object(psutil, "net_if_addrs", return_value={"dual": addresses}),
            patch.object(psutil, "net_if_stats", return_value={
                "dual": SimpleNamespace(isup=True),
            }),
            patch.object(multicast, "get_interface_index", side_effect=OSError("missing")),
        ):
            self.assertEqual(
                multicast.get_network_interfaces(),
                [multicast.NetworkInterface(
                    "dual", "00:00:00:00:00:00", "192.0.2.1", "fe80::1", None
                )],
            )

    def test_sender_alternates_and_restarts_with_ipv6_after_configuration_change(self):
        app = self.make_app()
        ipv4 = multicast.NetworkInterface(
            "v4", "00:11:22:33:44:55", "192.0.2.1", None, None
        )
        ipv6 = multicast.NetworkInterface(
            "v6", "00:11:22:33:44:66", None, "fe80::1", 7
        )
        both = [ipv4, ipv6]

        with (
            patch.object(multicast, "get_network_interfaces", return_value=both),
            patch.object(multicast, "send_multicast_packet") as send,
        ):
            app.send_current_packet()
            app.send_current_packet()
            self.assertEqual(
                [call.args[1] for call in send.call_args_list],
                [socket.AF_INET6, socket.AF_INET],
            )

            patcher = patch.object(multicast, "get_network_interfaces", return_value=[ipv4])
            with patcher:
                app.send_current_packet()
            with patch.object(multicast, "get_network_interfaces", return_value=both):
                app.send_current_packet()

        self.assertEqual(
            [call.args[1] for call in send.call_args_list],
            [socket.AF_INET6, socket.AF_INET, socket.AF_INET, socket.AF_INET6],
        )

    def test_sender_alternates_on_a_single_dual_stack_interface(self):
        app = self.make_app()
        dual_stack = multicast.NetworkInterface(
            "dual", "00:11:22:33:44:55", "192.0.2.1", "fe80::1", 7
        )
        with (
            patch.object(multicast, "get_network_interfaces", return_value=[dual_stack]),
            patch.object(multicast, "send_multicast_packet") as send,
        ):
            app.send_current_packet()
            app.send_current_packet()

        self.assertEqual(
            [call.args[1] for call in send.call_args_list],
            [socket.AF_INET6, socket.AF_INET],
        )

    def test_sender_uses_empty_field_for_unavailable_address_family(self):
        app = self.make_app()
        ipv4 = multicast.NetworkInterface(
            "v4", "00:11:22:33:44:55", "192.0.2.1", None, None
        )
        with (
            patch.object(multicast, "get_network_interfaces", return_value=[ipv4]),
            patch.object(multicast, "send_multicast_packet") as send,
        ):
            app.send_current_packet()

        packet = send.call_args.args[2]
        self.assertEqual(
            multicast.decode_strings(packet),
            ["test", "00:11:22:33:44:55", "", "192.0.2.1"],
        )

    def test_sender_does_nothing_without_multicast_interfaces(self):
        app = self.make_app()
        with (
            patch.object(multicast, "get_network_interfaces", return_value=[]),
            patch.object(multicast, "send_multicast_packet") as send,
        ):
            app.send_current_packet()
        send.assert_not_called()
        self.assertEqual(app.last_sent_packet, "None yet")

    def test_listener_joins_both_multicast_groups_on_all_matching_interfaces(self):
        app = self.make_app()
        interfaces = [
            multicast.NetworkInterface(
                "v4", "00:11:22:33:44:55", "192.0.2.1", None, None
            ),
            multicast.NetworkInterface(
                "v6", "00:11:22:33:44:66", None, "fe80::1", 7
            ),
            multicast.NetworkInterface(
                "dual", "00:11:22:33:44:77", "192.0.2.2", "fe80::2", 8
            ),
        ]
        ipv4_socket = Mock()
        ipv6_socket = Mock()
        with patch.object(
            multicast.socket, "socket", side_effect=[ipv4_socket, ipv6_socket]
        ):
            listeners = app.create_listener_sockets(interfaces)

        self.assertEqual(set(listeners), {socket.AF_INET, socket.AF_INET6})
        ipv4_memberships = [
            call.args[2] for call in ipv4_socket.setsockopt.call_args_list
            if call.args[:2] == (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)
        ]
        ipv6_memberships = [
            call.args[2] for call in ipv6_socket.setsockopt.call_args_list
            if call.args[:2] == (socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP)
        ]
        self.assertEqual(len(ipv4_memberships), 2)
        self.assertEqual(len(ipv6_memberships), 2)

    def test_packet_sender_uses_family_specific_multicast_destination(self):
        ipv4 = multicast.NetworkInterface(
            "v4", "00:11:22:33:44:55", "192.0.2.1", None, None
        )
        ipv6 = multicast.NetworkInterface(
            "v6", "00:11:22:33:44:66", None, "fe80::1", 7
        )
        ipv4_socket = Mock()
        ipv6_socket = Mock()
        with patch.object(
            multicast.socket, "socket", side_effect=[ipv4_socket, ipv6_socket]
        ):
            multicast.send_multicast_packet(ipv4, socket.AF_INET, b"packet")
            multicast.send_multicast_packet(ipv6, socket.AF_INET6, b"packet")

        ipv4_socket.sendto.assert_called_once_with(
            b"packet", (multicast.MCAST_IPV4, multicast.PORT)
        )
        ipv6_socket.sendto.assert_called_once_with(
            b"packet", (multicast.MCAST_IPV6, multicast.PORT, 0, 7)
        )
        ipv4_socket.close.assert_called_once()
        ipv6_socket.close.assert_called_once()


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
