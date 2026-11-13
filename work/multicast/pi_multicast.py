#!/usr/bin/env python3

#
# pip install psutil
# python pi_multicast.py MyLaptop -l
# python pi_multicast.py MyLaptop -g
#

import sys
import os
import json
import subprocess
import tempfile
import uuid
from pathlib import Path

os.environ["PYTHON_COLORS"] = "0"
os.environ["NO_COLOR"] = "1"

import socket
import struct
import time
import threading
import random
import argparse
import select
import errno
from contextlib import contextmanager
from typing import NamedTuple, Optional

# The Pi-local IPv6 Multicast Address generated earlier (this
# is a site-local multicast address) (It's the number pi.)
MCAST_IPV6 = "ff05:3141:5926:5358:9793:2384:6264:3383"
MCAST_IPV4 = "239.255.31.41"
PORT = 5005
history_length = 16
HISTORY_SAVE_ATTEMPTS = 5
HISTORY_SAVE_RETRY_DELAY_SECONDS = 0.1
HISTORY_MAX_AGE_SECONDS = 3 * 24 * 60 * 60


class NetworkInterface(NamedTuple):
    name: str
    mac: str
    ipv4: Optional[str]
    ipv6: Optional[str]
    index: Optional[int]


def relaunch_under_pythonw():
    """Relaunch the GUI without a console when started with python.exe."""
    if (
        os.name != "nt"
        or getattr(sys, "frozen", False)
        or Path(sys.executable).name.lower() == "pythonw.exe"
    ):
        return False

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        from tkinter import messagebox

        messagebox.showerror("Multicast Error", f"Could not find pythonw.exe beside:\n{sys.executable}")
        raise SystemExit(1)

    try:
        subprocess.Popen([str(pythonw), str(Path(__file__).resolve()), *sys.argv[1:]])
    except OSError as e:
        from tkinter import messagebox

        messagebox.showerror("Multicast Error", f"Could not start pythonw.exe:\n{e}")
        raise SystemExit(1) from e
    return True


# --- Network Helper Functions ---
def get_interface_index(interface):
    """Resolve both socket interface names and Windows friendly adapter names."""
    try:
        return socket.if_nametoindex(interface)
    except OSError:
        if os.name != "nt":
            raise

    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("iphlpapi")
    api.ConvertInterfaceAliasToLuid.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_uint64)
    ]
    api.ConvertInterfaceAliasToLuid.restype = wintypes.ULONG
    api.ConvertInterfaceLuidToIndex.argtypes = [
        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(wintypes.ULONG)
    ]
    api.ConvertInterfaceLuidToIndex.restype = wintypes.ULONG
    luid = ctypes.c_uint64()
    index = wintypes.ULONG()
    status = api.ConvertInterfaceAliasToLuid(interface, ctypes.byref(luid))
    if status:
        raise ctypes.WinError(status)
    status = api.ConvertInterfaceLuidToIndex(ctypes.byref(luid), ctypes.byref(index))
    if status:
        raise ctypes.WinError(status)
    return index.value


def get_network_interfaces():
    """Return active, non-loopback interfaces with usable multicast addresses."""
    import psutil

    stats = psutil.net_if_stats()
    interfaces = []
    for interface, addrs in psutil.net_if_addrs().items():
        if 'loopback' in interface.lower() or 'lo' == interface.lower():
            continue
        if interface not in stats or not stats[interface].isup:
            continue

        mac = next(
            (addr.address for addr in addrs if addr.family == psutil.AF_LINK),
            "00:00:00:00:00:00",
        )
        ipv4 = next(
            (addr.address for addr in addrs if addr.family == socket.AF_INET),
            None,
        )
        ipv6 = next(
            (
                addr.address.split("%")[0]
                for addr in addrs
                if addr.family == socket.AF_INET6
            ),
            None,
        )
        if ipv4 is None and ipv6 is None:
            continue

        index = None
        if ipv6 is not None:
            try:
                index = get_interface_index(interface)
            except OSError:
                index = None
            if index == 0:
                index = None

        interfaces.append(NetworkInterface(interface, mac, ipv4, ipv6, index))

    return interfaces


def send_multicast_packet(interface, family, payload):
    """Send one packet through the selected interface and address family."""
    if family == socket.AF_INET:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface.ipv4))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 15)
            sock.sendto(payload, (MCAST_IPV4, PORT))
        finally:
            sock.close()
        return

    if family == socket.AF_INET6:
        if interface.index is None:
            raise OSError(f"IPv6 multicast interface index is unavailable for {interface.name}")
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, interface.index)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 15)
            sock.sendto(payload, (MCAST_IPV6, PORT, 0, interface.index))
        finally:
            sock.close()
        return

    raise ValueError(f"Unsupported multicast address family: {family}")


def report_network_interfaces():
    """Print all discovered interfaces and their IP addresses."""
    import psutil

    interfaces = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    if not interfaces:
        print("No network interfaces found.")
        return

    for interface, addrs in interfaces.items():
        interface_stats = stats.get(interface)
        status = "unknown" if interface_stats is None else (
            "up" if interface_stats.isup else "down"
        )
        print(f"{interface} ({status})")
        for label, family in (("IPv4", socket.AF_INET), ("IPv6", socket.AF_INET6)):
            addresses = [addr.address for addr in addrs if addr.family == family]
            print(f"  {label}: {', '.join(addresses) if addresses else 'none'}")


def encode_string(s):
    """Encodes a string as 1 byte of length followed by UTF-8 content."""
    encoded = s.encode('utf-8')
    return struct.pack("B", len(encoded)) + encoded

def decode_strings(data):
    """Decodes concatenated Length-Value bytes into a list of strings."""
    strings = []
    offset = 0
    try:
        while offset < len(data):
            length = data[offset]
            offset += 1
            if offset + length > len(data):
                break
            s = data[offset:offset+length].decode('utf-8')
            strings.append(s)
            offset += length
    except Exception:
        return ["Error decoding payload"]
    return strings


@contextmanager
def history_file_lock(lock_path):
    """Hold an exclusive process-wide lock for history file operations."""
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()

            while True:
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def deduplicate_history(history):
    """Keep the newest record for each four-field packet identity."""
    latest_by_data = {}
    for record in history:
        key = tuple(record["data"])
        if key not in latest_by_data or record["timestamp"] >= latest_by_data[key]["timestamp"]:
            latest_by_data[key] = record
    return sorted(latest_by_data.values(), key=lambda record: record["timestamp"])[-history_length:]


def prune_history(history, current_time):
    """Remove expired/future records, deduplicate, and keep the newest records."""
    recent_history = [
        record for record in history
        if current_time - HISTORY_MAX_AGE_SECONDS <= record["timestamp"] <= current_time
    ]
    return deduplicate_history(recent_history)


# --- core App State ---
class MulticastApp:
    def __init__(self, machine_name, mode_logging, mode_gui):
        self.machine_name = machine_name if machine_name else "unset"
        self.logging = mode_logging
        self.gui_mode = mode_gui

        self.mac = "00:00:00:00:00:00"
        self.ipv6 = ""
        self.ipv4 = ""
        self.received_packets = {}  # key: sender signature, value: (timestamp, data_list)
        self.history_file = Path.home() / "pi_multicast_history.json"
        self.history_lock_file = self.history_file.with_name(
            self.history_file.name + ".lock"
        )
        self.history_lock = threading.Lock()
        self.history_error = None
        self.history_error_reported = False
        self.last_sent_packet = "None yet"
        self.last_sent_packets = {
            socket.AF_INET: "Waiting to broadcast...",
            socket.AF_INET6: "Waiting to broadcast...",
        }
        self.network_signature = None
        self.next_family = random.choice((socket.AF_INET, socket.AF_INET6))

        self.root = None
        self.sent_label = None
        self.tree = None
        self.history = self.load_history()

    def load_history(self):
        """Load the most recent packet records from the user's history file."""
        with self.history_lock, history_file_lock(self.history_lock_file):
            history = self.read_history_locked()
            cleaned_history = prune_history(history, time.time())
            if cleaned_history != history:
                self.write_history_locked(cleaned_history)
            return cleaned_history

    def read_history_locked(self):
        """Read and validate history while the process-wide lock is held."""
        try:
            with self.history_file.open("r", encoding="utf-8") as file:
                history = json.load(file)

            if not isinstance(history, list):
                raise ValueError("History file must contain a JSON list")

            for record in history:
                if (
                    not isinstance(record, dict)
                    or not isinstance(record.get("timestamp"), (int, float))
                    or not isinstance(record.get("data"), list)
                    or len(record["data"]) != 4
                    or not all(isinstance(value, str) for value in record["data"])
                ):
                    raise ValueError("Invalid packet record in history file")
        except FileNotFoundError:
            return []
        except ValueError as error:
            backup = self.history_file.with_name(
                f"{self.history_file.stem}.corrupt-{uuid.uuid4().hex}.json"
            )
            # Do not continue with empty history unless the original is preserved.
            self.history_file.replace(backup)
            message = (
                f"Could not load packet history: {error}\n"
                f"Original preserved at {backup}. Starting with empty history."
            )
            self.report_history_error(message)
            return []

        return history

    def save_history(self):
        """Persist the current packet history under the process-wide lock."""
        try:
            with self.history_lock, history_file_lock(self.history_lock_file):
                self.write_history_locked(self.history)
        except OSError as error:
            self.report_history_error(
                f"Could not lock packet history at {self.history_file}: {error}"
            )
            raise

    def write_history_locked(self, history):
        """Atomically persist history while the process-wide lock is held."""
        for attempt in range(HISTORY_SAVE_ATTEMPTS):
            try:
                with tempfile.TemporaryDirectory(
                    prefix=".pi_multicast-", dir=self.history_file.parent
                ) as directory:
                    pending = Path(directory) / self.history_file.name
                    with pending.open("w", encoding="utf-8") as file:
                        json.dump(history, file, ensure_ascii=False, indent=2)
                    pending.replace(self.history_file)
                return
            except OSError as error:
                if attempt == HISTORY_SAVE_ATTEMPTS - 1:
                    message = (
                        f"Could not save packet history to {self.history_file} "
                        f"after {HISTORY_SAVE_ATTEMPTS} attempts:\n{error}"
                    )
                    self.report_history_error(message)
                else:
                    time.sleep(HISTORY_SAVE_RETRY_DELAY_SECONDS)

    def report_history_error(self, message):
        if self.gui_mode:
            self.history_error = message
            if self.root and not self.history_error_reported:
                self.history_error_reported = True
                self.root.after(0, self.show_history_error, message)
        else:
            print(f"[ERROR HISTORY] {message}", file=sys.stderr)

    def show_history_error(self, message):
        from tkinter import messagebox

        messagebox.showerror("History Error", message)

    def record_packet(self, timestamp, packet_data):
        try:
            with self.history_lock, history_file_lock(self.history_lock_file):
                history = self.read_history_locked()
                history.append({"timestamp": timestamp, "data": list(packet_data)})
                self.history = prune_history(history, time.time())
                self.write_history_locked(self.history)
                if self.gui_mode:
                    # Use MAC address as unique device identifier key
                    if any(
                        record["timestamp"] == timestamp
                        and record["data"] == list(packet_data)
                        for record in self.history
                    ):
                        self.received_packets[packet_data[1]] = (timestamp, packet_data)
        except OSError as error:
            self.report_history_error(
                f"Could not update packet history at {self.history_file}: {error}"
            )
            raise

    def start(self):
        # 1. Start Listener Thread
        threading.Thread(target=self.listener_worker, daemon=True).start()

        # 2. Start Sender Thread
        threading.Thread(target=self.sender_worker, daemon=True).start()

        # 3. Handle UI or Keep-Alive Block
        if self.gui_mode:
            self.setup_gui()
        else:
            if self.logging:
                print(
                    f"[*] Listening on IPv4 {MCAST_IPV4}:{PORT} and "
                    f"IPv6 {MCAST_IPV6}:{PORT} with logging active..."
                )
            else:
                print("[*] Running quietly. Press Ctrl+C to exit.")

            # Keep main thread alive if no GUI
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] Exiting gracefully.")

    # --- Networking Threads ---
    def sender_worker(self):
        while True:
            self.send_current_packet()

            # Sleep randomly between 60 and 300 seconds (1 to 5 minutes)
            # Change this to random.randint(5, 10) if you want to test it rapidly!
            sleep_time = random.randint(60, 300)
            time.sleep(sleep_time)

    def network_signature_for(self, interfaces):
        return tuple(sorted(
            (
                interface.name,
                interface.ipv4 or "",
                interface.ipv6 or "",
                interface.index or 0,
            )
            for interface in interfaces
        ))

    def send_current_packet(self):
        interfaces = get_network_interfaces()
        signature = self.network_signature_for(interfaces)
        if signature != self.network_signature:
            self.network_signature = signature
            self.next_family = random.choice((socket.AF_INET, socket.AF_INET6))

        ipv4_interfaces = [interface for interface in interfaces if interface.ipv4]
        ipv6_interfaces = [interface for interface in interfaces if interface.ipv6]
        if ipv4_interfaces and ipv6_interfaces:
            family = self.next_family
            self.next_family = (
                socket.AF_INET if family == socket.AF_INET6 else socket.AF_INET6
            )
        elif ipv6_interfaces:
            family = socket.AF_INET6
        elif ipv4_interfaces:
            family = socket.AF_INET
        else:
            return

        candidates = ipv4_interfaces
        if family == socket.AF_INET6:
            indexed_interfaces = [
                candidate for candidate in ipv6_interfaces if candidate.index is not None
            ]
            candidates = indexed_interfaces or ipv6_interfaces
        interface = candidates[0]
        self.mac = interface.mac
        self.ipv6 = interface.ipv6 or ""
        self.ipv4 = interface.ipv4 or ""
        payload = (
            encode_string(self.machine_name)
            + encode_string(self.mac)
            + encode_string(self.ipv6)
            + encode_string(self.ipv4)
        )

        try:
            send_multicast_packet(interface, family, payload)
            family_name = "IPv6" if family == socket.AF_INET6 else "IPv4"
            packet_details = (
                f"Name: {self.machine_name} | MAC: {self.mac} | "
                f"IPv6: {self.ipv6} | IPv4: {self.ipv4}"
            )
            self.last_sent_packet = f"{family_name} | {packet_details}"
            self.last_sent_packets[family] = packet_details
            if not self.gui_mode and self.logging:
                print(f"[SENT] {self.last_sent_packet}")
        except OSError as error:
            if self.logging:
                print(f"[ERROR SENDING]: {error}")

    def report_network_error(self, message):
        if self.logging and not self.gui_mode:
            print(f"[ERROR NETWORK]: {message}")

    def create_listener_sockets(self, interfaces):
        listeners = {}
        family_interfaces = {
            socket.AF_INET: [interface for interface in interfaces if interface.ipv4],
            socket.AF_INET6: [
                interface for interface in interfaces
                if interface.ipv6 and interface.index is not None
            ],
        }

        for family, family_interfaces_for_socket in family_interfaces.items():
            if not family_interfaces_for_socket:
                continue

            sock = None
            try:
                sock = socket.socket(family, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET:
                    sock.bind(("", PORT))
                else:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    sock.bind(("::", PORT))

                joined = False
                for interface in family_interfaces_for_socket:
                    try:
                        if family == socket.AF_INET:
                            membership = (
                                socket.inet_aton(MCAST_IPV4)
                                + socket.inet_aton(interface.ipv4)
                            )
                            sock.setsockopt(
                                socket.IPPROTO_IP,
                                socket.IP_ADD_MEMBERSHIP,
                                membership,
                            )
                        else:
                            membership = (
                                socket.inet_pton(socket.AF_INET6, MCAST_IPV6)
                                + struct.pack("I", interface.index)
                            )
                            sock.setsockopt(
                                socket.IPPROTO_IPV6,
                                socket.IPV6_JOIN_GROUP,
                                membership,
                            )
                        joined = True
                    except OSError as error:
                        self.report_network_error(
                            f"Could not join {interface.name} "
                            f"{'IPv4' if family == socket.AF_INET else 'IPv6'} multicast: "
                            f"{error}"
                        )

                if joined:
                    listeners[family] = sock
                else:
                    sock.close()
            except OSError as error:
                if sock is not None:
                    sock.close()
                self.report_network_error(
                    f"Could not start "
                    f"{'IPv4' if family == socket.AF_INET else 'IPv6'} listener: {error}"
                )

        return listeners

    def listener_worker(self):
        listeners = {}
        active_signature = None
        while True:
            try:
                interfaces = get_network_interfaces()
            except OSError as error:
                self.report_network_error(f"Could not inspect network interfaces: {error}")
                time.sleep(1)
                continue

            signature = self.network_signature_for(interfaces)
            if signature != active_signature:
                for sock in listeners.values():
                    sock.close()
                listeners = self.create_listener_sockets(interfaces)
                active_signature = signature

            if not listeners:
                time.sleep(1)
                continue

            try:
                readable, _, _ = select.select(list(listeners.values()), [], [], 1)
            except OSError as error:
                self.report_network_error(f"Could not wait for multicast packets: {error}")
                time.sleep(1)
                continue

            for sock in readable:
                try:
                    data, addr = sock.recvfrom(1024)
                    parsed = decode_strings(data)
                    if len(parsed) < 4:
                        continue

                    if self.logging and not self.gui_mode:
                        print(
                            f"[RCVD from {addr[0]}]: Name={parsed[0]}, MAC={parsed[1]}, "
                            f"IPv6={parsed[2]}, IPv4={parsed[3]}"
                        )

                    timestamp = time.time()
                    packet_data = tuple(parsed[:4])
                    self.record_packet(timestamp, packet_data)
                except OSError as error:
                    if self.logging:
                        print(f"[ERROR RECEIVING]: {error}")

    # --- GUI Implementation ---
    def setup_gui(self):
        import tkinter as tk
        from tkinter import ttk

        self.root = tk.Tk()
        self.root.title("Pi-Local IPv4/IPv6 Multicast Dashboard")
        self.root.geometry("1100x400")

        # Top Section: Last Sent Packet Info
        # sent_frame = tk.LabelFrame(self.root, text="Last Sent Packet Status (Refreshes Every 1-5 Mins)", padding=10)
        # sent_frame.pack(fill="x", padx=10, pady=5)
        sent_frame = tk.LabelFrame(self.root, text="Last Sent Packet Status (Refreshes Every 1-5 Mins)", padx=10, pady=10)
        sent_frame.pack(fill="x", padx=10, pady=5)

        self.sent_label = tk.Label(
            sent_frame, text=self.sent_packet_status(), fg="green",
            font=("Arial", 10, "bold"),
        )
        self.sent_label.pack(anchor="w")

        # Bottom Section: Received Packets Table
        # rcvd_frame = tk.LabelFrame(self.root, text="Discovered Devices (Received Packets)", padding=10)
        rcvd_frame = tk.LabelFrame(self.root, text="Discovered Devices (Received Packets)", padx=10, pady=10)
        rcvd_frame.pack(fill="both", expand=True, padx=10, pady=5)

        columns = ("name", "mac", "ipv6", "ipv4", "age", "arrival")
        self.tree = ttk.Treeview(rcvd_frame, columns=columns, show="headings")
        self.tree.heading("name", text="Machine Name")
        self.tree.heading("mac", text="MAC Address")
        self.tree.heading("ipv6", text="IPv6 Address")
        self.tree.heading("ipv4", text="IPv4 Address")
        self.tree.heading("age", text="Age (s)")
        self.tree.heading("arrival", text="Arrival Time")

        self.tree.column("mac", width=150, minwidth=150, stretch=False, anchor="center")
        self.tree.column("ipv4", width=120, minwidth=120, stretch=False, anchor="center")
        self.tree.column("arrival", width=160, minwidth=160, stretch=False, anchor="center")
        self.tree.column("age", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True)

        if self.history_error and not self.history_error_reported:
            self.history_error_reported = True
            self.root.after(0, self.show_history_error, self.history_error)

        # Start loop to refresh GUI element values continuously
        self.update_gui_loop()
        self.root.mainloop()

    def update_gui_loop(self):
        # Update Sent text
        self.sent_label.config(text=self.sent_packet_status())

        # Clear Table and Redraw with calculated packet age
        for row in self.tree.get_children():
            self.tree.delete(row)

        current_time = time.time()
        with self.history_lock:
            received_packets = list(self.received_packets.items())
            history = list(self.history)

        received_packets.sort(key=lambda item: item[1][0], reverse=True)
        history.sort(key=lambda record: record["timestamp"], reverse=True)

        current_data = {tuple(data) for _, (_, data) in received_packets}
        for mac, (timestamp, data) in received_packets:
            age = int(current_time - timestamp)
            name, mac_addr, ip6, ip4 = data
            arrival = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
            self.tree.insert("", "end", values=(name, mac_addr, ip6, ip4, f"{age}s", arrival))

        for record in history:
            timestamp = record["timestamp"]
            name, mac_addr, ip6, ip4 = record["data"]
            if tuple(record["data"]) in current_data:
                continue
            age = int(current_time - timestamp)
            arrival = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
            self.tree.insert("", "end", values=(name, mac_addr, ip6, ip4, f"H {age}s", arrival))

        # Refresh every 1 second
        self.root.after(1000, self.update_gui_loop)

    def sent_packet_status(self):
        return "\n".join((
            f"IPv4 | {self.last_sent_packets[socket.AF_INET]}",
            f"IPv6 | {self.last_sent_packets[socket.AF_INET6]}",
        ))


def get_default_machine_name():
    """Resolve the default broadcast name from configuration or the host."""
    machine_name = os.getenv("MACHINE_NAME")
    if machine_name:
        return machine_name

    if sys.platform == "linux" or os.name == "nt":
        try:
            return socket.gethostname() or "unset"
        except OSError:
            pass

    return "unset"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pi-Local IPv4/IPv6 Multicast Broadcaster & Receiver",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python pi_multicast.py MyLaptop\n"
            "  python pi_multicast.py -l\n"
            "  python pi_multicast.py -g"
        ),
    )
    parser.add_argument(
        "name",
        nargs="?",
        default=get_default_machine_name(),
        help="Machine name to broadcast (overrides the default from the environment or host)",
    )
    parser.add_argument("-l", action="store_true", help="Log output packets directly to standard out")
    parser.add_argument("-g", action="store_true", help="Launch Graphical User Interface dashboard tracking network age")
    parser.add_argument("--report", action="store_true",
                        help="List network interfaces and IP addresses, then exit")

    args = parser.parse_args()

    if args.report:
        report_network_interfaces()
        raise SystemExit(0)

    if args.g and relaunch_under_pythonw():
        raise SystemExit(0)

    app = MulticastApp(machine_name=args.name, mode_logging=args.l, mode_gui=args.g)
    app.start()
