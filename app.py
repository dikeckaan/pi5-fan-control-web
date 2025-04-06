from flask import Flask, jsonify, request, render_template, send_file
import json
import threading
import time
import os
import glob

app = Flask(__name__, template_folder="templates")

CONFIG_PATH = "config.json"
TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"
FAN_PATH = "/sys/class/thermal/cooling_device0/cur_state"


class FanController:
    def __init__(self):
        self.running = True
        self.boost_cancel_event = threading.Event()
        self.thread = threading.Thread(target=self.run)
        self.thread.daemon = True
        self.thread.start()

    def read_config(self):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)

    def read_temp(self):
        with open(TEMP_PATH, "r") as f:
            return int(f.read().strip()) / 1000

    def write_fan_state(self, state):
        with open(FAN_PATH, "w") as f:
            f.write(str(state))

    def run(self):
        while self.running:
            try:
                cfg = self.read_config()
                mode = cfg.get("mode", "auto")

                if mode == "manual":
                    level = int(cfg.get("manual_level", 0))
                else:
                    temp = self.read_temp()
                    level = 0
                    thresholds = cfg.get("auto_thresholds", {})
                    for t, l in sorted((int(k), int(v)) for k, v in thresholds.items()):
                        if temp >= t:
                            level = l

                self.write_fan_state(level)
                time.sleep(3)
            except Exception as e:
                print("Fan kontrol hatası:", e)
                time.sleep(5)

    def stop(self):
        self.running = False
        self.thread.join()

    def boost(self, seconds=5):
        print("Fan BOOST aktif!")
        self.boost_cancel_event.clear()
        end_time = time.time() + seconds
        while time.time() < end_time:
            if self.boost_cancel_event.is_set():
                print("Fan BOOST erken iptal edildi.")
                return
            self.write_fan_state(4)
            time.sleep(0.1)
        print("Fan BOOST sona erdi.")

    def cancel_boost(self):
        self.boost_cancel_event.set()


fan_controller = FanController()

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/status")
def status():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        with open(FAN_PATH) as f:
            cur_state = int(f.read().strip())
        with open(TEMP_PATH) as f:
            temp = int(f.read().strip()) / 1000
        return jsonify({
            "mode": cfg.get("mode", "auto"),
            "manual_level": cfg.get("manual_level", 0),
            "cur_state": cur_state,
            "temperature": temp,
            "auto_thresholds": cfg.get("auto_thresholds", {})
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/set_mode", methods=["POST"])
def set_mode():
    data = request.json
    mode = data.get("mode")
    if mode not in ["manual", "auto"]:
        return jsonify({"error": "Invalid mode"}), 400
    cfg = fan_controller.read_config()
    cfg["mode"] = mode
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    return jsonify({"status": "ok"})


@app.route("/set_manual_level", methods=["POST"])
def set_manual_level():
    data = request.json
    level = int(data.get("level", 0))
    if level < 0 or level > 4:
        return jsonify({"error": "Invalid level"}), 400
    cfg = fan_controller.read_config()
    cfg["manual_level"] = level
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    return jsonify({"status": "ok"})


@app.route("/boost", methods=["POST"])
def boost():
    seconds = int(request.json.get("seconds", 5))
    threading.Thread(target=fan_controller.boost, args=(seconds,), daemon=True).start()
    return jsonify({"status": "boosting", "duration": seconds})


@app.route("/cancel_boost", methods=["POST"])
def cancel_boost():
    fan_controller.cancel_boost()
    return jsonify({"status": "boost_cancelled"})


@app.route("/config.json")
def serve_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return f.read(), 200, {"Content-Type": "application/json"}
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/upload_config", methods=["POST"])
def upload_config():
    try:
        new_config = request.get_json(force=True)

        if not isinstance(new_config, dict):
            raise ValueError("JSON nesnesi bekleniyor.")

        if "mode" not in new_config or new_config["mode"] not in ["auto", "manual"]:
            raise ValueError("Geçerli bir 'mode' alanı ('auto' veya 'manual') gerekiyor.")

        if "manual_level" not in new_config or not isinstance(new_config["manual_level"], int):
            raise ValueError("'manual_level' alanı bir tamsayı olmalı.")

        if "auto_thresholds" not in new_config or not isinstance(new_config["auto_thresholds"], dict):
            raise ValueError("'auto_thresholds' alanı bir sözlük (dict) olmalı.")

        for temp_str, level in new_config["auto_thresholds"].items():
            if not temp_str.isdigit():
                raise ValueError(f"Eşik anahtarı sayısal değil: {temp_str}")
            if not isinstance(level, int) or not (0 <= level <= 4):
                raise ValueError(f"Eşik seviyesi 0-4 aralığında olmalı: {level}")

        with open(CONFIG_PATH, "w") as f:
            json.dump(new_config, f, indent=2)

        return jsonify({"status": "updated"})

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/set_thresholds", methods=["POST"])
def set_thresholds():
    try:
        data = request.get_json(force=True)
        thresholds = data.get("thresholds", {})

        if not isinstance(thresholds, dict):
            raise ValueError("Eşikler bir sözlük olmalı.")

        for key, value in thresholds.items():
            if not str(key).isdigit():
                raise ValueError(f"Eşik değeri geçersiz: {key}")
            if not isinstance(value, int) or not (0 <= value <= 4):
                raise ValueError(f"Fan seviyesi 0–4 aralığında olmalı: {value}")

        config = fan_controller.read_config()
        config["auto_thresholds"] = {str(k): int(v) for k, v in thresholds.items()}

        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)

        return jsonify({"status": "thresholds updated"})

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/api/fan/rpm')
def get_fan_rpm():
    try:
        rpm_path = glob.glob('/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input')[0]
        with open(rpm_path, 'r') as f:
            rpm = int(f.read().strip())
        return jsonify({'rpm': rpm})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
