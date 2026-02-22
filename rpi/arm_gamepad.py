#!/usr/bin/env python3
import os
import time
import glob
import errno
import serial
from evdev import InputDevice, ecodes, list_devices

# =======================
# CONFIG
# =======================

BAUD = 9600
SEND_HZ = 30.0

# Root behavior
ROOT_STEP_DEG = 10      # <-- X degrees per L1/R1 press
ROOT_HOME = 90
ROOT_MIN = 0
ROOT_MAX = 180

# Wrist step size (D-pad)
WRIST_STEP_DEG = 3

# Debug
DEBUG_LIST_DEVICES = True
DEBUG_KEYS = True
DEBUG_SEND = True
PRINT_SEND_EVERY_SEC = 0.25

# If set, always use this device path (example: /dev/input/event7)
FORCED_GAMEPAD_DEV = os.environ.get("GAMEPAD_DEV", "").strip() or None

# Button mapping
L1_CODES = [ecodes.BTN_TL, ecodes.BTN_TL2]
R1_CODES = [ecodes.BTN_TR, ecodes.BTN_TR2]
HOME_CODES = [ecodes.BTN_START]     # press START -> root goes home

# Reconnect behavior
RECONNECT_SCAN_SEC = 1.0            # how often to rescan when pad missing
PAD_EVENT_DRAIN_MAX = 256           # events drained per loop

# =======================
# Helpers
# =======================

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

def ec_name(code):
    return ecodes.KEY.get(code, ecodes.BTN.get(code, str(code)))

def drain_events_safe(pad: InputDevice, max_events=128):
    out = []
    for _ in range(max_events):
        try:
            ev = pad.read_one()
        except OSError as e:
            # EAGAIN means "no events right now"
            if e.errno in (errno.EAGAIN, 11):
                return out
            # ENODEV / "No such device" happens on disconnect
            raise
        if ev is None:
            break
        out.append(ev)
    return out

def get_abs_ranges(pad: InputDevice):
    abs_info = {}
    try:
        for code, info in pad.absinfo.items():
            abs_info[code] = info
    except Exception:
        pass
    return abs_info

def device_score(dev: InputDevice) -> int:
    caps = dev.capabilities()
    score = 0
    abs_codes = caps.get(ecodes.EV_ABS, [])
    key_codes = caps.get(ecodes.EV_KEY, [])

    if abs_codes:
        score += 10
    if key_codes:
        score += 10

    common_btns = [
        ecodes.BTN_SOUTH, ecodes.BTN_EAST, ecodes.BTN_NORTH, ecodes.BTN_WEST,
        ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_START, ecodes.BTN_SELECT
    ]
    for b in common_btns:
        if b in key_codes:
            score += 3

    common_axes = [ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX, ecodes.ABS_RY, ecodes.ABS_Z, ecodes.ABS_RZ]
    for a in common_axes:
        if a in abs_codes:
            score += 2

    name = (dev.name or "").lower()
    if any(k in name for k in ["8bitdo", "xbox", "wireless controller", "gamepad", "controller"]):
        score += 5

    return score

def list_and_pick_gamepad():
    """
    Returns an InputDevice or None.
    Picks best-scoring device unless FORCED_GAMEPAD_DEV is set.
    """
    if FORCED_GAMEPAD_DEV:
        try:
            return InputDevice(FORCED_GAMEPAD_DEV)
        except Exception as e:
            print(f"[PAD] Forced device not available: {FORCED_GAMEPAD_DEV} ({e})")
            return None

    devs = []
    for p in list_devices():
        try:
            devs.append(InputDevice(p))
        except Exception:
            pass

    if not devs:
        return None

    if DEBUG_LIST_DEVICES:
        print("[PAD] ---- /dev/input devices ----")
        for d in devs:
            try:
                caps = d.capabilities()
                abs_codes = caps.get(ecodes.EV_ABS, [])
                key_codes = caps.get(ecodes.EV_KEY, [])
                print(f"[PAD] {d.path} name='{d.name}' score={device_score(d)} ABS={len(abs_codes)} KEY={len(key_codes)}")
            except Exception as e:
                print(f"[PAD] {d.path} (error reading caps: {e})")
        print("[PAD] ----------------------------")

    best = None
    best_score = -1
    for d in devs:
        try:
            s = device_score(d)
        except Exception:
            continue
        if s > best_score:
            best_score = s
            best = d

    return best

def connect_gamepad():
    """
    Keep scanning until a usable gamepad is found.
    Returns (pad, abs_info, abs_state, key_state, prev states).
    """
    while True:
        pad = list_and_pick_gamepad()
        if pad is not None:
            try:
                # open some info to ensure it's really accessible
                _ = pad.name
                abs_info = get_abs_ranges(pad)
                print(f"[PAD] Connected: {pad.path} ({pad.name})")
                print("[PAD] L1 codes:", [(c, ec_name(c)) for c in L1_CODES])
                print("[PAD] R1 codes:", [(c, ec_name(c)) for c in R1_CODES])
                print("[PAD] HOME codes:", [(c, ec_name(c)) for c in HOME_CODES])
                return pad, abs_info, {}, {}, False, False, False
            except Exception as e:
                print(f"[PAD] Failed to open candidate {getattr(pad,'path','?')}: {e}")

        print("[PAD] No controller found. Scanning again...")
        time.sleep(RECONNECT_SCAN_SEC)

# =======================
# Main
# =======================

def main():
    port = find_arduino_port()
    if not port:
        raise SystemExit("Arduino serial port not found. Check /dev/ttyACM0 or /dev/ttyUSB0")

    print(f"[SERIAL] Using Arduino port: {port}  BAUD={BAUD}")
    ser = serial.Serial(port, BAUD, timeout=0.05)
    time.sleep(2.0)

    # Angles: [Root, ArmA1, ArmB, WristA, WristB, Gripper]
    angles = [ROOT_HOME, 90, 90, 90, 90, 90]

    period = 1.0 / SEND_HZ
    last_send = 0.0
    last_print = 0.0

    # Initial pad connect (blocks until found)
    pad, abs_info, abs_state, key_state, prev_l1, prev_r1, prev_home = connect_gamepad()

    while True:
        # If pad disconnects, reading will throw (often ENODEV)
        try:
            events = drain_events_safe(pad, max_events=PAD_EVENT_DRAIN_MAX)
        except OSError as e:
            # Controller disconnected
            print(f"[PAD] Disconnected ({e}). Reconnecting...")
            try:
                pad.close()
            except Exception:
                pass
            pad, abs_info, abs_state, key_state, prev_l1, prev_r1, prev_home = connect_gamepad()
            continue
        except Exception as e:
            print(f"[PAD] Error reading controller ({e}). Reconnecting...")
            try:
                pad.close()
            except Exception:
                pass
            pad, abs_info, abs_state, key_state, prev_l1, prev_r1, prev_home = connect_gamepad()
            continue

        for ev in events:
            if ev.type == ecodes.EV_ABS:
                abs_state[ev.code] = ev.value
            elif ev.type == ecodes.EV_KEY:
                key_state[ev.code] = ev.value
                if DEBUG_KEYS:
                    print(f"[KEY] code={ev.code} name={ec_name(ev.code)} value={ev.value}")

        # Press state (treat 1 press and 2 hold as pressed)
        l1 = any(key_state.get(c, 0) in (1, 2) for c in L1_CODES)
        r1 = any(key_state.get(c, 0) in (1, 2) for c in R1_CODES)
        home = any(key_state.get(c, 0) in (1, 2) for c in HOME_CODES)

        # Root home on rising edge
        if home and not prev_home:
            angles[0] = int(clamp(ROOT_HOME, ROOT_MIN, ROOT_MAX))
            print(f"[ROOT] -> HOME ({angles[0]})")

        # Root step on rising edge
        if l1 and not prev_l1 and not r1:
            angles[0] = int(clamp(angles[0] - ROOT_STEP_DEG, ROOT_MIN, ROOT_MAX))
            print(f"[ROOT] step LEFT -> {angles[0]}")

        if r1 and not prev_r1 and not l1:
            angles[0] = int(clamp(angles[0] + ROOT_STEP_DEG, ROOT_MIN, ROOT_MAX))
            print(f"[ROOT] step RIGHT -> {angles[0]}")

        prev_l1 = l1
        prev_r1 = r1
        prev_home = home

        now = time.time()
        if now - last_send >= period:
            last_send = now

            # Arm A1 (Left stick Y)
            if ecodes.ABS_Y in abs_state:
                v = abs_state[ecodes.ABS_Y]
                info = abs_info.get(ecodes.ABS_Y)
                in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                angles[1] = int(map_range(v, in_min, in_max, 180, 0))

            # Arm B (Right stick Y)
            if ecodes.ABS_RY in abs_state:
                v = abs_state[ecodes.ABS_RY]
                info = abs_info.get(ecodes.ABS_RY)
                in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                angles[2] = int(map_range(v, in_min, in_max, 180, 0))

            # Wrist A/B (D-pad)
            if ecodes.ABS_HAT0Y in abs_state:
                haty = abs_state[ecodes.ABS_HAT0Y]
                if haty != 0:
                    angles[3] = int(clamp(angles[3] + (-haty * WRIST_STEP_DEG), 0, 180))

            if ecodes.ABS_HAT0X in abs_state:
                hatx = abs_state[ecodes.ABS_HAT0X]
                if hatx != 0:
                    angles[4] = int(clamp(angles[4] + (hatx * WRIST_STEP_DEG), 0, 180))

            # Gripper (Triggers)
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

            # Final clamp
            angles[0] = int(clamp(angles[0], ROOT_MIN, ROOT_MAX))
            for i in range(1, 6):
                angles[i] = int(clamp(angles[i], 0, 180))

            line = "{} {} {} {} {} {}\n".format(*angles)
            try:
                ser.write(line.encode("ascii"))
            except Exception as e:
                print(f"[SERIAL] write failed: {e}")

            if DEBUG_SEND and (now - last_print) >= PRINT_SEND_EVERY_SEC:
                last_print = now
                print("[SEND]", line.strip())

        time.sleep(0.001)

if __name__ == "__main__":
    main()