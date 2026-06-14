#!/usr/bin/env python3
import time
import glob
import errno
import threading
import serial
from flask import Flask, request, jsonify, Response
from evdev import InputDevice, ecodes, list_devices

# =======================
# CONFIG
# =======================

BAUD = 9600
SEND_HZ = 30.0

WEB_HOST = "0.0.0.0"
WEB_PORT = 5000

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

# 5-joint protocol:
# root armA1 armB wristA wristB
JOINTS = ["root", "arm_a1", "arm_b", "wrist_a", "wrist_b"]

# =======================
# Shared state
# =======================

state_lock = threading.Lock()

angles = {
    "root": ROOT_HOME,
    "arm_a1": 90,
    "arm_b": 90,
    "wrist_a": 90,
    "wrist_b": 90,
}

system_status = {
    "controller_connected": False,
    "controller_name": "",
    "controller_path": "",
    "arduino_port": "",
    "last_serial_line": "",
}

# =======================
# Flask Web UI
# =======================

app = Flask(__name__)

HTML_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Robotic Arm Control</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 20px;
      background: #111;
      color: #eee;
    }
    h1 {
      margin-bottom: 8px;
    }
    .status {
      padding: 12px;
      background: #222;
      border-radius: 8px;
      margin-bottom: 18px;
      font-size: 14px;
      line-height: 1.5;
    }
    .joint {
      background: #1d1d1d;
      padding: 14px;
      border-radius: 10px;
      margin-bottom: 12px;
    }
    .row {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    label {
      width: 90px;
      font-weight: bold;
    }
    input[type=range] {
      flex: 1;
    }
    input[type=number] {
      width: 70px;
      padding: 6px;
      border-radius: 6px;
      border: none;
    }
    button {
      padding: 10px 14px;
      border: none;
      border-radius: 8px;
      background: #3b82f6;
      color: white;
      font-weight: bold;
      cursor: pointer;
      margin-right: 8px;
      margin-top: 8px;
    }
    button.danger {
      background: #ef4444;
    }
    button.secondary {
      background: #555;
    }
    .small {
      font-size: 13px;
      color: #bbb;
    }
  </style>
</head>
<body>
  <h1>Robotic Arm Control</h1>

  <div class="status">
    <div><b>Arduino:</b> <span id="arduino">-</span></div>
    <div><b>Controller:</b> <span id="controller">-</span></div>
    <div><b>Last sent:</b> <span id="lastLine">-</span></div>
  </div>

  <div id="controls"></div>

  <button onclick="setAllHome()">All Home</button>
  <button class="secondary" onclick="setRootHome()">Root Home</button>

  <p class="small">
    Protocol sent to Arduino: root armA1 armB wristA wristB
  </p>

<script>
const joints = [
  ["root", "Root"],
  ["arm_a1", "Arm A1"],
  ["arm_b", "Arm B"],
  ["wrist_a", "Wrist A"],
  ["wrist_b", "Wrist B"]
];

let localEditing = false;

function makeControls() {
  const div = document.getElementById("controls");
  div.innerHTML = "";

  for (const [key, label] of joints) {
    const box = document.createElement("div");
    box.className = "joint";
    box.innerHTML = `
      <div class="row">
        <label>${label}</label>
        <input id="${key}_range" type="range" min="0" max="180" value="90"
          oninput="rangeChanged('${key}')"
          onmousedown="localEditing=true"
          onmouseup="localEditing=false"
          ontouchstart="localEditing=true"
          ontouchend="localEditing=false">
        <input id="${key}_num" type="number" min="0" max="180" value="90"
          onchange="numberChanged('${key}')">
      </div>
      <button onclick="stepJoint('${key}', -1)">-</button>
      <button onclick="stepJoint('${key}', 1)">+</button>
    `;
    div.appendChild(box);
  }
}

function clamp(v) {
  v = parseInt(v);
  if (isNaN(v)) v = 90;
  return Math.max(0, Math.min(180, v));
}

function getAnglesFromUI() {
  let data = {};
  for (const [key, _] of joints) {
    data[key] = clamp(document.getElementById(key + "_range").value);
  }
  return data;
}

function updateUIAngles(data) {
  if (localEditing) return;

  for (const [key, _] of joints) {
    if (data.angles && data.angles[key] !== undefined) {
      document.getElementById(key + "_range").value = data.angles[key];
      document.getElementById(key + "_num").value = data.angles[key];
    }
  }
}

async function sendAngles(data) {
  await fetch("/api/angles", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data)
  });
}

function rangeChanged(key) {
  const val = clamp(document.getElementById(key + "_range").value);
  document.getElementById(key + "_num").value = val;
  sendAngles(getAnglesFromUI());
}

function numberChanged(key) {
  const val = clamp(document.getElementById(key + "_num").value);
  document.getElementById(key + "_range").value = val;
  document.getElementById(key + "_num").value = val;
  sendAngles(getAnglesFromUI());
}

async function stepJoint(key, dir) {
  const current = clamp(document.getElementById(key + "_range").value);
  const next = clamp(current + dir);
  document.getElementById(key + "_range").value = next;
  document.getElementById(key + "_num").value = next;
  await sendAngles(getAnglesFromUI());
}

async function setAllHome() {
  let data = {
    root: 90,
    arm_a1: 90,
    arm_b: 90,
    wrist_a: 90,
    wrist_b: 90
  };
  await sendAngles(data);
  await refresh();
}

async function setRootHome() {
  let data = getAnglesFromUI();
  data.root = 90;
  await sendAngles(data);
  await refresh();
}

async function refresh() {
  try {
    const res = await fetch("/api/state");
    const data = await res.json();

    updateUIAngles(data);

    document.getElementById("arduino").innerText = data.status.arduino_port || "-";

    if (data.status.controller_connected) {
      document.getElementById("controller").innerText =
        "connected: " + data.status.controller_name + " (" + data.status.controller_path + ")";
    } else {
      document.getElementById("controller").innerText = "not connected";
    }

    document.getElementById("lastLine").innerText = data.status.last_serial_line || "-";
  } catch (e) {
    console.log(e);
  }
}

makeControls();
refresh();
setInterval(refresh, 500);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")

@app.route("/api/state", methods=["GET"])
def api_state():
    with state_lock:
        return jsonify({
            "angles": dict(angles),
            "status": dict(system_status),
        })

@app.route("/api/angles", methods=["POST"])
def api_angles():
    data = request.get_json(force=True, silent=True) or {}

    with state_lock:
        for joint in JOINTS:
            if joint in data:
                try:
                    angles[joint] = int(clamp(int(data[joint]), 0, 180))
                except Exception:
                    pass

    return jsonify({"ok": True})

def start_web_server():
    print(f"[WEB] Starting UI on http://{WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)

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
            if e.errno in (errno.EAGAIN, 11):
                return out
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

def is_bad_name(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in ["hdmi", "pwr_button", "power button", "keyboard", "mouse"])

def looks_like_gamepad(dev: InputDevice):
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

    common_axes = {
        ecodes.ABS_X,
        ecodes.ABS_Y,
        ecodes.ABS_RX,
        ecodes.ABS_RY,
        ecodes.ABS_Z,
        ecodes.ABS_RZ,
    }

    common_btns = {
        ecodes.BTN_SOUTH,
        ecodes.BTN_EAST,
        ecodes.BTN_NORTH,
        ecodes.BTN_WEST,
        ecodes.BTN_TL,
        ecodes.BTN_TR,
        ecodes.BTN_START,
        ecodes.BTN_SELECT,
    }

    s += 3 * len(abs_codes & common_axes)
    s += 2 * len(key_codes & common_btns)

    s += min(len(abs_codes), 10)
    s += min(len(key_codes), 20)

    return s

def pick_gamepad_generic():
    """
    Generic controller detection:
    1. Prefer /dev/input/by-id/*event-joystick
    2. Fallback to best /dev/input/event*
    """

    byid = sorted(glob.glob("/dev/input/by-id/*event-joystick"))
    for p in byid:
        try:
            d = InputDevice(p)
            if looks_like_gamepad(d):
                return d
        except Exception:
            pass

    devs = []
    for p in list_devices():
        try:
            devs.append(InputDevice(p))
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

def set_controller_status(connected, pad=None):
    with state_lock:
        system_status["controller_connected"] = bool(connected)
        if connected and pad is not None:
            system_status["controller_name"] = pad.name
            system_status["controller_path"] = pad.path
        else:
            system_status["controller_name"] = ""
            system_status["controller_path"] = ""

def get_angle_list():
    with state_lock:
        return [
            int(clamp(angles["root"], 0, 180)),
            int(clamp(angles["arm_a1"], 0, 180)),
            int(clamp(angles["arm_b"], 0, 180)),
            int(clamp(angles["wrist_a"], 0, 180)),
            int(clamp(angles["wrist_b"], 0, 180)),
        ]

def set_joint(joint, value):
    with state_lock:
        angles[joint] = int(clamp(value, 0, 180))

def step_joint(joint, delta):
    with state_lock:
        angles[joint] = int(clamp(angles[joint] + delta, 0, 180))

def connect_gamepad_once():
    pad = pick_gamepad_generic()
    if pad is None:
        return None, None

    print(f"[PAD] Connected: {pad.path} ({pad.name})")
    set_controller_status(True, pad)
    return pad, get_abs_ranges(pad)

# =======================
# Main
# =======================

def main():
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    port = find_arduino_port()
    if not port:
        raise SystemExit("Arduino serial port not found.")

    print(f"[SERIAL] Using Arduino port: {port} BAUD={BAUD}")

    with state_lock:
        system_status["arduino_port"] = port

    ser = serial.Serial(port, BAUD, timeout=0.05)
    time.sleep(2.0)

    period = 1.0 / SEND_HZ
    last_send = 0.0
    last_print = 0.0
    last_scan = 0.0

    pad = None
    abs_info = {}
    abs_state = {}
    key_state = {}

    prev_l1 = False
    prev_r1 = False
    prev_home = False

    while True:
        now = time.time()

        # Controller auto-connect / reconnect
        if pad is None and (now - last_scan) >= RECONNECT_SCAN_SEC:
            last_scan = now
            pad, abs_info = connect_gamepad_once()

            if pad is None:
                set_controller_status(False)
                print("[PAD] No controller found. Retrying...")

            abs_state = {}
            key_state = {}
            prev_l1 = False
            prev_r1 = False
            prev_home = False

        # Read controller if connected
        if pad is not None:
            try:
                events = drain_events_safe(pad, max_events=PAD_EVENT_DRAIN_MAX)
            except OSError as e:
                print(f"[PAD] Disconnected ({e}). Reconnecting...")
                try:
                    pad.close()
                except Exception:
                    pass

                pad = None
                abs_info = {}
                abs_state = {}
                key_state = {}
                set_controller_status(False)
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
                set_joint("root", ROOT_HOME)
                print(f"[ROOT] HOME -> {ROOT_HOME}")

            if l1 and not prev_l1 and not r1:
                step_joint("root", -ROOT_STEP_DEG)
                print(f"[ROOT] LEFT")

            if r1 and not prev_r1 and not l1:
                step_joint("root", ROOT_STEP_DEG)
                print(f"[ROOT] RIGHT")

            prev_l1, prev_r1, prev_home = l1, r1, home

            # Arm A1: left stick Y
            if ecodes.ABS_Y in abs_state:
                v = abs_state[ecodes.ABS_Y]
                info = abs_info.get(ecodes.ABS_Y)
                in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                set_joint("arm_a1", int(map_range(v, in_min, in_max, 180, 0)))

            # Arm B: right stick Y
            if ecodes.ABS_RY in abs_state:
                v = abs_state[ecodes.ABS_RY]
                info = abs_info.get(ecodes.ABS_RY)
                in_min, in_max = (info.min, info.max) if info else (-32768, 32767)
                set_joint("arm_b", int(map_range(v, in_min, in_max, 180, 0)))

            # Wrist A/B: D-pad
            if ecodes.ABS_HAT0Y in abs_state:
                haty = abs_state[ecodes.ABS_HAT0Y]
                if haty != 0:
                    step_joint("wrist_a", -haty * WRIST_STEP_DEG)

            if ecodes.ABS_HAT0X in abs_state:
                hatx = abs_state[ecodes.ABS_HAT0X]
                if hatx != 0:
                    step_joint("wrist_b", hatx * WRIST_STEP_DEG)

        # Send 5 values to Arduino
        now = time.time()
        if now - last_send >= period:
            last_send = now

            out = get_angle_list()
            line = "{} {} {} {} {}\n".format(*out)

            try:
                ser.write(line.encode("ascii"))
            except Exception as e:
                print(f"[SERIAL] write failed: {e}")

            with state_lock:
                system_status["last_serial_line"] = line.strip()

            if DEBUG_SEND and (now - last_print) >= PRINT_SEND_EVERY_SEC:
                last_print = now
                print("[SEND]", line.strip())

        time.sleep(0.001)

if __name__ == "__main__":
    main()