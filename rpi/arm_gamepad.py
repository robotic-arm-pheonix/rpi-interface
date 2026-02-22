#!/usr/bin/env python3
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

ROOT_STEP_DEG = 10
ROOT_HOME = 90
ROOT_MIN = 0
ROOT_MAX = 180

WRIST_STEP_DEG = 3

DEBUG_SCAN = True
DEBUG_KEYS = True
DEBUG_SEND = True
PRINT_SEND_EVERY_SEC = 0.25

RECONNECT_SCAN_SEC = 1.0
PAD_EVENT_DRAIN_MAX = 256

# Buttons
L1_CODES = [ecodes.BTN_TL, ecodes.BTN_TL2]
R1_CODES = [ecodes.BTN_TR, ecodes.BTN_TR2]
HOME_CODES = [ecodes.BTN_START]

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
    cands = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    return cands[0] if cands else None

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

def is_bad_name(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in ["hdmi", "pwr_button", "power button", "keyboard", "mouse"])

def looks_like_gamepad(dev: InputDevice):
    """
    Generic filter: exclude obvious non-gamepad devices.
    Require some ABS axes (sticks) and some KEY buttons, and not a full keyboard.
    """
    try:
        caps = dev.capabilities()
    except Exception:
        return False

    if is_bad_name(dev.name):
        return False

    abs_codes = caps.get(ecodes.EV_ABS, [])
    key_codes = caps.get(ecodes.EV_KEY, [])

    if len(abs_codes) < 2:
        return False
    if len(key_codes) < 1:
        return False
    if len(key_codes) > 150:
        return False

    return True

def score_gamepad(dev: InputDevice) -> int:
    """
    Prefer controller-like names and common axes/buttons, but keep it generic.
    """
    try:
        caps = dev.capabilities()
    except Exception:
        return -999

    s = 0
    name = (dev.name or "").lower()

    if any(k in name for k in ["controller", "gamepad", "8bitdo", "xbox", "dualshock", "sony", "wireless controller"]):
        s += 50

    abs_codes = set(caps.get(ecodes.EV_ABS, []))
    key_codes = set(caps.get(ecodes.EV_KEY, []))

    common_axes = {ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX, ecodes.ABS_RY, ecodes.ABS_Z, ecodes.ABS_RZ}
    common_btns = {ecodes.BTN_SOUTH, ecodes.BTN_EAST, ecodes.BTN_NORTH, ecodes.BTN_WEST,
                   ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_START, ecodes.BTN_SELECT}

    s += 3 * len(abs_codes & common_axes)
    s += 2 * len(key_codes & common_btns)

    # mild preference for richer capability devices
    s += min(len(abs_codes), 10)
    s += min(len(key_codes), 20)

    return s

def pick_gamepad_generic():
    """
    Priority:
      1) /dev/input/by-id/*event-joystick (stable, generic)
      2) best-scoring /dev/input/event*
    """
    # 1) stable symlinks (generic)
    byid = sorted(glob.glob("/dev/input/by-id/*event-joystick"))
    for p in byid:
        try:
            d = InputDevice(p)
            if looks_like_gamepad(d):
                return d
        except Exception:
            pass

    # 2) fallback scan
    devs = []
    for p in list_devices():
        try:
            d = InputDevice(p)
            devs.append(d)
        except Exception:
            pass

    if DEBUG_SCAN:
        print("[PAD] ---- scan ----")
        for d in devs:
            ok = looks_like_gamepad(d)
            try:
                caps = d.capabilities()
                abs_n = len(caps.get(ecodes.EV_ABS, []))
                key_n = len(caps.get(ecodes.EV_KEY, []))
            except Exception:
                abs_n = key_n = -1
            tag = "OK " if ok else "NO "
            print(f"[PAD] {tag} {d.path} name='{d.name}' abs={abs_n} key={key_n} score={score_gamepad(d)}")
        print("[PAD] ------------")

    candidates = [d for d in devs if looks_like_gamepad(d)]
    if not candidates:
        return None

    candidates.sort(key=score_gamepad, reverse=True)
    return candidates[0]

def connect_gamepad_blocking():
    while True:
        pad = pick_gamepad_generic()
        if pad is not None:
            print(f"[PAD] Connected: {pad.path} ({pad.name})")
            abs_info = get_abs_ranges(pad)
            return pad, abs_info, {}, {}, False, False, False
        print("[PAD] No controller found. Retrying...")
        time.sleep(RECONNECT_SCAN_SEC)

# =======================
# Main
# =======================
def main():
    port = find_arduino_port()
    if not port:
        raise SystemExit("Arduino serial port not found.")

    print(f"[SERIAL] Using Arduino port: {port} BAUD={BAUD}")
    ser = serial.Serial(port, BAUD, timeout=0.05)
    time.sleep(2.0)

    angles = [ROOT_HOME, 90, 90, 90, 90, 90]
    period = 1.0 / SEND_HZ
    last_send = 0.0
    last_print = 0.0

    pad, abs_info, abs_state, key_state, prev_l1, prev_r1, prev_home = connect_gamepad_blocking()

    while True:
        try:
            events = drain_events_safe(pad, max_events=PAD_EVENT_DRAIN_MAX)
        except OSError as e:
            print(f"[PAD] Disconnected ({e}). Reconnecting...")
            try:
                pad.close()
            except Exception:
                pass
            pad, abs_info, abs_state, key_state, prev_l1, prev_r1, prev_home = connect_gamepad_blocking()
            continue

        for ev in events:
            if ev.type == ecodes.EV_ABS:
                abs_state[ev.code] = ev.value
            elif ev.type == ecodes.EV_KEY:
                key_state[ev.code] = ev.value
                if DEBUG_KEYS:
                    print(f"[KEY] code={ev.code} name={ec_name(ev.code)} value={ev.value}")

        l1 = any(key_state.get(c, 0) in (1, 2) for c in L1_CODES)
        r1 = any(key_state.get(c, 0) in (1, 2) for c in R1_CODES)
        home = any(key_state.get(c, 0) in (1, 2) for c in HOME_CODES)

        if home and not prev_home:
            angles[0] = int(clamp(ROOT_HOME, ROOT_MIN, ROOT_MAX))
            print(f"[ROOT] HOME -> {angles[0]}")

        if l1 and not prev_l1 and not r1:
            angles[0] = int(clamp(angles[0] - ROOT_STEP_DEG, ROOT_MIN, ROOT_MAX))
            print(f"[ROOT] LEFT -> {angles[0]}")

        if r1 and not prev_r1 and not l1:
            angles[0] = int(clamp(angles[0] + ROOT_STEP_DEG, ROOT_MIN, ROOT_MAX))
            print(f"[ROOT] RIGHT -> {angles[0]}")

        prev_l1, prev_r1, prev_home = l1, r1, home

        now = time.time()
        if now - last_send >= period:
            last_send = now

            if ecodes.ABS_Y in abs_state:
                v = abs_state[ecodes.ABS_Y]
                info = abs_info.get(ecodes.ABS_Y)
                in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                angles[1] = int(map_range(v, in_min, in_max, 180, 0))

            if ecodes.ABS_RY in abs_state:
                v = abs_state[ecodes.ABS_RY]
                info = abs_info.get(ecodes.ABS_RY)
                in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                angles[2] = int(map_range(v, in_min, in_max, 180, 0))

            if ecodes.ABS_HAT0Y in abs_state:
                haty = abs_state[ecodes.ABS_HAT0Y]
                if haty != 0:
                    angles[3] = int(clamp(angles[3] + (-haty * WRIST_STEP_DEG), 0, 180))

            if ecodes.ABS_HAT0X in abs_state:
                hatx = abs_state[ecodes.ABS_HAT0X]
                if hatx != 0:
                    angles[4] = int(clamp(angles[4] + (hatx * WRIST_STEP_DEG), 0, 180))

            lt = abs_state.get(ecodes.ABS_Z)
            rt = abs_state.get(ecodes.ABS_RZ)
            if lt is not None or rt is not None:
                def norm(val, mn=0, mx=255):
                    if val is None or mx == mn:
                        return 0.0
                    return max(0.0, min(1.0, (val - mn) / (mx - mn)))
                grip = map_range(norm(rt) - norm(lt), -1.0, 1.0, 0, 180)
                angles[5] = int(clamp(grip, 0, 180))

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