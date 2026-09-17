"""เล็งวงกลมสีแดงที่ใหญ่ที่สุด แล้วยิงหนึ่งครั้ง (Ctrl+C เพื่อหยุด)

เชื่อมต่อ Wi-Fi ของ EP แล้วรัน: .venv/bin/python Red_Target_Simple.py
ใช้ได้กับ opencv-python-headless โดยไม่เปิดหน้าต่างภาพ
"""

from queue import Empty

import cv2
import numpy as np
from robomaster import blaster, robot


# ค่าที่ปรับได้
KP = 150                 # ยิ่งมาก ยิ่งหมุนตามเร็ว
MAX_SPEED = 60            # จำกัดความเร็วหมุน (องศา/วินาที)
DEAD_ZONE = 0.03          # ยอมให้คลาดจากกลางภาพ 3% ของแต่ละแกน
MIN_AREA = 800            # ไม่สนใจจุดสีแดงเล็กกว่า 800 พิกเซล
MIN_CIRCULARITY = 0.70    # เผื่อขอบหยักจากกล้อง (ยังกรองจำนวนมุมร่วมด้วย)
MIN_ASPECT_RATIO = 0.75   # เผื่อวงกลมที่เอียงเล็กน้อยจนดูรี
LOCK_FRAMES = 5           # ต้องอยู่กลางภาพติดต่อกันก่อนยิง


def find_red_target(frame, debug=False):
    """คืนจุดกลาง (x, y) ของวงกลมสีแดงที่ใหญ่ที่สุด หรือ None"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # สีแดงอยู่ทั้งต้นและปลายช่วง Hue ของ OpenCV (0-179)
    red_low = cv2.inRange(hsv, (0, 120, 70), (10, 255, 255))
    red_high = cv2.inRange(hsv, (170, 120, 70), (179, 255, 255))
    mask = red_low | red_high
    kernel = np.ones((3, 3), dtype=np.uint8)
    # เชื่อมรอยขาดเล็ก ๆ ก่อนลบจุดรบกวน ใช้ kernel เล็กเพื่อรักษาวงแหวน
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    circles = []
    details = []
    for contour in contours:
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        if perimeter == 0:
            continue

        # วงกลมมีค่า 4*pi*พื้นที่/ความยาวขอบ² ใกล้ 1
        circularity = 4 * np.pi * area / (perimeter ** 2)
        _, _, width, height = cv2.boundingRect(contour)
        aspect_ratio = min(width, height) / max(width, height)

        # กรองรูปหลายเหลี่ยมง่าย ๆ เช่น สามเหลี่ยมและสี่เหลี่ยม
        corners = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        reasons = []
        if area < MIN_AREA:
            reasons.append("small")
        if circularity < MIN_CIRCULARITY:
            reasons.append("roundness")
        if aspect_ratio < MIN_ASPECT_RATIO:
            reasons.append("aspect")
        if len(corners) < 7:
            reasons.append("corners")
        details.append((contour, area, circularity, aspect_ratio, len(corners), reasons))
        if not reasons:
            circles.append(contour)

    if not circles:
        return (None, mask, details) if debug else None

    target = max(circles, key=cv2.contourArea)
    moments = cv2.moments(target)
    x = moments["m10"] / moments["m00"]
    y = moments["m01"] / moments["m00"]
    return ((x, y), mask, details) if debug else (x, y)


def main():
    ep = robot.Robot()
    connected = False
    camera_started = False
    locked_frames = 0

    try:
        ep.initialize(conn_type="ap")
        connected = True
        ep.set_robot_mode(mode=robot.FREE)
        ep.gimbal.recenter().wait_for_completed()
        camera_started = ep.camera.start_video_stream(display=False)
        if not camera_started:
            raise RuntimeError("เปิดกล้องไม่สำเร็จ")

        print("กำลังหาวงกลมสีแดง... กด Ctrl+C เพื่อหยุด")
        while True:
            try:
                frame = ep.camera.read_cv2_image(strategy="newest", timeout=0.5)
            except Empty:
                frame = None

            target = find_red_target(frame) if frame is not None else None
            if target is None:
                ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
                locked_frames = 0
                continue

            height, width = frame.shape[:2]
            x, y = target
            error_x = x / width - 0.5   # ขวาเป็นบวก
            error_y = 0.5 - y / height  # บนเป็นบวก

            if abs(error_x) < DEAD_ZONE and abs(error_y) < DEAD_ZONE:
                ep.gimbal.drive_speed(pitch_speed=0, yaw_speed=0)
                locked_frames += 1
                if locked_frames >= LOCK_FRAMES:
                    success = ep.blaster.fire(
                         times=1
                    )
                    print("ยิงแล้ว" if success else "คำสั่งยิงไม่สำเร็จ")
                    break
            else:
                locked_frames = 0
                yaw = max(-MAX_SPEED, min(MAX_SPEED, KP * error_x))
                pitch = max(-MAX_SPEED, min(MAX_SPEED, KP * error_y))
                ep.gimbal.drive_speed(pitch_speed=pitch, yaw_speed=yaw)

    except KeyboardInterrupt:
        print("\nหยุดโปรแกรม")
    finally:
        # ปิดการเชื่อมต่อเสมอ แม้คำสั่งหยุดหรือปิดกล้องจะเกิดข้อผิดพลาด
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
