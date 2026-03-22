#!/usr/bin/env python3

import argparse
import re
import sys
import time
from pathlib import Path

try:
    import usb.core
    import usb.util
except ModuleNotFoundError:
    print(
        "Error: pyusb is not installed. Run 'python3 -m pip install --user -r requirements.txt' "
        "or install python3-pyusb from your package manager.",
        file=sys.stderr,
    )
    raise SystemExit(1)

from otp_parse import parse_otp, print_summary


DFU_CLASS = 0xFE
DFU_SUBCLASS = 0x01
DFU_PROTOCOL = 0x02

REQ_DNLOAD = 0x01
REQ_UPLOAD = 0x02
REQ_GETSTATUS = 0x03
REQ_CLRSTATUS = 0x04
REQ_GETSTATE = 0x05
REQ_ABORT = 0x06

CMD_SET_ADDRESS_POINTER = 0x21

OTP_START = 0x1FFF7000
OTP_SIZE = 0x400
DEFAULT_TRANSFER_SIZE = 2048

STATE_DFU_IDLE = 0x02
STATE_DFU_DNLOAD_SYNC = 0x03
STATE_DFU_DNBUSY = 0x04
STATE_DFU_DNLOAD_IDLE = 0x05
STATE_DFU_UPLOAD_IDLE = 0x09
STATE_DFU_ERROR = 0x0A

STATE_NAMES = {
    0x00: "appIDLE",
    0x01: "appDETACH",
    0x02: "dfuIDLE",
    0x03: "dfuDNLOAD-SYNC",
    0x04: "dfuDNBUSY",
    0x05: "dfuDNLOAD-IDLE",
    0x06: "dfuMANIFEST-SYNC",
    0x07: "dfuMANIFEST",
    0x08: "dfuMANIFEST-WAIT-RESET",
    0x09: "dfuUPLOAD-IDLE",
    0x0A: "dfuERROR",
}

STATUS_NAMES = {
    0x00: "OK",
    0x01: "errTARGET",
    0x02: "errFILE",
    0x03: "errWRITE",
    0x04: "errERASE",
    0x05: "errCHECK_ERASED",
    0x06: "errPROG",
    0x07: "errVERIFY",
    0x08: "errADDRESS",
    0x09: "errNOTDONE",
    0x0A: "errFIRMWARE",
    0x0B: "errVENDOR",
    0x0C: "errUSBR",
    0x0D: "errPOR",
    0x0E: "errUNKNOWN",
    0x0F: "errSTALLEDPKT",
}


class DfuError(Exception):
    pass


def get_usb_string(dev, index):
    if not index:
        return None
    try:
        return usb.util.get_string(dev, index)
    except (usb.core.USBError, ValueError):
        return None


def get_usb_serial(dev):
    serial = get_usb_string(dev, dev.iSerialNumber)
    if not serial:
        return None
    match = re.search(r"[0-9a-fA-F]{24}", serial)
    if match:
        return match.group(0)
    return serial


def iter_dfu_devices():
    try:
        devices = usb.core.find(find_all=True)
    except usb.core.NoBackendError as exc:
        raise DfuError(
            "pyusb cannot find a USB backend. Install libusb "
            "(for example libusb-1.0 on Linux or WinUSB/libusb on Windows)."
        ) from exc

    result = []
    for dev in devices:
        serial = get_usb_serial(dev)
        for cfg in dev:
            for intf in cfg:
                if intf.bInterfaceClass != DFU_CLASS:
                    continue
                if intf.bInterfaceSubClass != DFU_SUBCLASS:
                    continue
                if intf.bInterfaceProtocol != DFU_PROTOCOL:
                    continue

                result.append(
                    {
                        "dev": dev,
                        "serial": serial,
                        "vid": dev.idVendor,
                        "pid": dev.idProduct,
                        "cfg": cfg.bConfigurationValue,
                        "intf": intf.bInterfaceNumber,
                        "alt": intf.bAlternateSetting,
                        "name": get_usb_string(dev, intf.iInterface),
                    }
                )
    return result


class Stm32Dfu:
    def __init__(self, info, transfer_size):
        self.dev = info["dev"]
        self.info = info
        self.transfer_size = transfer_size

    def open(self):
        try:
            self.dev.set_configuration(self.info["cfg"])
        except usb.core.USBError:
            pass

        try:
            if self.dev.is_kernel_driver_active(self.info["intf"]):
                self.dev.detach_kernel_driver(self.info["intf"])
        except (NotImplementedError, usb.core.USBError):
            pass

        try:
            usb.util.claim_interface(self.dev, self.info["intf"])
        except usb.core.USBError as exc:
            if getattr(exc, "errno", None) == 13 or "Access denied" in str(exc):
                raise DfuError(
                    "access denied to USB DFU device. Run with sudo or add a udev rule for 0483:DF11."
                ) from exc
            raise DfuError(f"cannot claim DFU interface: {exc}") from exc

        try:
            self.dev.set_interface_altsetting(interface=self.info["intf"], alternate_setting=self.info["alt"])
        except usb.core.USBError as exc:
            raise DfuError(f"cannot select alt {self.info['alt']}: {exc}") from exc

    def close(self):
        try:
            usb.util.release_interface(self.dev, self.info["intf"])
        except usb.core.USBError:
            pass
        usb.util.dispose_resources(self.dev)

    def ctrl_out(self, request, value, data=b"", timeout=1000):
        try:
            self.dev.ctrl_transfer(0x21, request, value, self.info["intf"], data, timeout)
        except usb.core.USBError as exc:
            raise DfuError(f"USB OUT request 0x{request:02X} failed: {exc}") from exc

    def ctrl_in(self, request, value, length, timeout=1000):
        try:
            data = self.dev.ctrl_transfer(0xA1, request, value, self.info["intf"], length, timeout)
            return data.tobytes()
        except usb.core.USBError as exc:
            raise DfuError(f"USB IN request 0x{request:02X} failed: {exc}") from exc

    def get_state(self):
        return self.ctrl_in(REQ_GETSTATE, 0, 1)[0]

    def clear_status(self):
        self.ctrl_out(REQ_CLRSTATUS, 0)

    def abort(self):
        self.ctrl_out(REQ_ABORT, 0)

    def get_status(self):
        raw = self.ctrl_in(REQ_GETSTATUS, 0, 6)
        status = raw[0]
        poll_timeout = raw[1] | (raw[2] << 8) | (raw[3] << 16)
        state = raw[4]
        return status, poll_timeout, state

    def ensure_idle(self):
        state = self.get_state()
        if state == STATE_DFU_IDLE:
            return
        if state == STATE_DFU_ERROR:
            self.clear_status()
            state = self.get_state()
        if state in (STATE_DFU_DNLOAD_IDLE, STATE_DFU_UPLOAD_IDLE):
            self.abort()
            state = self.get_state()
        if state != STATE_DFU_IDLE:
            raise DfuError(f"device is not in dfuIDLE, current state={STATE_NAMES.get(state, hex(state))}")

    def wait_ready(self):
        for _ in range(100):
            status, poll_timeout, state = self.get_status()
            if poll_timeout:
                time.sleep(poll_timeout / 1000.0)
            if status != 0x00:
                raise DfuError(
                    f"DFU status error: {STATUS_NAMES.get(status, hex(status))}, "
                    f"state={STATE_NAMES.get(state, hex(state))}"
                )
            if state not in (STATE_DFU_DNBUSY, STATE_DFU_DNLOAD_SYNC):
                return state
        raise DfuError("DFU device stayed busy for too long")

    def get_supported_commands(self):
        self.ensure_idle()
        data = self.ctrl_in(REQ_UPLOAD, 0, 32)
        self.ensure_idle()
        return data

    def set_address(self, address):
        self.ensure_idle()
        payload = bytes([CMD_SET_ADDRESS_POINTER]) + address.to_bytes(4, "little")
        self.ctrl_out(REQ_DNLOAD, 0, payload)
        state = self.wait_ready()
        if state != STATE_DFU_DNLOAD_IDLE:
            raise DfuError(f"unexpected state after SET_ADDRESS_POINTER: {STATE_NAMES.get(state, hex(state))}")
        self.abort()
        self.ensure_idle()

    def upload(self, block_num, length):
        data = self.ctrl_in(REQ_UPLOAD, block_num, length)
        _, _, state = self.get_status()
        if state not in (STATE_DFU_UPLOAD_IDLE, STATE_DFU_IDLE):
            state = self.wait_ready()
            if state not in (STATE_DFU_UPLOAD_IDLE, STATE_DFU_IDLE):
                raise DfuError(f"unexpected state after upload: {STATE_NAMES.get(state, hex(state))}")
        return data

    def read_memory(self, address, size):
        self.set_address(address)
        out = bytearray()
        block_num = 2
        left = size
        while left > 0:
            chunk = min(left, self.transfer_size)
            part = self.upload(block_num, chunk)
            if len(part) != chunk:
                raise DfuError(f"short read at block {block_num}: got {len(part)} bytes, expected {chunk}")
            out.extend(part)
            left -= chunk
            block_num += 1
        self.abort()
        self.ensure_idle()
        return bytes(out)


def parse_int(value):
    return int(value, 0)


def filter_devices(devices, vid=None, pid=None, serial=None):
    out = devices
    if vid is not None:
        out = [item for item in out if item["vid"] == vid]
    if pid is not None:
        out = [item for item in out if item["pid"] == pid]
    if serial:
        out = [item for item in out if item["serial"] == serial]
    return out


def choose_alt(devices, base_info, alt):
    for item in devices:
        if item["dev"].bus != base_info["dev"].bus:
            continue
        if item["dev"].address != base_info["dev"].address:
            continue
        if item["alt"] == alt:
            return item
    raise DfuError(f"selected DFU device does not expose alt setting {alt}")


def hexdump(data, max_len=128):
    lines = []
    for offset in range(0, min(len(data), max_len), 16):
        chunk = data[offset : offset + 16]
        hex_part = " ".join(f"{byte:02X}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08X}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Read STM32WB55 OTP over STM32 USB DFU. Works with Flipper Zero and STM32WB55 boards in DFU mode."
    )
    parser.add_argument("-o", "--output", default="otp_dump.bin", help="output file path")
    parser.add_argument("--address", default=hex(OTP_START), help=f"start address, default: {hex(OTP_START)}")
    parser.add_argument("--size", default=hex(OTP_SIZE), help=f"size in bytes, default: {hex(OTP_SIZE)}")
    parser.add_argument("--transfer-size", type=int, default=DEFAULT_TRANSFER_SIZE, help="DFU transfer size")
    parser.add_argument("--serial", help="USB serial filter")
    parser.add_argument("--vid", type=parse_int, help="USB vendor ID, for example 0x0483")
    parser.add_argument("--pid", type=parse_int, help="USB product ID, for example 0xDF11")
    parser.add_argument("--index", type=int, default=0, help="device index after filtering")
    parser.add_argument("--list", action="store_true", help="list DFU interfaces and exit")
    parser.add_argument("--alt", type=int, help="use a specific DFU alternate setting")
    parser.add_argument("--probe-alts", action="store_true", help="try a small read on every DFU alt setting")
    args = parser.parse_args()

    devices = iter_dfu_devices()

    if args.list:
        if not devices:
            print("No DFU devices found")
            return 1
        for i, item in enumerate(devices):
            print(
                f"[{i}] vid=0x{item['vid']:04X} pid=0x{item['pid']:04X} serial={item['serial']} "
                f"cfg={item['cfg']} intf={item['intf']} alt={item['alt']} name={item['name'] or '-'}"
            )
        return 0

    devices = filter_devices(devices, args.vid, args.pid, args.serial)
    if not devices:
        raise DfuError("no matching DFU devices found")

    if args.probe_alts:
        probe_size = min(parse_int(args.size), 16)
        seen = set()
        for item in devices:
            key = (item["dev"].bus or -1, item["dev"].address or -1, item["alt"])
            if key in seen:
                continue
            seen.add(key)
            print(
                f"Probing alt={item['alt']} vid=0x{item['vid']:04X} pid=0x{item['pid']:04X} serial={item['serial']}"
            )
            dfu = Stm32Dfu(item, args.transfer_size)
            try:
                dfu.open()
                commands = dfu.get_supported_commands()
                if CMD_SET_ADDRESS_POINTER not in commands:
                    print("  unsupported: no STM32 set-address command")
                    continue
                data = dfu.read_memory(parse_int(args.address), probe_size)
                print(f"  OK: {data.hex()}")
            except DfuError as exc:
                print(f"  FAIL: {exc}")
            finally:
                dfu.close()
        return 0

    if args.index < 0 or args.index >= len(devices):
        raise DfuError(f"device index {args.index} is out of range, found {len(devices)} device(s)")

    device_info = devices[args.index]
    if args.alt is not None and device_info["alt"] != args.alt:
        device_info = choose_alt(devices, device_info, args.alt)

    dfu = Stm32Dfu(device_info, args.transfer_size)
    dfu.open()
    try:
        commands = dfu.get_supported_commands()
        if CMD_SET_ADDRESS_POINTER not in commands:
            raise DfuError(
                "DFU device does not advertise STM32 Set Address Pointer command. "
                "This does not look like STM32 system DFU bootloader."
            )

        address = parse_int(args.address)
        size = parse_int(args.size)
        data = dfu.read_memory(address, size)
        output = Path(args.output).expanduser().resolve()
        output.write_bytes(data)

        print(f"Read {len(data)} bytes from 0x{address:08X} to {output}")
        print(f"USB: vid=0x{device_info['vid']:04X} pid=0x{device_info['pid']:04X} serial={device_info['serial']}")
        print(
            f"DFU interface: cfg={device_info['cfg']} intf={device_info['intf']} "
            f"alt={device_info['alt']} name={device_info['name'] or '-'}"
        )
        print("Supported STM32 DFU commands:", " ".join(f"0x{value:02X}" for value in commands))
        try:
            print_summary(parse_otp(data))
        except Exception as exc:
            print(f"Parsed OTP: unavailable ({exc})")
        print("Preview:")
        print(hexdump(data))
        return 0
    finally:
        dfu.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DfuError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
