"""ยิงตามลำดับ: วงกลมแดง -> สี่เหลี่ยมเขียว -> สี่เหลี่ยมเหลือง

รัน: .venv/bin/python Color_Shape_Sequence.py
ยิงกระสุนเจลเป้าละหนึ่งนัด ครบสามเป้าแล้วจบ กด Ctrl+C เพื่อหยุด
เป้าที่กำลังตามต้องอยู่ในภาพกล้อง ถ้าไม่พบจะหยุดรอ ไม่กวาดค้นหา
"""

from queue import Empty
import time

import cv2
import numpy as np
from robomaster import blaster, robot

# ใช้การตรวจวงกลมสีแดงที่ปรับจนใช้งานได้แล้ว
from Red_Target_Simple import (
    find_red_target, KP, MAX_SPEED, DEAD_ZONE, MIN_AREA, LOCK_FRAMES,
)


# ช่วงสี HSV ของ OpenCV: H = 0-179, S/V = 0-255
COLOR_RANGES = {
    "yellow": ((20, 100, 70), (35, 255, 255)),
    # รับเป้าเขียวที่มืด แต่ใช้ Saturation สูงเพื่อไม่รับสีขาว/สีเทา
    "green": ((36, 80, 20), (95, 255, 255)),
}
TARGETS = [
    ("วงกลมสีแดง", "red"),
    ("สี่เหลี่ยมสีเขียว", "green"),
    ("สี่เหลี่ยมสีเหลือง", "yellow"),
]
FIRE_TYPE = blaster.WATER_FIRE  # กระสุนเจล/Water gel bead ใน RoboMaster SDK
SHOT_PAUSE = 1.0          # เว้นระยะก่อนเริ่มเล็งเป้าถัดไป (วินาที)
ERROR_SMOOTHING_ALPHA = 0.25  # ค่าน้อยจะนุ่มขึ้น แต่ตอบสนองช้าลง
MAX_ACCELERATION = 120.0       # จำกัดการเปลี่ยนความเร็ว (องศา/วินาที²)
SETTLE_SPEED = 0.75            # ต้องเกือบหยุดนิ่งจึงเริ่มนับ Lock
LOST_TARGET_GRACE_FRAMES = 8   # ผ่อนความเร็วระหว่างรอพบเป้ากลับมา
AIM_HEIGHT_RATIO = 0.35        # เล็ง 35% จากขอบบนทั้ง Infrared และกระสุนเจล
RED_AIM_X_OFFSET_RATIO = -0.045 # ชดเชยเป้าแดงไปซ้าย 4.5% ของความกว้างเป้า


class SmoothGimbalController:
    """กรอง Error และจำกัดความเร่งเพื่อลดการกระตุกของ Gimbal."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.filtered_x = None
        self.filtered_y = None
        self.yaw_speed = 0.0
        self.pitch_speed = 0.0
        self.last_update = None

    @staticmethod
    def _move_towards(current, target, max_change):
        change = max(-max_change, min(max_change, target - current))
        return current + change

    def update(self, error_x, error_y, now=None):
        now = time.perf_counter() if now is None else now
        if self.last_update is None:
            delta_time = 1.0 / 30.0
        else:
            # จำกัดช่วงเวลาเพื่อไม่ให้เร่งกระทันหันหลังกล้องสะดุด
            delta_time = max(1.0 / 120.0, min(0.1, now - self.last_update))
        self.last_update = now

        if self.filtered_x is None:
            self.filtered_x, self.filtered_y = error_x, error_y
        else:
            alpha = ERROR_SMOOTHING_ALPHA
            self.filtered_x += alpha * (error_x - self.filtered_x)
            self.filtered_y += alpha * (error_y - self.filtered_y)

        target_yaw = max(-MAX_SPEED, min(MAX_SPEED, KP * self.filtered_x))
        target_pitch = max(-MAX_SPEED, min(MAX_SPEED, KP * self.filtered_y))
        max_change = MAX_ACCELERATION * delta_time
        self.yaw_speed = self._move_towards(
            self.yaw_speed, target_yaw, max_change
        )
        self.pitch_speed = self._move_towards(
            self.pitch_speed, target_pitch, max_change
        )
        return self.yaw_speed, self.pitch_speed

    def slow_down(self, now=None):
        """ลดความเร็วเข้าศูนย์โดยตรง แต่เก็บตำแหน่งกรองไว้รอ Reacquire."""
        now = time.perf_counter() if now is None else now
        if self.last_update is None:
            delta_time = 1.0 / 30.0
        else:
            delta_time = max(1.0 / 120.0, min(0.1, now - self.last_update))
        self.last_update = now
        max_change = MAX_ACCELERATION * delta_time
        self.yaw_speed = self._move_towards(self.yaw_speed, 0.0, max_change)
        self.pitch_speed = self._move_towards(
            self.pitch_speed, 0.0, max_change
        )
        return self.yaw_speed, self.pitch_speed


def is_target_centered(error_x, error_y):
    """ตรวจว่าจุดกลางเป้าอยู่ในวงกลมรอบกากบาทกลางภาพ."""
    return error_x ** 2 + error_y ** 2 <= DEAD_ZONE ** 2


def find_rectangle(frame, color, debug=False):
    """เลือกสี่เหลี่ยมสีที่ต้องการชิ้นใหญ่สุด รับทั้งจัตุรัสและผืนผ้า"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower, upper = COLOR_RANGES[color]
    mask = cv2.inRange(hsv, lower, upper)
    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    rectangles = []
    details = []
    for contour in contours:
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        if perimeter == 0:
            continue
        corners = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        # ยอมให้สี่เหลี่ยมเอียงในภาพได้ แต่ต้องมี 4 มุมและไม่มีส่วนเว้า
        reasons = []
        if area < MIN_AREA:
            reasons.append("small")
        if len(corners) != 4:
            reasons.append("corners")
        if not cv2.isContourConvex(corners):
            reasons.append("not convex")
        _, _, width, height = cv2.boundingRect(contour)
        circularity = 4 * np.pi * area / perimeter ** 2
        ratio = min(width, height) / max(width, height)
        details.append((contour, area, circularity, ratio, len(corners), reasons))
        if not reasons:
            rectangles.append(contour)

    if not rectangles:
        return (None, mask, details) if debug else None
    target = max(rectangles, key=cv2.contourArea)
    moments = cv2.moments(target)
    center = moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]
    return (center, mask, details) if debug else center


def detect_target(frame, color, debug=False):
    """Detector กลางที่ใช้ร่วมกันทั้ง Debug, GUI และโปรแกรมยิง."""
    return (
        find_red_target(frame, debug=debug)
        if color == "red"
        else find_rectangle(frame, color, debug=debug)
    )


def elevated_aim_point(center, details, color=None):
    """คืนจุดเล็งช่วงบนของเป้า โดยยังอยู่ภายในกรอบเป้า."""
    if center is None:
        return None
    passed = [detail for detail in details if not detail[5]]
    if not passed:
        return center
    contour = max(passed, key=lambda item: item[1])[0]
    _, top, width, height = cv2.boundingRect(contour)
    aim_x = center[0]
    if color == "red":
        aim_x += RED_AIM_X_OFFSET_RATIO * width
    return aim_x, top + AIM_HEIGHT_RATIO * height


def aim_and_fire(ep, name, color):
    """รอเป้าปัจจุบันอยู่กลางภาพครบจำนวนเฟรม แล้วยิงครั้งเดียว"""
    locked_frames = 0
    lost_frames = 0
    controller = SmoothGimbalController()
    print(f"กำลังเล็ง: {name}")
    while True:
        try:
            frame = ep.camera.read_cv2_image(strategy="newest", timeout=0.5)
        except Empty:
            frame = None

        target = None
        if frame is not None:
            detected_center, _, details = detect_target(frame, color, debug=True)
            target = elevated_aim_point(detected_center, details, color)

        if target is None:
            locked_frames = 0
            lost_frames += 1
            if lost_frames <= LOST_TARGET_GRACE_FRAMES:
                # เป้าอาจหายชั่วคราวจาก Motion blur: ชะลอแทนการหยุดกระตุก
                yaw, pitch = controller.slow_down()
                ep.gimbal.drive_speed(pitch_speed=pitch, yaw_speed=yaw)
            else:
                ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
                controller.reset()
            continue

        lost_frames = 0
        height, width = frame.shape[:2]
        x, y = target
        error_x = x / width - 0.5
        error_y = 0.5 - y / height

        if is_target_centered(error_x, error_y):
            yaw, pitch = controller.slow_down()
            ep.gimbal.drive_speed(pitch_speed=pitch, yaw_speed=yaw)
            if abs(yaw) <= SETTLE_SPEED and abs(pitch) <= SETTLE_SPEED:
                locked_frames += 1
            else:
                locked_frames = 0
            if locked_frames >= LOCK_FRAMES:
                ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
                if not ep.blaster.fire(fire_type=FIRE_TYPE, times=1):
                    raise RuntimeError(f"คำสั่งยิง {name} ไม่สำเร็จ หยุดลำดับ")
                print(f"ยิงแล้ว: {name}")
                return
        else:
            locked_frames = 0
            yaw, pitch = controller.update(error_x, error_y)
            ep.gimbal.drive_speed(pitch_speed=pitch, yaw_speed=yaw)


def main():
    ep = robot.Robot()
    connected = False
    camera_started = False
    try:
        ep.initialize(conn_type="ap")
        connected = True
        ep.set_robot_mode(mode=robot.FREE)
        ep.gimbal.recenter().wait_for_completed()
        camera_started = ep.camera.start_video_stream(display=False)
        if not camera_started:
            raise RuntimeError("เปิดกล้องไม่สำเร็จ")

        for index, (name, color) in enumerate(TARGETS):
            aim_and_fire(ep, name, color)
            if index < len(TARGETS) - 1:
                time.sleep(SHOT_PAUSE)
        print("ยิงครบ 3 เป้าแล้ว")
    except KeyboardInterrupt:
        print("\nหยุดโปรแกรม")
    finally:
        try:
            if connected:
                ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
        finally:
            try:
                if camera_started:
                    ep.camera.stop_video_stream()
            finally:
                ep.close()


if __name__ == "__main__":
    main()
