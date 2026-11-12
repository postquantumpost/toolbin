# Pi-Local IPv6 Multicast

`pi_multicast.py` periodically broadcasts this machine's name and network
addresses over IPv6 multicast, and listens for broadcasts from other machines.
Use the optional GUI to view discovered devices, the arrival time of their
latest packets, their age, and a persisted history of recent arrivals.

## Requirements

- Python 3
- `psutil`
- `tkinter` when using the GUI (often included with Python; some Linux
  distributions package it separately)
- An active network interface with IPv4 and IPv6 multicast support

Install the Python dependency:

```shell
python -m pip install psutil
```

## Windows installation

Run `Install.ps1` from PowerShell to create `C:\runhere\` if needed and copy
`pi_multicast.py` there:

```powershell
.\Install.ps1
```

If PowerShell reports an access-denied error, run PowerShell with permission to
write to `C:\`. The installer copies only the program; install `psutil` for the
Python interpreter that will run it.

## Usage

Run these commands from this directory:

```shell
python pi_multicast.py --help
python pi_multicast.py [MACHINE_NAME] [-l] [-g]
```

`MACHINE_NAME` is an optional positional argument and overrides the default.
If omitted, the program uses the `MACHINE_NAME` environment variable. If that
is unset, Windows and Linux runs use the machine hostname; if that cannot be
retrieved (or the program is running on another platform), it broadcasts the
name `unset`.

Options:

- `-h`, `--help`: Show the command-line help and exit.
- `-l`: Print sent and received packet details to standard output when the GUI
  is disabled. Without logging or the GUI, the program runs quietly.
- `-g`: Launch the dashboard showing the last sent packet and discovered
  devices, including each latest packet's arrival time and age. On Windows,
  the GUI relaunches under `pythonw.exe` when needed to hide the console. The
  GUI is optional; the program sends and listens in both modes.

The program keeps up to 16 distinct received packet records in
`pi_multicast_history.json` in the user's home directory. Records with the
same machine name, MAC, IPv6, and IPv4 values are deduplicated, retaining only
the most recent arrival. The GUI displays historical arrivals with `H`
prefixed to their age.

History saves atomically replace the previous file so interrupted or concurrent
writes cannot leave a partially written JSON document. If history contains
invalid JSON or malformed records, the program preserves the original beside
it as `pi_multicast_history.corrupt-<unique-id>.json`, warns on standard error
(or in a GUI dialog), and starts with empty history. If the backup cannot be
created, startup fails rather than risking loss of the original.

Interface selection skips disconnected adapters and requires both IPv4 and
IPv6 addresses. On Windows, friendly adapter names (such as `Ethernet 3`) are
resolved using the native Windows interface API when socket name lookup fails.

Examples:

```shell
python pi_multicast.py WorkshopPC
python pi_multicast.py WorkshopPC -l
python pi_multicast.py -g
```

The program sends to IPv6 multicast address `ff05:3141:5926:5358:9793:2384:6264:3383`
on UDP port `5005`, using a hop limit of 15. Broadcasts are sent at random
intervals of 60 to 300 seconds. Network and firewall configuration must allow
IPv6 multicast traffic on the participating interface.

## Tests

From this directory, run `python -m unittest -v test_pi_multicast`. The tests
use temporary history files, never the user's saved history. On Windows,
they also check friendly adapter names against the native interface API.
