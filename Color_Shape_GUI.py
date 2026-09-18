"""GUI สำหรับเล็ง/ยิงสามเป้าและเก็บข้อมูลการทดลองแบบ Real-time.

โปรแกรมเริ่มในสถานะ SAFE เสมอ และจะเชื่อมต่อ/ยิงเมื่อผู้ใช้กดเริ่มเท่านั้น
ลำดับเป้า: วงกลมแดง -> สี่เหลี่ยมเขียว -> สี่เหลี่ยมเหลือง
"""

from collections import deque
import csv
from datetime import datetime
import json
from pathlib import Path
import platform
from queue import Empty
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from robomaster import blaster, robot

from Color_Shape_Sequence import (
    COLOR_RANGES,
    ERROR_SMOOTHING_ALPHA,
    AIM_HEIGHT_RATIO,
    LOST_TARGET_GRACE_FRAMES,
    MAX_ACCELERATION,
    RED_AIM_X_OFFSET_RATIO,
    SETTLE_SPEED,
    SHOT_PAUSE,
    SmoothGimbalController,
    TARGETS,
    detect_target,
    elevated_aim_point,
    is_target_centered,
)
from Red_Target_Debug import draw_debug
from Red_Target_Simple import (
    DEAD_ZONE,
    KP,
    LOCK_FRAMES,
    MAX_SPEED,
    MIN_AREA,
)


FRAME_FIELDS = (
    "timestamp",
    "elapsed_s",
    "target_type",
    "detected",
    "center_x",
    "center_y",
    "error_x",
    "error_y",
    "target_area",
    "circularity",
    "aspect_ratio",
    "corners",
    "yaw_speed",
    "pitch_speed",
    "locked_frames",
    "lost_frames",
    "state",
    "fps",
)

SHOT_FIELDS = (
    "trial_id",
    "timestamp",
    "target_type",
    "detection_time_s",
    "aiming_time_s",
    "total_time_s",
    "frames_to_lock",
    "shot_command_success",
    "target_hit",
)

UI_BG = "#F4F0E6"
UI_INK = "#171717"
UI_MUTED = "#77736B"
UI_LINE = "#C9C2B5"
UI_PANEL = "#E9E4D9"
UI_DANGER = "#A74336"
TARGET_COLORS = ("#B44B42", "#55745A", "#C39B3B")
FIRE_MODES = {
    "INFRARED (TEST)": blaster.INFRARED_FIRE,
    "GEL BULLET": blaster.WATER_FIRE,
}


class ExperimentLogger:
    """บันทึกข้อมูลดิบ สรุปผล และสร้างกราฟของหนึ่งรอบทดลอง."""

    def __init__(self, config):
        session_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_id = "".join(
            char if char.isalnum() or char in "-_" else "_"
            for char in config["experiment_id"]
        ).strip("_") or "experiment"
        self.session_dir = (
            Path(__file__).resolve().parent
            / "experiment_logs"
            / f"{session_stamp}_{safe_id}"
        )
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.config = config
        self.shots = []
        self.plot_samples = []
        self._last_plot_time = -1.0
        self._total_frames = 0
        self._detected_frames = 0
        self._target_frame_counts = {
            color: {"total": 0, "detected": 0} for _, color in TARGETS
        }
        self._camera_resolution_set = False
        self._lock = threading.Lock()
        self._closed = False
        self._final_status = None
        self._finished_at = None

        self._write_json("config.json", config)
        self._frame_file = (self.session_dir / "frames.csv").open(
            "w", newline="", encoding="utf-8-sig"
        )
        self._frame_writer = csv.DictWriter(
            self._frame_file, fieldnames=FRAME_FIELDS
        )
        self._frame_writer.writeheader()
        self._write_shots()

    def _write_json(self, filename, data):
        path = self.session_dir / filename
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _write_shots(self):
        with (self.session_dir / "shots.csv").open(
            "w", newline="", encoding="utf-8-sig"
        ) as output:
            writer = csv.DictWriter(output, fieldnames=SHOT_FIELDS)
            writer.writeheader()
            writer.writerows(self.shots)

    def log_frame(self, row):
        with self._lock:
            if self._closed:
                return
            self._frame_writer.writerow(row)
            self._total_frames += 1
            self._detected_frames += int(row["detected"])
            target_counts = self._target_frame_counts[row["target_type"]]
            target_counts["total"] += 1
            target_counts["detected"] += int(row["detected"])
            # จำกัดข้อมูลสำหรับวาดกราฟไว้ประมาณ 10 จุด/วินาทีเพื่อลด RAM
            if row["elapsed_s"] - self._last_plot_time >= 0.1:
                self.plot_samples.append(dict(row))
                self._last_plot_time = row["elapsed_s"]

    def set_camera_resolution(self, width, height):
        with self._lock:
            if self._camera_resolution_set:
                return
            self.config["camera_resolution"] = [width, height]
            self._write_json("config.json", self.config)
            self._camera_resolution_set = True

    def log_shot(self, row):
        with self._lock:
            self.shots.append(row)
            self._write_shots()

    def mark_hit(self, trial_id, hit):
        with self._lock:
            for shot in self.shots:
                if shot["trial_id"] == trial_id:
                    shot["target_hit"] = bool(hit)
                    self._write_shots()
                    # อนุญาตให้ประเมินโดน/ไม่โดนหลังจบการทดลองได้
                    if self._closed and self._final_status is not None:
                        self._write_json(
                            "summary.json", self._build_summary(self._final_status)
                        )
                    return True
            return False

    def shots_snapshot(self):
        with self._lock:
            return [dict(row) for row in self.shots]

    @staticmethod
    def _average(values):
        return round(sum(values) / len(values), 4) if values else None

    def _build_summary(self, final_status):
        evaluated = [s for s in self.shots if s["target_hit"] != ""]
        hits = sum(s["target_hit"] is True for s in evaluated)
        detection_by_target = {
            target: {
                **counts,
                "frame_detection_rate": round(
                    counts["detected"] / counts["total"], 4
                )
                if counts["total"]
                else None,
            }
            for target, counts in self._target_frame_counts.items()
        }
        return {
            "experiment_id": self.config["experiment_id"],
            "finished_at": self._finished_at,
            "final_status": final_status,
            "total_shots": len(self.shots),
            "successful_shot_commands": sum(
                bool(s["shot_command_success"]) for s in self.shots
            ),
            "evaluated_hits": len(evaluated),
            "hits": hits,
            "misses": len(evaluated) - hits,
            "hit_rate": round(hits / len(evaluated), 4) if evaluated else None,
            "logged_frames": self._total_frames,
            "detected_frames": self._detected_frames,
            "frame_detection_rate": round(
                self._detected_frames / self._total_frames, 4
            )
            if self._total_frames
            else None,
            "detection_by_target": detection_by_target,
            "average_detection_time_s": self._average(
                [s["detection_time_s"] for s in self.shots]
            ),
            "average_aiming_time_s": self._average(
                [s["aiming_time_s"] for s in self.shots]
            ),
            "average_total_time_s": self._average(
                [s["total_time_s"] for s in self.shots]
            ),
            "shots": self.shots,
        }

    def finish(self, final_status):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._final_status = final_status
            self._finished_at = datetime.now().isoformat(timespec="seconds")
            self._frame_file.flush()
            self._frame_file.close()
            self._write_json("summary.json", self._build_summary(final_status))

        self._create_plots()

    def _create_plots(self):
        if not self.plot_samples:
            return
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            self._write_json(
                "plot_status.json",
                {"created": False, "reason": "matplotlib is not installed"},
            )
            return

        detected = [row for row in self.plot_samples if row["detected"]]
        figure, axes = plt.subplots(3, 1, figsize=(12, 11))

        if detected:
            elapsed = [row["elapsed_s"] for row in detected]
            axes[0].plot(elapsed, [row["error_x"] for row in detected], label="error_x")
            axes[0].plot(elapsed, [row["error_y"] for row in detected], label="error_y")
            axes[0].axhline(DEAD_ZONE, color="gray", linestyle="--", linewidth=1)
            axes[0].axhline(-DEAD_ZONE, color="gray", linestyle="--", linewidth=1)
            axes[0].set_ylabel("Normalized error")
            axes[0].set_title("Aiming error over time")
            axes[0].legend()
            axes[0].grid(alpha=0.25)

            axes[1].plot(
                elapsed, [row["yaw_speed"] for row in detected], label="yaw_speed"
            )
            axes[1].plot(
                elapsed,
                [row["pitch_speed"] for row in detected],
                label="pitch_speed",
            )
            axes[1].set_ylabel("Speed (degree/s)")
            axes[1].set_title("Gimbal command over time")
            axes[1].legend()
            axes[1].grid(alpha=0.25)

        shot_names = [shot["target_type"] for shot in self.shots]
        shot_times = [shot["aiming_time_s"] for shot in self.shots]
        axes[2].bar(shot_names, shot_times, color=("#d9534f", "#5cb85c", "#f0ad4e"))
        axes[2].set_ylabel("Time (s)")
        axes[2].set_title("Aiming time by target")
        axes[2].grid(axis="y", alpha=0.25)
        axes[2].set_xlabel("Target")

        figure.suptitle(self.config["experiment_id"])
        figure.tight_layout()
        figure.savefig(self.session_dir / "session_plots.png", dpi=150)
        plt.close(figure)


class RobotExperiment:
    """ทำงานกับ RoboMaster ใน Background thread เพื่อไม่ให้ GUI ค้าง."""

    def __init__(self, config, frame_callback, status_callback, finish_callback):
        self.config = config
        self.frame_callback = frame_callback
        self.status_callback = status_callback
        self.finish_callback = finish_callback
        self.stop_event = threading.Event()
        self.logger = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self.stop_event.set()

    def is_alive(self):
        return self._thread.is_alive()

    def mark_hit(self, trial_id, hit):
        return self.logger is not None and self.logger.mark_hit(trial_id, hit)

    def shots_snapshot(self):
        return self.logger.shots_snapshot() if self.logger else []

    @staticmethod
    def _detect(frame, color):
        return detect_target(frame, color, debug=True)

    @staticmethod
    def _target_metrics(details):
        passed = [detail for detail in details if not detail[5]]
        if not passed:
            return "", "", "", ""
        _, area, circularity, ratio, corners, _ = max(
            passed, key=lambda item: item[1]
        )
        return round(area, 2), round(circularity, 4), round(ratio, 4), corners

    @staticmethod
    def _make_display(frame, detection, name, state, error_x, error_y):
        image, _ = draw_debug(frame, detection)
        height, frame_width = frame.shape[:2]
        center = (frame_width // 2, height // 2)
        cv2.drawMarker(image, center, (255, 255, 0), cv2.MARKER_CROSS, 28, 2)
        error_text = (
            "target not detected"
            if error_x == ""
            else f"error=({error_x:+.3f}, {error_y:+.3f})"
        )
        cv2.rectangle(image, (0, 0), (image.shape[1], 38), (25, 25, 25), -1)
        cv2.putText(
            image,
            f"{name} | {state} | {error_text}",
            (12, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (255, 255, 255),
            2,
        )
        return image

    @staticmethod
    def _to_ppm(image, max_width=1100, max_height=580):
        height, width = image.shape[:2]
        scale = min(max_width / width, max_height / height, 1.0)
        if scale < 1.0:
            image = cv2.resize(
                image,
                (int(width * scale), int(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        return f"P6\n{width} {height}\n255\n".encode() + rgb.tobytes()

    def _publish_status(self, **values):
        self.status_callback(values)

    def _run(self):
        ep = robot.Robot()
        connected = False
        camera_started = False
        final_status = "ERROR"
        error_message = None
        started_at = time.perf_counter()
        frame_times = deque(maxlen=30)

        try:
            self.logger = ExperimentLogger(self.config)
            self._publish_status(state="CONNECTING", target="-", locked=0, fps=0.0)
            ep.initialize(conn_type="ap")
            connected = True
            ep.set_robot_mode(mode=robot.FREE)
            ep.gimbal.recenter().wait_for_completed()
            camera_started = ep.camera.start_video_stream(display=False)
            if not camera_started:
                raise RuntimeError("เปิดกล้องไม่สำเร็จ")

            for trial_id, (name, color) in enumerate(TARGETS, start=1):
                if self.stop_event.is_set():
                    break
                target_started = time.perf_counter()
                first_detection = None
                locked_frames = 0
                lost_frames = 0
                processed_frames = 0
                controller = SmoothGimbalController()

                while not self.stop_event.is_set():
                    try:
                        frame = ep.camera.read_cv2_image(
                            strategy="newest", timeout=0.5
                        )
                    except Empty:
                        frame = None
                    if frame is None:
                        ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
                        self._publish_status(
                            state="NO CAMERA FRAME",
                            target=name,
                            locked=0,
                            fps=0.0,
                        )
                        continue

                    now = time.perf_counter()
                    height, width = frame.shape[:2]
                    self.logger.set_camera_resolution(width, height)
                    frame_times.append(now)
                    fps = (
                        (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
                        if len(frame_times) > 1 and frame_times[-1] > frame_times[0]
                        else 0.0
                    )
                    processed_frames += 1
                    detection = self._detect(frame, color)
                    target, _, details = detection
                    if target is not None:
                        target = elevated_aim_point(target, details, color)
                        detection = (target, detection[1], details)
                    area, circularity, ratio, corners = self._target_metrics(details)
                    error_x = error_y = ""
                    yaw_speed = pitch_speed = 0.0
                    state = "SEARCHING"
                    should_fire = False

                    if target is None:
                        locked_frames = 0
                        lost_frames += 1
                        if lost_frames <= LOST_TARGET_GRACE_FRAMES:
                            state = "REACQUIRING"
                            yaw_speed, pitch_speed = controller.slow_down(now)
                            ep.gimbal.drive_speed(
                                pitch_speed=pitch_speed,
                                yaw_speed=yaw_speed,
                            )
                        else:
                            state = "SEARCHING"
                            controller.reset()
                            ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
                    else:
                        lost_frames = 0
                        if first_detection is None:
                            first_detection = now
                        x, y = target
                        error_x = x / width - 0.5
                        error_y = 0.5 - y / height

                        if is_target_centered(error_x, error_y):
                            yaw_speed, pitch_speed = controller.slow_down(now)
                            ep.gimbal.drive_speed(
                                pitch_speed=pitch_speed,
                                yaw_speed=yaw_speed,
                            )
                            settled = (
                                abs(yaw_speed) <= SETTLE_SPEED
                                and abs(pitch_speed) <= SETTLE_SPEED
                            )
                            locked_frames = locked_frames + 1 if settled else 0
                            state = "LOCKING" if settled else "SETTLING"
                            should_fire = locked_frames >= LOCK_FRAMES
                        else:
                            locked_frames = 0
                            state = "AIMING"
                            yaw_speed, pitch_speed = controller.update(
                                error_x, error_y, now
                            )
                            ep.gimbal.drive_speed(
                                pitch_speed=pitch_speed, yaw_speed=yaw_speed
                            )

                    if should_fire:
                        state = "FIRING"

                    row = {
                        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                        "elapsed_s": round(now - started_at, 4),
                        "target_type": color,
                        "detected": target is not None,
                        "center_x": round(target[0], 2) if target else "",
                        "center_y": round(target[1], 2) if target else "",
                        "error_x": round(error_x, 5) if error_x != "" else "",
                        "error_y": round(error_y, 5) if error_y != "" else "",
                        "target_area": area,
                        "circularity": circularity,
                        "aspect_ratio": ratio,
                        "corners": corners,
                        "yaw_speed": round(yaw_speed, 3),
                        "pitch_speed": round(pitch_speed, 3),
                        "locked_frames": locked_frames,
                        "lost_frames": lost_frames,
                        "state": state,
                        "fps": round(fps, 2),
                    }
                    self.logger.log_frame(row)
                    display = self._make_display(
                        frame, detection, name, state, error_x, error_y
                    )
                    self.frame_callback(self._to_ppm(display))
                    self._publish_status(
                        state=state,
                        target=name,
                        locked=locked_frames,
                        fps=fps,
                    )

                    if should_fire:
                        ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
                        shot_time = time.perf_counter()
                        success = bool(
                            ep.blaster.fire(
                                fire_type=self.config["fire_type"], times=1
                            )
                        )
                        detection_time = (
                            first_detection - target_started
                            if first_detection is not None
                            else 0.0
                        )
                        aiming_time = (
                            shot_time - first_detection
                            if first_detection is not None
                            else 0.0
                        )
                        self.logger.log_shot(
                            {
                                "trial_id": trial_id,
                                "timestamp": datetime.now().isoformat(
                                    timespec="milliseconds"
                                ),
                                "target_type": color,
                                "detection_time_s": round(detection_time, 4),
                                "aiming_time_s": round(aiming_time, 4),
                                "total_time_s": round(shot_time - target_started, 4),
                                "frames_to_lock": processed_frames,
                                "shot_command_success": success,
                                "target_hit": "",
                            }
                        )
                        self._publish_status(
                            state="FIRED" if success else "FIRE FAILED",
                            target=name,
                            locked=locked_frames,
                            fps=fps,
                        )
                        if not success:
                            raise RuntimeError(f"คำสั่งยิง {name} ไม่สำเร็จ")
                        break

                if self.stop_event.wait(SHOT_PAUSE):
                    break

            final_status = "STOPPED" if self.stop_event.is_set() else "COMPLETED"
        except Exception as error:
            error_message = f"{type(error).__name__}: {error}"
            traceback.print_exc()
        finally:
            try:
                if connected:
                    ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
            except Exception:
                pass
            try:
                if camera_started:
                    ep.camera.stop_video_stream()
            except Exception:
                pass
            try:
                ep.close()
            except Exception:
                pass
            if self.logger is not None:
                self.logger.finish(final_status)
                session_dir = str(self.logger.session_dir)
            else:
                session_dir = ""
            self.finish_callback(final_status, session_dir, error_message)


class ExperimentApp:
    """หน้าต่างหลักและตัวกลางแลกข้อมูลระหว่าง GUI กับ Worker thread."""

    def __init__(
        self,
        root,
        fire_type=blaster.INFRARED_FIRE,
        fire_label="Infrared",
    ):
        self.root = root
        self.root.title("Help Me Astra")
        self.root.geometry("1280x900")
        self.root.minsize(1024, 760)
        self.root.configure(background=UI_BG)
        self.worker = None
        self._data_lock = threading.Lock()
        self._latest_frame = None
        self._latest_status = {
            "state": "SAFE — ยังไม่เชื่อมต่อและไม่ยิง",
            "target": "-",
            "locked": 0,
            "fps": 0.0,
        }
        self._finished = None
        self._shown_shot_count = 0
        self._photo = None
        initial_mode = next(
            (label for label, value in FIRE_MODES.items() if value == fire_type),
            "INFRARED (TEST)",
        )

        self.experiment_id = tk.StringVar(
            value=datetime.now().strftime("EXP_%Y%m%d_%H%M%S")
        )
        self.distance = tk.StringVar(value="2.0")
        self.lighting = tk.StringVar(value="indoor")
        self.notes = tk.StringVar()
        self.fire_mode = tk.StringVar(value=initial_mode)
        self.mode_text = tk.StringVar(value=f"MODE / {initial_mode}")
        self.fire_mode.trace_add(
            "write",
            lambda *_args: self.mode_text.set(f"MODE / {self.fire_mode.get()}"),
        )
        self.state_text = tk.StringVar(value=self._latest_status["state"])
        self.target_text = tk.StringVar(value="เป้าปัจจุบัน: -")
        self.fps_text = tk.StringVar(value="FPS: 0.0")
        self.output_text = tk.StringVar(value="ยังไม่มีข้อมูลการทดลอง")
        self.target_cards = {}

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self._refresh_ui)

    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Cream.TFrame", background=UI_BG)
        style.configure(
            "Primary.TButton",
            background=UI_INK,
            foreground=UI_BG,
            borderwidth=0,
            padding=(22, 11),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", "#AAA59B"), ("active", "#343434")],
        )
        style.configure(
            "Danger.TButton",
            background=UI_DANGER,
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(20, 11),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Danger.TButton",
            background=[("disabled", "#C8C2B8"), ("active", "#7F3027")],
        )
        style.configure(
            "Lock.Horizontal.TProgressbar",
            troughcolor=UI_PANEL,
            background=UI_INK,
            borderwidth=0,
            lightcolor=UI_INK,
            darkcolor=UI_INK,
        )

        header = tk.Frame(self.root, background=UI_BG)
        header.pack(fill="x", padx=42, pady=(26, 14))
        brand = tk.Frame(header, background=UI_BG)
        brand.pack(side="left")
        tk.Label(
            brand,
            text="Help Me Astra",
            background=UI_BG,
            foreground=UI_INK,
            font=("Bahnschrift SemiBold", 27),
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="R O B O M A S T E R",
            background=UI_BG,
            foreground=UI_MUTED,
            font=("Consolas", 8),
        ).pack(anchor="w", pady=(1, 0))

        concept = tk.Frame(header, background=UI_BG)
        concept.pack(side="right")
        tk.Button(
            concept,
            text="SETTINGS",
            command=self._open_settings,
            background=UI_BG,
            foreground=UI_INK,
            activebackground=UI_PANEL,
            relief="solid",
            borderwidth=1,
            padx=14,
            pady=5,
            font=("Consolas", 9),
            cursor="hand2",
        ).pack(anchor="e")
        tk.Label(
            concept,
            text="DETECT  →  CENTER  →  SETTLE  →  FIRE",
            background=UI_BG,
            foreground=UI_MUTED,
            font=("Consolas", 9),
        ).pack(anchor="e", pady=(9, 0))
        tk.Label(
            concept,
            textvariable=self.mode_text,
            background=UI_BG,
            foreground=UI_INK,
            font=("Consolas", 9, "bold"),
        ).pack(anchor="e", pady=(3, 0))

        tk.Frame(self.root, background=UI_INK, height=1).pack(
            fill="x", padx=42
        )

        status = tk.Frame(self.root, background=UI_BG)
        status.pack(fill="x", padx=42, pady=(12, 8))
        tk.Label(
            status,
            textvariable=self.state_text,
            background=UI_BG,
            foreground=UI_INK,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            status,
            textvariable=self.target_text,
            background=UI_BG,
            foreground=UI_MUTED,
            font=("Segoe UI", 10),
        ).pack(side="left", padx=24)
        tk.Label(
            status,
            textvariable=self.fps_text,
            background=UI_BG,
            foreground=UI_MUTED,
            font=("Consolas", 9),
        ).pack(side="right")

        camera_border = tk.Frame(self.root, background=UI_INK, padx=5, pady=5)
        camera_border.pack(fill="both", expand=True, padx=42, pady=(0, 8))
        self.preview = tk.Label(
            camera_border,
            text="CAMERA OFFLINE\n\nกด START SEQUENCE เมื่อพื้นที่ทดสอบปลอดภัย",
            anchor="center",
            background="#0E0E0E",
            foreground="#D8D3C9",
            font=("Consolas", 12),
        )
        self.preview.pack(fill="both", expand=True)

        action_bar = tk.Frame(self.root, background=UI_BG)
        action_bar.pack(fill="x", padx=42, pady=(2, 12))
        self.start_button = ttk.Button(
            action_bar,
            text="START SEQUENCE",
            command=self._start,
            style="Primary.TButton",
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            action_bar,
            text="EMERGENCY STOP",
            command=self._stop,
            state="disabled",
            style="Danger.TButton",
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        tk.Label(
            action_bar,
            text="FIRE MODE",
            background=UI_BG,
            foreground=UI_MUTED,
            font=("Consolas", 9),
        ).pack(side="left", padx=(22, 8))
        self.fire_mode_combo = ttk.Combobox(
            action_bar,
            textvariable=self.fire_mode,
            values=tuple(FIRE_MODES),
            width=17,
            state="readonly",
            font=("Consolas", 9),
        )
        self.fire_mode_combo.pack(side="left")
        self.lock_progress = ttk.Progressbar(
            action_bar,
            maximum=LOCK_FRAMES,
            length=210,
            mode="determinate",
            style="Lock.Horizontal.TProgressbar",
        )
        self.lock_progress.pack(side="right", pady=12)
        tk.Label(
            action_bar,
            text="LOCK",
            background=UI_BG,
            foreground=UI_MUTED,
            font=("Consolas", 9),
        ).pack(side="right", padx=10)

        results_header = tk.Frame(self.root, background=UI_BG)
        results_header.pack(fill="x", padx=42)
        tk.Label(
            results_header,
            text="TARGET RESULTS",
            background=UI_BG,
            foreground=UI_INK,
            font=("Consolas", 10, "bold"),
        ).pack(side="left")
        tk.Label(
            results_header,
            text="เลือก HIT / MISS หลังตรวจผลจริง",
            background=UI_BG,
            foreground=UI_MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right")

        cards = tk.Frame(self.root, background=UI_BG)
        cards.pack(fill="x", padx=42, pady=(7, 7))
        target_labels = (
            ("RED CIRCLE", "วงกลมสีแดง"),
            ("GREEN RECTANGLE", "สี่เหลี่ยมสีเขียว"),
            ("YELLOW RECTANGLE", "สี่เหลี่ยมสีเหลือง"),
        )
        for trial_id, ((_, color), labels, accent) in enumerate(
            zip(TARGETS, target_labels, TARGET_COLORS), start=1
        ):
            card = tk.Frame(
                cards,
                background=UI_BG,
                highlightbackground=UI_LINE,
                highlightthickness=1,
                padx=14,
                pady=10,
            )
            card.grid(
                row=0,
                column=trial_id - 1,
                sticky="nsew",
                padx=(0, 8) if trial_id < 3 else 0,
            )
            cards.columnconfigure(trial_id - 1, weight=1)
            dot = tk.Canvas(
                card,
                width=18,
                height=18,
                background=UI_BG,
                highlightthickness=0,
            )
            dot.create_oval(3, 3, 15, 15, fill=accent, outline=UI_INK)
            dot.grid(row=0, column=0, rowspan=2, padx=(0, 8), sticky="n")
            tk.Label(
                card,
                text=f"0{trial_id}  {labels[0]}",
                background=UI_BG,
                foreground=UI_INK,
                font=("Consolas", 10, "bold"),
            ).grid(row=0, column=1, sticky="w")
            tk.Label(
                card,
                text=labels[1],
                background=UI_BG,
                foreground=UI_MUTED,
                font=("Segoe UI", 9),
            ).grid(row=1, column=1, sticky="w")

            state_var = tk.StringVar(value="WAITING")
            metrics_var = tk.StringVar(value="DETECT --  /  AIM --")
            tk.Label(
                card,
                textvariable=state_var,
                background=UI_BG,
                foreground=UI_INK,
                font=("Consolas", 9, "bold"),
            ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(9, 0))
            tk.Label(
                card,
                textvariable=metrics_var,
                background=UI_BG,
                foreground=UI_MUTED,
                font=("Consolas", 8),
            ).grid(row=3, column=0, columnspan=2, sticky="w")

            result_controls = tk.Frame(card, background=UI_BG)
            result_controls.grid(row=0, column=2, rowspan=4, padx=(10, 0))
            hit_button = tk.Button(
                result_controls,
                text="HIT",
                command=lambda item=trial_id: self._mark_trial_hit(item, True),
                state="disabled",
                width=5,
                relief="solid",
                borderwidth=1,
                background=UI_BG,
                font=("Consolas", 8, "bold"),
            )
            hit_button.pack(pady=(0, 4))
            miss_button = tk.Button(
                result_controls,
                text="MISS",
                command=lambda item=trial_id: self._mark_trial_hit(item, False),
                state="disabled",
                width=5,
                relief="solid",
                borderwidth=1,
                background=UI_BG,
                font=("Consolas", 8, "bold"),
            )
            miss_button.pack()
            card.columnconfigure(1, weight=1)
            self.target_cards[trial_id] = {
                "state": state_var,
                "metrics": metrics_var,
                "hit_button": hit_button,
                "miss_button": miss_button,
                "color": color,
            }

        tk.Label(
            self.root,
            textvariable=self.output_text,
            background=UI_BG,
            foreground=UI_MUTED,
            font=("Consolas", 8),
            anchor="w",
        ).pack(fill="x", padx=42, pady=(0, 10))

    def _open_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Experiment settings")
        dialog.configure(background=UI_BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog,
            text="EXPERIMENT SETTINGS",
            background=UI_BG,
            foreground=UI_INK,
            font=("Bahnschrift SemiBold", 18),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=24, pady=(22, 18))
        fields = (
            ("Experiment ID", self.experiment_id),
            ("Target distance (m)", self.distance),
            ("Lighting", self.lighting),
            ("Notes", self.notes),
        )
        for row, (label, variable) in enumerate(fields, start=1):
            tk.Label(
                dialog,
                text=label.upper(),
                background=UI_BG,
                foreground=UI_MUTED,
                font=("Consolas", 9),
            ).grid(row=row, column=0, sticky="w", padx=(24, 18), pady=7)
            if label == "Lighting":
                field = ttk.Combobox(
                    dialog,
                    textvariable=variable,
                    values=("indoor", "outdoor", "low-light", "bright"),
                    width=30,
                    state="readonly",
                )
            else:
                field = ttk.Entry(dialog, textvariable=variable, width=33)
            field.grid(row=row, column=1, padx=(0, 24), pady=7)

        ttk.Button(
            dialog,
            text="SAVE",
            command=dialog.destroy,
            style="Primary.TButton",
        ).grid(row=len(fields) + 1, column=0, columnspan=2, pady=(18, 24))

    def _reset_target_cards(self):
        for card in self.target_cards.values():
            card["state"].set("WAITING")
            card["metrics"].set("DETECT --  /  AIM --")
            card["hit_button"].configure(state="disabled", background=UI_BG)
            card["miss_button"].configure(state="disabled", background=UI_BG)

    def _build_config(self):
        experiment_id = self.experiment_id.get().strip()
        if not experiment_id:
            raise ValueError("กรุณากรอก Experiment ID")
        distance = float(self.distance.get())
        if distance <= 0:
            raise ValueError("ระยะเป้าต้องมากกว่า 0")
        fire_mode_label = self.fire_mode.get()
        if fire_mode_label not in FIRE_MODES:
            raise ValueError("กรุณาเลือก FIRE MODE ที่ถูกต้อง")
        return {
            "experiment_id": experiment_id,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "target_distance_m": distance,
            "lighting": self.lighting.get().strip(),
            "notes": self.notes.get().strip(),
            "camera_resolution": None,
            "connection_type": "ap",
            "fire_mode_label": fire_mode_label,
            "fire_type": FIRE_MODES[fire_mode_label],
            "software": {
                "python": platform.python_version(),
                "opencv": cv2.__version__,
            },
            "target_sequence": [color for _, color in TARGETS],
            "controller": {
                "kp": KP,
                "max_speed": MAX_SPEED,
                "dead_zone": DEAD_ZONE,
                "dead_zone_shape": "center_radius",
                "lock_frames": LOCK_FRAMES,
                "min_area": MIN_AREA,
                "error_smoothing_alpha": ERROR_SMOOTHING_ALPHA,
                "max_acceleration_deg_s2": MAX_ACCELERATION,
                "settle_speed_deg_s": SETTLE_SPEED,
                "lost_target_grace_frames": LOST_TARGET_GRACE_FRAMES,
                "aim_height_ratio": AIM_HEIGHT_RATIO,
                "red_aim_x_offset_ratio": RED_AIM_X_OFFSET_RATIO,
            },
            "color_ranges_hsv": {
                key: {"lower": list(lower), "upper": list(upper)}
                for key, (lower, upper) in COLOR_RANGES.items()
            },
        }

    def _start(self):
        try:
            config = self._build_config()
        except ValueError as error:
            messagebox.showerror("ข้อมูลไม่ถูกต้อง", str(error))
            return
        is_gel = config["fire_type"] == blaster.WATER_FIRE
        shot_description = (
            "ยิงกระสุนเจล 3 นัด"
            if is_gel
            else "ยิงสัญญาณ Infrared 3 ครั้ง โดยไม่ใช้กระสุนเจล"
        )
        if not messagebox.askyesno(
            "ยืนยันการทดลอง",
            f"โหมด: {config['fire_mode_label']}\n"
            f"โปรแกรมจะควบคุม Gimbal และ{shot_description}\n"
            "ยืนยันว่าพื้นที่ด้านหน้าหุ่นยนต์ปลอดภัยหรือไม่?",
        ):
            return

        self._shown_shot_count = 0
        self._reset_target_cards()
        self.worker = RobotExperiment(
            config, self._receive_frame, self._receive_status, self._receive_finish
        )
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.fire_mode_combo.configure(state="disabled")
        self.output_text.set("กำลังสร้างชุดข้อมูลการทดลอง...")
        self.worker.start()

    def _stop(self):
        if self.worker:
            self.worker.stop()
            self.state_text.set("STOPPING — กำลังหยุด Gimbal และปิดการเชื่อมต่อ")
            self.stop_button.configure(state="disabled")

    def _mark_trial_hit(self, trial_id, hit):
        if self.worker and self.worker.mark_hit(trial_id, hit):
            self._update_shot_cards(force=True)

    def _receive_frame(self, ppm_data):
        with self._data_lock:
            self._latest_frame = ppm_data

    def _receive_status(self, status):
        with self._data_lock:
            self._latest_status = status

    def _receive_finish(self, final_status, session_dir, error_message):
        with self._data_lock:
            self._finished = (final_status, session_dir, error_message)

    def _update_shot_cards(self, force=False):
        if not self.worker:
            return
        shots = self.worker.shots_snapshot()
        if not force and len(shots) == self._shown_shot_count:
            return
        for shot in shots:
            card = self.target_cards[shot["trial_id"]]
            result = (
                "HIT"
                if shot["target_hit"] is True
                else "MISS"
                if shot["target_hit"] is False
                else "FIRED — RATE RESULT"
            )
            if not shot["shot_command_success"]:
                result = "FIRE FAILED"
            card["state"].set(result)
            card["metrics"].set(
                f"DETECT {shot['detection_time_s']:.2f}s  /  "
                f"AIM {shot['aiming_time_s']:.2f}s"
            )
            card["hit_button"].configure(
                state="normal",
                background=UI_INK if shot["target_hit"] is True else UI_BG,
                foreground=UI_BG if shot["target_hit"] is True else UI_INK,
            )
            card["miss_button"].configure(
                state="normal",
                background=UI_INK if shot["target_hit"] is False else UI_BG,
                foreground=UI_BG if shot["target_hit"] is False else UI_INK,
            )
        self._shown_shot_count = len(shots)

    def _refresh_ui(self):
        with self._data_lock:
            frame = self._latest_frame
            self._latest_frame = None
            status = dict(self._latest_status)
            finished = self._finished
            self._finished = None

        if frame is not None:
            try:
                self._photo = tk.PhotoImage(data=frame, format="PPM")
                self.preview.configure(image=self._photo, text="")
            except tk.TclError as error:
                self.preview.configure(text=f"แสดงภาพไม่ได้: {error}", image="")

        self.state_text.set(status.get("state", "-"))
        self.target_text.set(f"เป้าปัจจุบัน: {status.get('target', '-')}")
        self.fps_text.set(f"FPS: {status.get('fps', 0.0):.1f}")
        self.lock_progress["value"] = status.get("locked", 0)
        self._update_shot_cards()

        if finished is not None:
            final_status, session_dir, error_message = finished
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.fire_mode_combo.configure(state="readonly")
            self.output_text.set(f"บันทึกผลที่: {session_dir}")
            if error_message:
                messagebox.showerror(
                    "การทดลองหยุดเพราะข้อผิดพลาด",
                    f"{error_message}\n\nข้อมูลที่บันทึกไว้: {session_dir}",
                )
            else:
                messagebox.showinfo(
                    "จบการทดลอง",
                    f"สถานะ: {final_status}\nบันทึกข้อมูลไว้ที่:\n{session_dir}",
                )

        self.root.after(50, self._refresh_ui)

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.state_text.set("STOPPING — รอปิดการเชื่อมต่ออย่างปลอดภัย")
            self.root.after(100, self._wait_then_close)
        else:
            self.root.destroy()

    def _wait_then_close(self):
        if self.worker and self.worker.is_alive():
            self.root.after(100, self._wait_then_close)
        else:
            self.root.destroy()


def main(fire_type=blaster.INFRARED_FIRE, fire_label="Infrared"):
    root = tk.Tk()
    ExperimentApp(root, fire_type=fire_type, fire_label=fire_label)
    root.mainloop()


if __name__ == "__main__":
    main()
