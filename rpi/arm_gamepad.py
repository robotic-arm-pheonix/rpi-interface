#!/usr/bin/env python3
import time
import glob
import serial
from evdev import InputDevice, ecodes, list_devices, categorize
import time
import errno


BAUD = 115200
SEND_HZ = 30.0  # command rate

# --- helpers ---
def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def map_range(x, in_min, in_max, out_min, out_max):
    # works with ints or floats
    if in_max == in_min:
        return out_min
    t = (x - in_min) / (in_max - in_min)
    return out_min + t * (out_max - out_min)

def find_arduino_port():
    # common Arduino Uno ports on Linux
    candidates = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    return candidates[0] if candidates else None

def find_gamepad():
    devs = [InputDevice(p) for p in list_devices()]
    # pick the first device that looks like a controller
    for d in devs:
        name = (d.name or "").lower()
        if "controller" in name or "gamepad" in name or "xbox" in name or "sony" in name or "dualshock" in name:
            return d
    # fallback: first device that has ABS axes
    for d in devs:
        caps = d.capabilities().get(ecodes.EV_ABS, [])
        if caps:
            return d
    return None

# --- main ---
def main():
    port = find_arduino_port()
    if not port:
        raise SystemExit("Arduino serial port not found. Check /dev/ttyACM0 or /dev/ttyUSB0")

    pad = find_gamepad()
    if not pad:
        raise SystemExit("Gamepad not found. Check /dev/input/event* and permissions (group 'input').")

    print(f"Using Arduino port: {port}")
    print(f"Using gamepad: {pad.path} ({pad.name})")

    ser = serial.Serial(port, BAUD, timeout=0.01)
    time.sleep(2.0)  # Arduino reset on serial open

    # Default angles: [Root, ArmA1, ArmB, WristA, WristB, Gripper]
    angles = [90, 90, 90, 90, 90, 90]

    # Track raw inputs
    abs_state = {}
    key_state = {}

    # Get abs axis ranges (min/max) if available
    abs_info = {}
    try:
        for code, info in pad.absinfo.items():
            abs_info[code] = info
    except Exception:
        pass

    # Choose common axis codes (varies by controller)
    # We'll adapt at runtime if events come in.
    # Typical:
    #  ABS_X  left stick X
    #  ABS_Y  left stick Y
    #  ABS_RX right stick X
    #  ABS_RY right stick Y
    #  ABS_Z / ABS_RZ triggers (sometimes)
    #  ABS_HAT0X / ABS_HAT0Y dpad
    #
    # If your controller uses different codes, run with printouts (see below).
    DEBUG_PRINT_EVENTS = False

    last_send = 0.0
    period = 1.0 / SEND_HZ

    # put device in non-blocking mode
    pad.grab()  # exclusive access (optional). comment out if it blocks other apps

    try:
        while True:
            # Read all pending events quickly

            try:
                events = pad.read()
            except BlockingIOError:
                events = []
            except OSError as e:
                # Just in case: treat EAGAIN like "no events"
                if e.errno == errno.EAGAIN:
                    events = []
                else:
                    raise

            for ev in events:
                if ev.type == ecodes.EV_ABS:
                    abs_state[ev.code] = ev.value
                elif ev.type == ecodes.EV_KEY:
                    key_state[ev.code] = ev.value


            # # for ev in pad.read():
            #     if ev.type == ecodes.EV_ABS:
            #         abs_state[ev.code] = ev.value
            #         if DEBUG_PRINT_EVENTS:
            #             print("ABS", ev.code, ev.value)
            #     elif ev.type == ecodes.EV_KEY:
            #         key_state[ev.code] = ev.value
            #         if DEBUG_PRINT_EVENTS:
            #             print("KEY", ev.code, ev.value)

            now = time.time()
            if now - last_send >= period:
                last_send = now

                # --- compute angles from abs_state ---

                # Left stick X -> Root
                if ecodes.ABS_X in abs_state:
                    v = abs_state[ecodes.ABS_X]
                    info = abs_info.get(ecodes.ABS_X)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[0] = int(map_range(v, in_min, in_max, 0, 180))

                # Left stick Y -> Arm A1 (invert so up = increase)
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
                    # -1 up, +1 down
                    haty = abs_state[ecodes.ABS_HAT0Y]
                    angles[3] = clamp(angles[3] + (-haty * 3), 0, 180)  # step 3 deg

                if ecodes.ABS_HAT0X in abs_state:
                    hatx = abs_state[ecodes.ABS_HAT0X]
                    angles[4] = clamp(angles[4] + (hatx * 3), 0, 180)

                # Triggers -> Gripper
                # Many controllers: ABS_Z and ABS_RZ go 0..255 (or 0..1023)
                lt = abs_state.get(ecodes.ABS_Z, None)
                rt = abs_state.get(ecodes.ABS_RZ, None)
                if lt is not None or rt is not None:
                    # normalize each to 0..1 then do rt-lt
                    def norm(code, val):
                        info = abs_info.get(code)
                        mn, mx = (info.min, info.max) if info else (0, 255)
                        return clamp((val - mn) / (mx - mn), 0.0, 1.0)

                    lt_n = norm(ecodes.ABS_Z, lt) if lt is not None else 0.0
                    rt_n = norm(ecodes.ABS_RZ, rt) if rt is not None else 0.0

                    # map [-1..1] -> [0..180]
                    grip = map_range(rt_n - lt_n, -1.0, 1.0, 0, 180)
                    angles[5] = int(clamp(grip, 0, 180))

                # Clamp all
                angles = [clamp(a, 0, 180) for a in angles]

                # Send line: "a b c d e f\n"
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
