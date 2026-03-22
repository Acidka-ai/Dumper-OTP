#!/usr/bin/env python3

import argparse
import json
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path


OTP_SIZE = 0x400

DISPLAY_IDS = {
    1: "erc",
    2: "mgg",
}

COLOR_IDS = {
    0: "unknown",
    1: "black",
    2: "white",
    3: "transparent",
}

REGION_IDS = {
    0: "unknown",
    1: "en_ru",
    2: "us_ca_au",
    3: "jp",
    4: "world",
}


def parse_otp(data):
    if len(data) < 32:
        raise ValueError(f"OTP dump is too short: {len(data)} bytes")

    magic, otp_version = struct.unpack_from("<HH", data, 0)
    timestamp = struct.unpack_from("<I", data, 4)[0]
    version = data[8]
    firmware = data[9]
    body = data[10]
    connect = data[11]
    display_id = struct.unpack_from("<I", data, 12)[0]
    color_id = data[16]
    region_id = data[17]
    raw_name = data[24:32]
    name = raw_name.split(b"\x00", 1)[0].decode("ascii", "replace").strip()

    return {
        "magic": magic,
        "magic_hex": f"0x{magic:04X}",
        "otp_version": otp_version,
        "timestamp": timestamp,
        "timestamp_iso_utc": datetime.fromtimestamp(timestamp, UTC).isoformat(),
        "version": version,
        "firmware": firmware,
        "body": body,
        "connect": connect,
        "display_id": display_id,
        "display": DISPLAY_IDS.get(display_id, f"unknown({display_id})"),
        "color_id": color_id,
        "color": COLOR_IDS.get(color_id, f"unknown({color_id})"),
        "region_id": region_id,
        "region": REGION_IDS.get(region_id, f"unknown({region_id})"),
        "name": name,
    }


def print_summary(info):
    print("Parsed OTP:")
    print(f"  magic: {info['magic_hex']}")
    print(f"  otp_version: {info['otp_version']}")
    print(f"  build_date_utc: {info['timestamp_iso_utc']}")
    print(f"  version: {info['version']}")
    print(f"  firmware: {info['firmware']}")
    print(f"  body: {info['body']}")
    print(f"  connect: {info['connect']}")
    print(f"  display_id: {info['display_id']} ({info['display']})")
    print(f"  color: {info['color']} (code {info['color_id']})")
    print(f"  region: {info['region']} (code {info['region_id']})")
    print(f"  name: {info['name'] or '-'}")


def main():
    parser = argparse.ArgumentParser(description="Parse STM32WB55/Flipper OTP dump")
    parser.add_argument("file", help="path to otp dump")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    data = Path(args.file).read_bytes()
    if len(data) != OTP_SIZE:
        print(f"Warning: expected {OTP_SIZE} bytes, got {len(data)}", file=sys.stderr)

    info = parse_otp(data)
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print_summary(info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
