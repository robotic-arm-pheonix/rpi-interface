#!/usr/bin/env python3
import time
import glob
import errno
import serial
from evdev import InputDevice, ecodes, list_devices

BAUD = 115200
SEND_HZ = 30.0  # send rate

STEP_DEG = 3    # D-pad step size for wrist angles

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
    # Prefer obvious controller names
    for d in devs:
        name = (d.name or "").lower()
        if any(k in name for k in ["controller", "gamepad", "xbox", "sony", "dualshock", "8bitdo"]):
            return d
    # Fallback: anything with ABS axes
    for d in devs:
        caps = d.capabilities().get(ecodes.EV_ABS, [])
        if caps:
            return d
    return None

def get_abs_ranges(pad: InputDevice):
    # best-effort axis min/max
    abs_info = {}
    try:
        for code, info in pad.absinfo.items():
            abs_info[code] = info
    except Exception:
        pass
    return abs_info

def read_events_nonblocking(pad: InputDevice):
    """
    Read all pending events without crashing when none are available.
    """
    try:
        return pad.read()
    except BlockingIOError:
        return []
    except OSError as e:
        if e.errno == errno.EAGAIN:
            return []
        raise

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
    time.sleep(2.0)  # UNO resets on serial open

    # Angles: [Root, ArmA1, ArmB, WristA, WristB, Gripper]
    angles = [90, 90, 90, 90, 90, 90]

    abs_state = {}
    key_state = {}

    abs_info = get_abs_ranges(pad)

    last_send = 0.0
    period = 1.0 / SEND_HZ

    # Optional exclusive access (prevents other apps from reading controller)
    try:
        pad.grab()
    except Exception:
        # Not fatal; continue without grab
        pass

    try:
        while True:
            # Read pending events (non-blocking, safe)
            events = read_events_nonblocking(pad)

            for ev in events:
                if ev.type == ecodes.EV_ABS:
                    abs_state[ev.code] = ev.value
                elif ev.type == ecodes.EV_KEY:
                    key_state[ev.code] = ev.value

            now = time.time()
            if now - last_send >= period:
                last_send = now

                # --- map inputs to angles ---

                # Left stick X -> Root
                if ecodes.ABS_X in abs_state:
                    v = abs_state[ecodes.ABS_X]
                    info = abs_info.get(ecodes.ABS_X)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[0] = int(map_range(v, in_min, in_max, 0, 180))

                # Left stick Y -> Arm A1 (invert so up increases)
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

                # D-pad -> Wrist A/B (hat)
                if ecodes.ABS_HAT0Y in abs_state:
                    haty = abs_state[ecodes.ABS_HAT0Y]  # -1 up, +1 down
                    if haty != 0:
                        angles[3] = clamp(angles[3] + (-haty * STEP_DEG), 0, 180)

                if ecodes.ABS_HAT0X in abs_state:
                    hatx = abs_state[ecodes.ABS_HAT0X]  # -1 left, +1 right
                    if hatx != 0:
                        angles[4] = clamp(angles[4] + (hatx * STEP_DEG), 0, 180)

                # Triggers -> Gripper (ABS_Z / ABS_RZ commonly)
                lt = abs_state.get(ecodes.ABS_Z, None)
                rt = abs_state.get(ecodes.ABS_RZ, None)
                if lt is not None or rt is not None:
                    def norm(code, val):
                        info = abs_info.get(code)
                        mn, mx = (info.min, info.max) if info else (0, 255)
                        if mx == mn:
                            return 0.0
                        return clamp((val - mn) / (mx - mn), 0.0, 1.0)

                    lt_n = norm(ecodes.ABS_Z, lt) if lt is not None else 0.0
                    rt_n = norm(ecodes.ABS_RZ, rt) if rt is not None else 0.0

                    grip = map_range(rt_n - lt_n, -1.0, 1.0, 0, 180)
                    angles[5] = int(clamp(grip, 0, 180))

                # Final clamp
                angles = [clamp(a, 0, 180) for a in angles]

                # Send to Arduino
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
