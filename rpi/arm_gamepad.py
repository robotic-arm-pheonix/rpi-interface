#!/usr/bin/env python3
import time
import glob
import errno
import serial
from evdev import InputDevice, ecodes, list_devices

# Match Arduino Serial.begin(...)
BAUD = 9600
SEND_HZ = 30.0

# ===== Root step size (X) =====
ROOT_STEP_DEG = 5     # <-- CHANGE THIS (degrees per button press)
ROOT_HOME = 90
ROOT_MIN = 0
ROOT_MAX = 180

# Wrist step size
WRIST_STEP_DEG = 3

DEBUG_KEYS = True   # prints key codes when you press buttons (helpful if mapping differs)
DEBUG_SEND = False  # set True to print outgoing serial line occasionally

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

def ec_name(code):
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
    print(f"BAUD: {BAUD}  ROOT_STEP_DEG: {ROOT_STEP_DEG}")

    ser = serial.Serial(port, BAUD, timeout=0.05)
    time.sleep(2.0)

    # [Root, ArmA1, ArmB, WristA, WristB, Gripper]
    angles = [ROOT_HOME, 90, 90, 90, 90, 90]

    abs_state = {}
    key_state = {}
    abs_info = get_abs_ranges(pad)

    period = 1.0 / SEND_HZ
    last_send = 0.0
    last_debug_send = 0.0

    # L1/R1 candidates (8BitDo modes may vary)
    L1_CANDIDATES = [ecodes.BTN_TL, ecodes.BTN_TL2]
    R1_CANDIDATES = [ecodes.BTN_TR, ecodes.BTN_TR2]

    # For "one step per press", we detect rising edges (0->1)
    prev_l1 = False
    prev_r1 = False

    try:
        pad.grab()
    except Exception as e:
        print("pad.grab() failed (not fatal):", e)

    try:
        while True:
            events = drain_events_safe(pad, max_events=256)

            for ev in events:
                if ev.type == ecodes.EV_ABS:
                    abs_state[ev.code] = ev.value

                elif ev.type == ecodes.EV_KEY:
                    key_state[ev.code] = ev.value
                    if DEBUG_KEYS:
                        print(f"KEY: code={ev.code} name={ec_name(ev.code)} value={ev.value}")

            # --- ROOT stepping by X degrees per press ---
            l1_pressed = any(key_state.get(code, 0) in (1, 2) for code in L1_CANDIDATES)
            r1_pressed = any(key_state.get(code, 0) in (1, 2) for code in R1_CANDIDATES)

            # Rising edge: not pressed -> pressed
            if l1_pressed and not prev_l1 and not r1_pressed:
                angles[0] = int(clamp(angles[0] - ROOT_STEP_DEG, ROOT_MIN, ROOT_MAX))

            if r1_pressed and not prev_r1 and not l1_pressed:
                angles[0] = int(clamp(angles[0] + ROOT_STEP_DEG, ROOT_MIN, ROOT_MAX))

            prev_l1 = l1_pressed
            prev_r1 = r1_pressed

            now = time.time()
            if now - last_send >= period:
                last_send = now

                # --- Arm A1 (Left stick Y) ---
                if ecodes.ABS_Y in abs_state:
                    v = abs_state[ecodes.ABS_Y]
                    info = abs_info.get(ecodes.ABS_Y)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[1] = int(map_range(v, in_min, in_max, 180, 0))

                # --- Arm B (Right stick Y) ---
                if ecodes.ABS_RY in abs_state:
                    v = abs_state[ecodes.ABS_RY]
                    info = abs_info.get(ecodes.ABS_RY)
                    in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                    angles[2] = int(map_range(v, in_min, in_max, 180, 0))

                # --- Wrist A/B (D-pad) ---
                if ecodes.ABS_HAT0Y in abs_state:
                    haty = abs_state[ecodes.ABS_HAT0Y]
                    if haty != 0:
                        angles[3] = int(clamp(angles[3] + (-haty * WRIST_STEP_DEG), 0, 180))

                if ecodes.ABS_HAT0X in abs_state:
                    hatx = abs_state[ecodes.ABS_HAT0X]
                    if hatx != 0:
                        angles[4] = int(clamp(angles[4] + (hatx * WRIST_STEP_DEG), 0, 180))

                # --- Gripper (Triggers ABS_Z/ABS_RZ) ---
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

                # Clamp all (root already clamped)
                angles[0] = int(clamp(angles[0], ROOT_MIN, ROOT_MAX))
                for i in range(1, 6):
                    angles[i] = int(clamp(angles[i], 0, 180))

                line = "{} {} {} {} {} {}\n".format(*angles)
                ser.write(line.encode("ascii"))

                if DEBUG_SEND and (now - last_debug_send) > 0.2:
                    last_debug_send = now
                    print("SEND:", line.strip())

            time.sleep(0.001)

    finally:
        try:
            pad.ungrab()
        except Exception:
            pass
        ser.close()

if __name__ == "__main__":
    main()
