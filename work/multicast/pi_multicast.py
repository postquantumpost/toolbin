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

# The Pi-local IPv6 Multicast Address generated earlier (this
# is a site-local multicast address) (It's the number pi.)
MCAST_IPV6 = "ff05:3141:5926:5358:9793:2384:6264:3383"
PORT = 5005
history_length = 16
HISTORY_SAVE_ATTEMPTS = 5
HISTORY_SAVE_RETRY_DELAY_SECONDS = 0.1


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


def get_network_info():
    """Dynamically finds active interface, MAC, IPv4, and IPv6 addresses."""
    import psutil

    mac_addr = "00:00:00:00:00:00"
    ipv4_addr = "127.0.0.1"
    ipv6_addr = "::1"
    if_index = 0

    stats = psutil.net_if_stats()
    lookup_errors = []
    for interface, addrs in psutil.net_if_addrs().items():
        # Skip loopback interfaces unless nothing else is available
        if 'loopback' in interface.lower() or 'lo' == interface.lower():
            continue
        if interface not in stats or not stats[interface].isup:
            continue

        has_v4 = any(a.family == socket.AF_INET for a in addrs)
        has_v6 = any(a.family == socket.AF_INET6 for a in addrs)
        if not has_v4 or not has_v6:
            continue  # Look for an active interface with an IPv4/IPv6 footprint

        # Get interface index for IPv6 multicast scoping
        try:
            if_index = get_interface_index(interface)
        except OSError as error:
            lookup_errors.append(f"{interface}: {error}")
            continue
        if if_index == 0:
            lookup_errors.append(f"{interface}: interface index is zero")
            continue

        for addr in addrs:
            if addr.family == psutil.AF_LINK:
                mac_addr = addr.address
            elif addr.family == socket.AF_INET:
                ipv4_addr = addr.address
            elif addr.family == socket.AF_INET6:
                # Avoid link-local scoped addresses for simplicity if global exists
                ipv6_addr = addr.address.split('%')[0]
        break
    else:
        details = "; ".join(lookup_errors)
        raise OSError(
            "No active IPv4/IPv6 multicast interface could be resolved"
            + (f": {details}" if details else ".")
        )

    return mac_addr, ipv6_addr, ipv4_addr, if_index

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


def deduplicate_history(history):
    """Keep the newest record for each four-field packet identity."""
    latest_by_data = {}
    for record in history:
        latest_by_data[tuple(record["data"])] = record
    return sorted(latest_by_data.values(), key=lambda record: record["timestamp"])[-history_length:]


# --- core App State ---
class MulticastApp:
    def __init__(self, machine_name, mode_logging, mode_gui):
        self.machine_name = machine_name if machine_name else "unset"
        self.logging = mode_logging
        self.gui_mode = mode_gui

        self.mac, self.ipv6, self.ipv4, self.if_index = get_network_info()
        self.received_packets = {}  # key: sender signature, value: (timestamp, data_list)
        self.history_file = Path.home() / "pi_multicast_history.json"
        self.history_lock = threading.Lock()
        self.history_error = None
        self.history_error_reported = False
        self.last_sent_packet = "None yet"

        self.root = None
        self.sent_label = None
        self.tree = None
        self.history = self.load_history()

    def load_history(self):
        """Load the most recent packet records from the user's history file."""
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

        return deduplicate_history(history)

    def save_history(self):
        """Persist the bounded packet history."""
        for attempt in range(HISTORY_SAVE_ATTEMPTS):
            try:
                with tempfile.TemporaryDirectory(
                    prefix=".pi_multicast-", dir=self.history_file.parent
                ) as directory:
                    pending = Path(directory) / self.history_file.name
                    with pending.open("w", encoding="utf-8") as file:
                        json.dump(self.history, file, ensure_ascii=False, indent=2)
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
        mac = packet_data[1]
        with self.history_lock:
            self.history.append({"timestamp": timestamp, "data": list(packet_data)})
            self.history = deduplicate_history(self.history)
            if self.gui_mode:
                # Use MAC address as unique device identifier key
                self.received_packets[mac] = (timestamp, packet_data)
            self.save_history()

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
                print(f"[*] Listening on {MCAST_IPV6}:{PORT} with logging active...")
            else:
                print(f"[*] Running in Sender-Only silent mode. Press Ctrl+C to exit.")

            # Keep main thread alive if no GUI
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] Exiting gracefully.")

    # --- Networking Threads ---
    def sender_worker(self):
        # Create IPv6 UDP Socket
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

        # Set Multicast Interface Index scope
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, self.if_index)

        # Set TTL (Hop Limit) to 15 to ensure it passes home routers if needed (Site-Local)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 15)

        while True:
            # Re-verify IPs in case they changed
            self.mac, self.ipv6, self.ipv4, _ = get_network_info()

            # Construct the payload
            payload = (
                encode_string(self.machine_name) +
                encode_string(self.mac) +
                encode_string(self.ipv6) +
                encode_string(self.ipv4)
            )

            try:
                sock.sendto(payload, (MCAST_IPV6, PORT))
                self.last_sent_packet = f"Name: {self.machine_name} | MAC: {self.mac} | IPv6: {self.ipv6} | IPv4: {self.ipv4}"
                if not self.gui_mode and self.logging:
                    print(f"[SENT] {self.last_sent_packet}")
            except Exception as e:
                if self.logging:
                    print(f"[ERROR SENDING]: {e}")

            # Sleep randomly between 60 and 300 seconds (1 to 5 minutes)
            # Change this to random.randint(5, 10) if you want to test it rapidly!
            sleep_time = random.randint(60, 300)
            time.sleep(sleep_time)

    def listener_worker(self):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Bind to the port and wildcard address
        sock.bind(('::', PORT))

        # Join the multicast group on our active interface
        mreq = socket.inet_pton(socket.AF_INET6, MCAST_IPV6) + struct.pack("I", self.if_index)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)

        while True:
            try:
                data, addr = sock.recvfrom(1024)
                parsed = decode_strings(data)

                if len(parsed) >= 4:
                    name, mac, ip6, ip4 = parsed[0], parsed[1], parsed[2], parsed[3]

                    # Only do actions if logging or GUI is turned on
                    if self.logging and not self.gui_mode:
                        print(f"[RCVD from {addr[0]}]: Name={name}, MAC={mac}, IPv6={ip6}, IPv4={ip4}")

                    timestamp = time.time()
                    packet_data = (name, mac, ip6, ip4)
                    self.record_packet(timestamp, packet_data)
            except Exception as e:
                if self.logging:
                    print(f"[ERROR RECEIVING]: {e}")

    # --- GUI Implementation ---
    def setup_gui(self):
        import tkinter as tk
        from tkinter import ttk

        self.root = tk.Tk()
        self.root.title("Pi-Local IPv6 Multicast Dashboard")
        self.root.geometry("1100x400")

        # Top Section: Last Sent Packet Info
        # sent_frame = tk.LabelFrame(self.root, text="Last Sent Packet Status (Refreshes Every 1-5 Mins)", padding=10)
        # sent_frame.pack(fill="x", padx=10, pady=5)
        sent_frame = tk.LabelFrame(self.root, text="Last Sent Packet Status (Refreshes Every 1-5 Mins)", padx=10, pady=10)
        sent_frame.pack(fill="x", padx=10, pady=5)

        self.sent_label = tk.Label(sent_frame, text="Waiting to broadcast...", fg="green", font=("Arial", 10, "bold"))
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
        self.sent_label.config(text=self.last_sent_packet)

        # Clear Table and Redraw with calculated packet age
        for row in self.tree.get_children():
            self.tree.delete(row)

        current_time = time.time()
        with self.history_lock:
            received_packets = list(self.received_packets.items())
            history = list(self.history)

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
        description="Pi-Local IPv6 Multicast Broadcaster & Receiver",
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

    args = parser.parse_args()

    if args.g and relaunch_under_pythonw():
        raise SystemExit(0)

    app = MulticastApp(machine_name=args.name, mode_logging=args.l, mode_gui=args.g)
    app.start()
