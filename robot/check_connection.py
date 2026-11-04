"""Bounded STA connection/version check. No actuator commands or camera stream."""

import argparse
import json
import logging
import socket
import subprocess
import sys
import time


def find_rover(serial, timeout=8):
    from robomaster import config, conn

    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("0.0.0.0", config.ROBOT_BROADCAST_PORT))
        while time.monotonic() < deadline:
            listener.settimeout(min(1, deadline - time.monotonic()))
            try:
                data, address = listener.recvfrom(1024)
                received_serial = conn.get_sn_form_data(data)
            except socket.timeout:
                continue
            except UnicodeDecodeError:
                continue
            if received_serial == serial:
                return address[0]
    raise RuntimeError("Confirmed serial not discovered; check STA mode, LAN access and macOS Local Network permission")


def connect_once(serial, verbose=False):
    from robomaster import client, config, conn, logger, protocol, robot

    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)

    address = find_rover(serial)
    config.ROBOT_IP_STR = address
    rover = None
    # Establish an explicit transport first. The stock initialize() otherwise
    # falls back to a default client when route negotiation fails.
    negotiation = conn.SdkConnection()
    try:
        ok, local, remote = negotiation.request_connection(
            protocol.host2byte(9, 6), conn_type="sta", proto_type="udp", sn=serial
        )
        if not ok or remote[0] != address:
            raise RuntimeError("STA route negotiation failed; refusing default-client fallback")
        transport = conn.Connection(local, remote, protocol="udp")
        rover = robot.Robot(cli=client.Client(9, 6, transport))
        if not rover.initialize(conn_type="sta", proto_type="udp", sn=serial):
            raise RuntimeError("SDK initialization failed")
        reported_serial = rover.get_sn()
        if reported_serial != serial:
            raise RuntimeError("Device serial mismatch; no further queries issued")
        firmware = rover.get_version()
        if not firmware:
            raise RuntimeError("No firmware response received")
        result = {"serial": reported_serial, "ip": address,
                  "firmware": firmware, "connection": "sta/udp",
                  "actuator_commands": "NONE",
                  "camera_and_sensor_checks": "NOT ATTEMPTED"}
    finally:
        try:
            if rover is not None:
                rover.close()
        finally:
            negotiation.close()
    result["sdk_close"] = "RETURNED"
    print(json.dumps(result, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sn", required=True, help="Serial confirmed on the robot label/app")
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--verbose", action="store_true", help="Include SDK protocol diagnostics")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if len(args.sn) != 14 or not args.sn.isascii() or not args.sn.isalnum():
        parser.error("Serial must be 14 ASCII letters/digits")
    if args.worker:
        try:
            connect_once(args.sn, args.verbose)
            return 0
        except Exception as exc:
            print(f"Connection check FAILED: {exc}", file=sys.stderr, flush=True)
            return 1
    for index in range(args.repeat):
        print(f"Connection cycle {index + 1}/{args.repeat}", flush=True)
        try:
            command = [sys.executable, __file__, "--sn", args.sn, "--worker"]
            if args.verbose:
                command.append("--verbose")
            result = subprocess.run(
                command,
                timeout=35, capture_output=True, text=True,
            )
        except subprocess.TimeoutExpired:
            print("Timed out after 35 s; worker terminated. SDK cleanup is unconfirmed. Do not issue movement commands.", file=sys.stderr)
            return 1
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            return result.returncode
        if index + 1 < args.repeat:
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
