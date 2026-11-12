# Pi-Local IPv6 Multicast

`pi_multicast.py` periodically broadcasts this machine's name and network
addresses over IPv6 multicast, and listens for broadcasts from other machines.
Use the optional GUI to view discovered devices and the age of their latest
packets.

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

## Usage

Run these commands from this directory:

```shell
python pi_multicast.py --help
python pi_multicast.py [MACHINE_NAME] [-l] [-g]
```

`MACHINE_NAME` is an optional positional argument. If omitted, the program uses
the `MACHINE_NAME` environment variable; if that is also unset, it broadcasts
the name `unset`.

Options:

- `-h`, `--help`: Show the command-line help and exit.
- `-l`: Print sent and received packet details to standard output when the GUI
  is disabled. Without logging or the GUI, the program runs quietly.
- `-g`: Launch the dashboard showing the last sent packet and discovered
  devices. The GUI is optional; the program sends and listens in both modes.

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
