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

# Optional force: set to a stable symlink like /dev/input/by-id/...-event-joystick
FORCED_GAMEPAD_DEV = os.environ.get("GAMEPAD_DEV", "").strip() or None

# Button mapping
L1_CODES = [ecodes.BTN_TL, ecodes.BTN_TL2]
R1_CODES = [ecodes.BTN_TR, ecodes.BTN_TR2]
HOME_CODES = [ecodes.BTN_START]     # press START -> root goes home

# Reconnect behavior
RECONNECT_SCAN_SEC = 1.0
PAD_EVENT_DRAIN_MAX = 256

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
            if e.errno in (errno.EAGAIN, 11):  # no events right now
                return out
            raise  # ENODEV etc => disconnected
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

GAMEPAD_BUTTONS = {
    ecodes.BTN_SOUTH, ecodes.BTN_EAST, ecodes.BTN_NORTH, ecodes.BTN_WEST,
    ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_SELECT, ecodes.BTN_START,
    ecodes.BTN_THUMBL, ecodes.BTN_THUMBR,
}

GAMEPAD_AXES = {ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX, ecodes.ABS_RY, ecodes.ABS_Z, ecodes.ABS_RZ}

def is_real_gamepad(dev: InputDevice) -> bool:
    """
    Filter out keyboard/mouse/HDMI/event nodes.
    """
    try:
        caps = dev.capabilities()
    except Exception:
        return False

    abs_codes = set(caps.get(ecodes.EV_ABS, []))
    key_codes = set(caps.get(ecodes.EV_KEY, []))

    name = (dev.name or "").lower()

    # Reject obvious non-gamepad names
    if "keyboard" in name or "mouse" in name or "hdmi" in name or "pwr_button" in name:
        return False

    # Must have at least 2 ABS axes to be a controller (sticks / triggers)
    if len(abs_codes) < 2:
        return False

    # Must have at least one typical gamepad axis
    if not (abs_codes & GAMEPAD_AXES):
        return False

    # Must have at least one typical gamepad button
    if not (key_codes & GAMEPAD_BUTTONS):
        return False

    # Reject keyboard-like devices that have hundreds of keys
    if len(key_codes) > 100:
        return False

    return True

def preferred_by_id_paths():
    """
    Stable symlinks for controllers usually appear here.
    Prefer *event-joystick if available.
    """
    paths = sorted(glob.glob("/dev/input/by-id/*event-joystick"))
    return paths

def list_candidates():
    """
    Return list of InputDevice objects that look like real gamepads.
    Prefer by-id stable paths first.
    """
    candidates = []

    # 1) by-id stable event-joystick first
    for p in preferred_by_id_paths():
        try:
            d = InputDevice(p)
            if is_real_gamepad(d):
                candidates.append(d)
        except Exception:
            pass

    # 2) fallback: scan all event devices
    for p in list_devices():
        try:
            d = InputDevice(p)
            if is_real_gamepad(d):
                candidates.append(d)
        except Exception:
            pass

    return candidates

def connect_gamepad():
    """
    Blocks until a real gamepad is available, then returns:
    pad, abs_info, abs_state, key_state, prev_l1, prev_r1, prev_home
    """
    while True:
        if FORCED_GAMEPAD_DEV:
            try:
                pad = InputDevice(FORCED_GAMEPAD_DEV)
                if is_real_gamepad(pad):
                    print(f"[PAD] Connected (forced): {pad.path} ({pad.name})")
                    return pad, get_abs_ranges(pad), {}, {}, False, False, False
                else:
                    print(f"[PAD] Forced device not a real gamepad: {FORCED_GAMEPAD_DEV} ({pad.name})")
            except Exception as e:
                print(f"[PAD] Forced device not available: {FORCED_GAMEPAD_DEV} ({e})")

        cands = list_candidates()

        if DEBUG_LIST_DEVICES:
            print("[PAD] ---- candidates ----")
            if cands:
                for d in cands:
                    caps = d.capabilities()
                    abs_n = len(caps.get(ecodes.EV_ABS, []))
                    key_n = len(caps.get(ecodes.EV_KEY, []))
                    print(f"[PAD] {d.path} name='{d.name}' ABS={abs_n} KEY={key_n}")
            else:
                print("[PAD] (none)")
            print("[PAD] -------------------")

        if cands:
            # If multiple, choose one with name containing '8bitdo' first, else first
            cands_sorted = sorted(
                cands,
                key=lambda d: (("8bitdo" not in (d.name or "").lower()), d.path)
            )
            pad = cands_sorted[0]
            print(f"[PAD] Connected: {pad.path} ({pad.name})")
            print("[PAD] L1 codes:", [(c, ec_name(c)) for c in L1_CODES])
            print("[PAD] R1 codes:", [(c, ec_name(c)) for c in R1_CODES])
            print("[PAD] HOME codes:", [(c, ec_name(c)) for c in HOME_CODES])
            return pad, get_abs_ranges(pad), {}, {}, False, False, False

        print("[PAD] No real controller found. Scanning again...")
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

    pad, abs_info, abs_state, key_state, prev_l1, prev_r1, prev_home = connect_gamepad()

    while True:
        try:
            events = drain_events_safe(pad, max_events=PAD_EVENT_DRAIN_MAX)
        except OSError as e:
            print(f"[PAD] Disconnected ({e}). Reconnecting...")
            try:
                pad.close()
            except Exception:
                pass
            pad, abs_info, abs_state, key_state, prev_l1, prev_r1, prev_home = connect_gamepad()
            continue
        except Exception as e:
            print(f"[PAD] Read error ({e}). Reconnecting...")
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

        # Button pressed state (1 press, 2 hold)
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

        prev_l1, prev_r1, prev_home = l1, r1, home

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

            # Clamp all
            angles[0] = int(clamp(angles[0], ROOT_MIN, ROOT_MAX))
            for i in range(1, 6):
                angles[i] = int(clamp(angles[i], 0, 180))

            line = "{} {} {} {} {} {}\n".format(*angles)
            ser.write(line.encode("ascii"))

            if DEBUG_SEND and (now - last_print) >= PRINT_SEND_EVERY_SEC:
                last_print = now
                print("[SEND]", line.strip())

        time.sleep(0.001)

if __name__ == "__main__":
    main()