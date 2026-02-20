#!/usr/bin/env python3
import time
import glob
import errno
import serial
from evdev import InputDevice, ecodes, list_devices

# IMPORTANT: set this to match Arduino Serial.begin(...)
BAUD = 9600   # <-- your Arduino code uses 9600
SEND_HZ = 30.0
STEP_DEG = 3

ROOT_HOME = 90
ROOT_MIN = 0
ROOT_MAX = 180
ROOT_SPEED_DEG_PER_SEC = 90.0
ROOT_RETURN_DEG_PER_SEC = 120.0

DEBUG = True
DEBUG_PRINT_EVERY_SEC = 0.10  # print sent line at 10 Hz

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

def drain_events_safe(pad: InputDevice, max_events=128):
    out = []
    for _ in range(max_events):
        try:
            ev = pad.read_one()
        except OSError as e:
            if e.errno in (errno.EAGAIN, 11):
                return out
            raise
        if ev is None:
            break
        out.append(ev)
    return out

def move_towards(current, target, max_step):
    if current < target:
        return min(current + max_step, target)
    if current > target:
        return max(current - max_step, target)
    return current

def ec_name(code):
    # safe name for debug
    return ecodes.KEY.get(code, ecodes.BTN.get(code, str(code)))

def main():
    port = find_arduino_port()
    if not port:
        raise SystemExit("Arduino serial port not found. Check /dev/ttyACM0 or /dev/ttyUSB0")

    pad = find_gamepad()
    if not pad:
        raise SystemExit("Gamepad not found. Check /dev/input/event* and permissions.")

    print(f"Using Arduino port: {port}")
    print(f"Using gamepad: {pad.path} ({pad.name})")
    print(f"BAUD: {BAUD}")

    ser = serial.Serial(port, BAUD, timeout=0.05)
    time.sleep(2.0)

    # [Root, ArmA1, ArmB, WristA, WristB, Gripper]
    angles = [float(ROOT_HOME), 90.0, 90.0, 90.0, 90.0, 90.0]

    abs_state = {}
    key_state = {}
    abs_info = get_abs_ranges(pad)

    period = 1.0 / SEND_HZ
    last_send = 0.0
    last_loop = time.time()
    last_debug_print = 0.0

    # L1/R1 candidates (may differ on 8BitDo depending on mode)
    L1_CANDIDATES = [ecodes.BTN_TL, ecodes.BTN_TL2]
    R1_CANDIDATES = [ecodes.BTN_TR, ecodes.BTN_TR2]

    print("L1 candidates:", [(c, ec_name(c)) for c in L1_CANDIDATES])
    print("R1 candidates:", [(c, ec_name(c)) for c in R1_CANDIDATES])

    try:
        pad.grab()
    except Exception as e:
        print("pad.grab() failed (not fatal):", e)

    try:
        while True:
            now_loop = time.time()
            dt = now_loop - last_loop
            last_loop = now_loop
            if dt <= 0:
                dt = period

            events = drain_events_safe(pad, max_events=256)

            for ev in events:
                if ev.type == ecodes.EV_ABS:
                    abs_state[ev.code] = ev.value

                elif ev.type == ecodes.EV_KEY:
                    key_state[ev.code] = ev.value

                    if DEBUG:
                        # ev.value: 1 press, 0 release, 2 hold (some controllers)
                        print(f"KEY event: code={ev.code} name={ec_name(ev.code)} value={ev.value}")

            now = time.time()
            if now - last_send >= period:
                last_send = now

                # ---- ROOT via L1/R1 ----
                # treat value 1 or 2 as pressed
                l1_pressed = any(key_state.get(code, 0) in (1, 2) for code in L1_CANDIDATES)
                r1_pressed = any(key_state.get(code, 0) in (1, 2) for code in R1_CANDIDATES)

                if l1_pressed and not r1_pressed:
                    step = ROOT_SPEED_DEG_PER_SEC * dt
                    angles[0] = clamp(angles[0] - step, ROOT_MIN, ROOT_MAX)
                elif r1_pressed and not l1_pressed:
                    step = ROOT_SPEED_DEG_PER_SEC * dt
                    angles[0] = clamp(angles[0] + step, ROOT_MIN, ROOT_MAX)
                else:
                    step = ROOT_RETURN_DEG_PER_SEC * dt
                    angles[0] = move_towards(angles[0], ROOT_HOME, step)

                # ---- other joints unchanged ----
                if ecodes.ABS_Y in abs_state:
                    v = abs_state[ecodes.ABS_Y]
                    info = abs_info.get(ecodes.ABS_Y)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[1] = map_range(v, in_min, in_max, 180, 0)

                if ecodes.ABS_RY in abs_state:
                    v = abs_state[ecodes.ABS_RY]
                    info = abs_info.get(ecodes.ABS_RY)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[2] = map_range(v, in_min, in_max, 180, 0)

                if ecodes.ABS_HAT0Y in abs_state:
                    haty = abs_state[ecodes.ABS_HAT0Y]
                    if haty != 0:
                        angles[3] = clamp(angles[3] + (-haty * STEP_DEG), 0, 180)

                if ecodes.ABS_HAT0X in abs_state:
                    hatx = abs_state[ecodes.ABS_HAT0X]
                    if hatx != 0:
                        angles[4] = clamp(angles[4] + (hatx * STEP_DEG), 0, 180)

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
                    angles[5] = grip

                # final ints
                out = [
                    int(clamp(angles[0], ROOT_MIN, ROOT_MAX)),
                    int(clamp(angles[1], 0, 180)),
                    int(clamp(angles[2], 0, 180)),
                    int(clamp(angles[3], 0, 180)),
                    int(clamp(angles[4], 0, 180)),
                    int(clamp(angles[5], 0, 180)),
                ]

                line = "{} {} {} {} {} {}\n".format(*out)
                ser.write(line.encode("ascii"))

                if DEBUG and (now - last_debug_print) >= DEBUG_PRINT_EVERY_SEC:
                    last_debug_print = now
                    print(f"SEND: {line.strip()}  (l1={l1_pressed} r1={r1_pressed})")

            time.sleep(0.001)

    finally:
        try:
            pad.ungrab()
        except Exception:
            pass
        ser.close()

if __name__ == "__main__":
    main()
