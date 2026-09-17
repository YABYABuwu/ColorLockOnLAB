"""ดูภาพและเหตุผลที่ตรวจไม่ผ่าน โดยไม่สั่งกิมบอลหรือยิง

รัน .venv/bin/python Red_Target_Debug.py แล้วเปิด URL ที่แสดง
ซ้าย: ภาพพร้อมค่าตรวจจับ / ขวา: ส่วนสีแดงที่ผ่านการกรองเป็นสีขาว
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty
from urllib.parse import urlsplit
import threading
import time

import cv2
from robomaster import robot
from Red_Target_Simple import find_red_target


latest_jpeg = None
status = "Waiting for camera"


class Viewer(BaseHTTPRequestHandler):
    def do_GET(self):
        # หน้าเว็บเติม ?t=... เพื่อไม่ใช้ภาพเก่า ต้องแยกออกก่อนเทียบเส้นทาง
        path = urlsplit(self.path).path
        if path == "/frame.jpg":
            data = latest_jpeg
            content_type = "image/jpeg"
            if data is None:
                self.send_error(503, "Waiting for camera")
                return
        elif path == "/status":
            data = status.encode()
            content_type = "text/plain; charset=utf-8"
        elif path == "/":
            data = b"""<!doctype html><meta charset="utf-8">
<title>Red circle debug</title>
<h2>Camera (left) / red mask (right)</h2>
<p>Green = passed, orange = rejected. A = area, C = circularity,
R = aspect ratio, N = corners.</p>
<p id="status"></p><img id="frame" style="max-width:100%">
<script>
setInterval(async () => {
  document.getElementById('frame').src='/frame.jpg?t='+Date.now();
  document.getElementById('status').textContent=await (await fetch('/status')).text();
}, 300);
</script>"""
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


def draw_debug(frame, detection=None):
    # รับผลตรวจจากสีอื่นได้ เพื่อใช้รูปแบบแสดงผลเดียวกัน
    target, mask, details = (find_red_target(frame, debug=True)
                             if detection is None else detection)
    image = frame.copy()
    # แสดงรายละเอียด 5 ชิ้นใหญ่สุดเพื่อไม่ให้ข้อความแน่นเกินไป
    for contour, area, circularity, ratio, corners, reasons in sorted(
        details, key=lambda item: item[1], reverse=True
    )[:5]:
        color = (0, 165, 255) if reasons else (0, 255, 0)
        cv2.drawContours(image, [contour], -1, color, 2)
        x, y, _, _ = cv2.boundingRect(contour)
        label = f"A={area:.0f} C={circularity:.2f} R={ratio:.2f} N={corners}"
        cv2.putText(image, label, (x, max(18, y - 22)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        cv2.putText(image, ','.join(reasons) or 'PASS', (x, max(36, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    if target is not None:
        cv2.drawMarker(image, tuple(map(int, target)), (0, 255, 0))
    return cv2.hconcat([image, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)]), target


def main():
    global latest_jpeg, status
    ep = robot.Robot()
    server = None
    camera_started = False
    try:
        ep.initialize(conn_type="ap")
        camera_started = ep.camera.start_video_stream(display=False)
        if not camera_started:
            raise RuntimeError("เปิดกล้องไม่สำเร็จ")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Viewer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"ดูภาพที่ http://127.0.0.1:{server.server_port}/ (Ctrl+C เพื่อหยุด)")
        while True:
            try:
                frame = ep.camera.read_cv2_image(strategy="newest", timeout=0.5)
            except Empty:
                frame = None
            if frame is None:
                status = "No camera frame: check connection (display may show old image)"
                continue
            image, target = draw_debug(frame)
            ok, jpeg = cv2.imencode(".jpg", image)
            if ok:
                latest_jpeg = jpeg.tobytes()
            status = f"Updated {time.strftime('%H:%M:%S')} | " + (
                "Circle detected" if target is not None else "No matching circle"
            )
    except KeyboardInterrupt:
        print("\nหยุดตรวจสอบ")
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        try:
            if camera_started:
                ep.camera.stop_video_stream()
        finally:
            ep.close()


if __name__ == "__main__":
    main()
