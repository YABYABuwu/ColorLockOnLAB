"""ยิงตามลำดับ: วงกลมแดง -> สี่เหลี่ยมเขียว -> สี่เหลี่ยมเหลือง

รัน: .venv/bin/python Color_Shape_Sequence.py
ยิงอินฟราเรดเป้าละหนึ่งครั้ง ครบสามเป้าแล้วจบ กด Ctrl+C เพื่อหยุด
เป้าที่กำลังตามต้องอยู่ในภาพกล้อง ถ้าไม่พบจะหยุดรอ ไม่กวาดค้นหา
"""

from queue import Empty
import time

import cv2
import numpy as np
from robomaster import robot

# ใช้การตรวจวงกลมสีแดงที่ปรับจนใช้งานได้แล้ว
from Red_Target_Simple import (
    find_red_target, KP, MAX_SPEED, DEAD_ZONE, MIN_AREA, LOCK_FRAMES,
)


# ช่วงสี HSV ของ OpenCV: H = 0-179, S/V = 0-255
COLOR_RANGES = {
    "yellow": ((20, 100, 70), (35, 255, 255)),
    # รับเขียวอมเหลือง/เขียวซีด/เขียวมืดเพิ่ม โดยไม่ทับช่วง Hue สีเหลือง
    "green": ((140, 45, 40), (150, 255, 255)),
}
TARGETS = [
    ("วงกลมสีแดง", "red"),
    ("สี่เหลี่ยมสีเขียว", "green"),
    ("สี่เหลี่ยมสีเหลือง", "yellow"),
]
FIRE_TYPE = "ir"          # ยิงอินฟราเรด
SHOT_PAUSE = 1.0          # เว้นระยะก่อนเริ่มเล็งเป้าถัดไป (วินาที)


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


def aim_and_fire(ep, name, color):
    """รอเป้าปัจจุบันอยู่กลางภาพครบจำนวนเฟรม แล้วยิงครั้งเดียว"""
    locked_frames = 0
    print(f"กำลังเล็ง: {name}")
    while True:
        try:
            frame = ep.camera.read_cv2_image(strategy="newest", timeout=0.5)
        except Empty:
            frame = None

        target = None
        if frame is not None:
            target = (find_red_target(frame) if color == "red"
                      else find_rectangle(frame, color))

        if target is None:
            ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
            locked_frames = 0
            continue

        height, width = frame.shape[:2]
        x, y = target
        error_x = x / width - 0.5
        error_y = 0.5 - y / height

        if abs(error_x) < DEAD_ZONE and abs(error_y) < DEAD_ZONE:
            ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
            locked_frames += 1
            if locked_frames >= LOCK_FRAMES:
                if not ep.blaster.fire(fire_type=FIRE_TYPE, times=1):
                    raise RuntimeError(f"คำสั่งยิง {name} ไม่สำเร็จ หยุดลำดับ")
                print(f"ยิงแล้ว: {name}")
                return
        else:
            locked_frames = 0
            yaw = max(-MAX_SPEED, min(MAX_SPEED, KP * error_x))
            pitch = max(-MAX_SPEED, min(MAX_SPEED, KP * error_y))
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
