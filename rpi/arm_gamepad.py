#!/usr/bin/env python3
import time
import glob
import errno
import serial
from evdev import InputDevice, ecodes, list_devices

BAUD = 115200
SEND_HZ = 30.0
STEP_DEG = 3

def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def map_range(x, in_min, in_max, out_min, out_max):
    if in_max == in_min:
        return out_min
    t = (x - in_min) / (in_max - in_min)
    return out_min + t * (out_max - out_min)

def find_arduino_port():
    candidates = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    return candidates[0] if candidates else None

def find_gamepad():
    devs = [InputDevice(p) for p in list_devices()]
    for d in devs:
        name = (d.name or "").lower()
        if any(k in name for k in ["controller", "gamepad", "xbox", "sony", "dualshock", "8bitdo"]):
            return d
    for d in devs:
        if d.capabilities().get(ecodes.EV_ABS):
            return d
    return None

def get_abs_ranges(pad: InputDevice):
    abs_info = {}
    try:
        for code, info in pad.absinfo.items():
            abs_info[code] = info
    except Exception:
        pass
    return abs_info

def drain_events_safe(pad: InputDevice, max_events=64):
    """
    Non-blocking: read up to max_events events using read_one().
    This never throws BlockingIOError when no events are available.
    """
    out = []
    for _ in range(max_events):
        try:
            ev = pad.read_one()   # returns None if no event is ready
        except OSError as e:
            if e.errno in (errno.EAGAIN, 11):
                return out
            raise
        if ev is None:
            break
        out.append(ev)
    return out

def main():
    port = find_arduino_port()
    if not port:
        raise SystemExit("Arduino serial port not found. Check /dev/ttyACM0 or /dev/ttyUSB0")

    pad = find_gamepad()
    if not pad:
        raise SystemExit("Gamepad not found. Check /dev/input/event* and permissions.")

    print(f"Using Arduino port: {port}")
    print(f"Using gamepad: {pad.path} ({pad.name})")

    ser = serial.Serial(port, BAUD, timeout=0.01)
    time.sleep(2.0)

    # [Root, ArmA1, ArmB, WristA, WristB, Gripper]
    angles = [90, 90, 90, 90, 90, 90]

    abs_state = {}
    key_state = {}
    abs_info = get_abs_ranges(pad)

    period = 1.0 / SEND_HZ
    last_send = 0.0

    # Optional exclusive access
    try:
        pad.grab()
    except Exception:
        pass

    try:
        while True:
            # Read pending events safely (non-blocking)
            events = drain_events_safe(pad, max_events=128)

            for ev in events:
                if ev.type == ecodes.EV_ABS:
                    abs_state[ev.code] = ev.value
                elif ev.type == ecodes.EV_KEY:
                    key_state[ev.code] = ev.value

            now = time.time()
            if now - last_send >= period:
                last_send = now

                # Left stick X -> Root
                if ecodes.ABS_X in abs_state:
                    v = abs_state[ecodes.ABS_X]
                    info = abs_info.get(ecodes.ABS_X)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[0] = int(map_range(v, in_min, in_max, 0, 180))

                # Left stick Y -> Arm A1 (invert)
                if ecodes.ABS_Y in abs_state:
                    v = abs_state[ecodes.ABS_Y]
                    info = abs_info.get(ecodes.ABS_Y)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[1] = int(map_range(v, in_min, in_max, 180, 0))

                # Right stick Y -> Arm B (invert)
                if ecodes.ABS_RY in abs_state:
                    v = abs_state[ecodes.ABS_RY]
                    info = abs_info.get(ecodes.ABS_RY)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[2] = int(map_range(v, in_min, in_max, 180, 0))

                # D-pad -> Wrist A/B
                if ecodes.ABS_HAT0Y in abs_state:
                    haty = abs_state[ecodes.ABS_HAT0Y]  # -1 up, +1 down
                    if haty != 0:
                        angles[3] = clamp(angles[3] + (-haty * STEP_DEG), 0, 180)

                if ecodes.ABS_HAT0X in abs_state:
                    hatx = abs_state[ecodes.ABS_HAT0X]  # -1 left, +1 right
                    if hatx != 0:
                        angles[4] = clamp(angles[4] + (hatx * STEP_DEG), 0, 180)

                # Triggers -> Gripper (ABS_Z/ABS_RZ)
                lt = abs_state.get(ecodes.ABS_Z)
                rt = abs_state.get(ecodes.ABS_RZ)
                if lt is not None or rt is not None:
                    def norm(code, val):
                        info = abs_info.get(code)
                        mn, mx = (info.min, info.max) if info else (0, 255)
                        if val is None or mx == mn:
                            return 0.0
                        return clamp((val - mn) / (mx - mn), 0.0, 1.0)

                    lt_n = norm(ecodes.ABS_Z, lt)
                    rt_n = norm(ecodes.ABS_RZ, rt)

                    grip = map_range(rt_n - lt_n, -1.0, 1.0, 0, 180)
                    angles[5] = int(clamp(grip, 0, 180))

                angles = [clamp(a, 0, 180) for a in angles]

                line = "{} {} {} {} {} {}\n".format(*angles)
                ser.write(line.encode("ascii"))

            time.sleep(0.001)

    finally:
        try:
            pad.ungrab()
        except Exception:
            pass
        ser.close()

if __name__ == "__main__":
    main()
