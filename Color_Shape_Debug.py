"""Debug สามเป้าผ่านเว็บ ดูอย่างเดียว ไม่สั่งกิมบอลหรือยิง

รัน: .venv/bin/python Color_Shape_Debug.py แล้วเปิด URL ใน Terminal
ใช้ตัวตรวจจับเดียวกับ Color_Shape_Sequence.py
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty
from urllib.parse import urlsplit, parse_qs
import threading
import time

import cv2
from robomaster import robot
from Color_Shape_Sequence import TARGETS, find_rectangle, find_red_target
from Red_Target_Debug import draw_debug


latest_images = {}
status = "กำลังรอภาพจากกล้อง"

PAGE = """<!doctype html><html lang="th"><meta charset="utf-8">
<title>Color and shape debug</title>
<style>body{font-family:sans-serif;background:#171717;color:#eee;margin:20px}
select{font-size:18px;padding:8px}img{max-width:100%;display:block;margin-top:15px}</style>
<h2>ตรวจสีและรูปร่าง — ไม่มีการหมุนหรือยิง</h2>
<label>เลือกเป้า <select id="target">
<option value="red">1. วงกลมสีแดง</option>
<option value="green">2. สี่เหลี่ยมสีเขียว</option>
<option value="yellow">3. สี่เหลี่ยมสีเหลือง</option>
</select></label>
<p>ซ้าย: ภาพกล้องพร้อมกรอบตรวจจับ | ขวา: สีที่กรองได้เป็นสีขาว</p>
<p>กรอบเขียว = ผ่าน, กรอบส้ม = ไม่ผ่าน, กากบาท = เป้าที่เลือกชิ้นใหญ่สุด</p>
<p>A = พื้นที่, C = ความกลม, R = สัดส่วนด้านสั้น/ยาว, N = จำนวนมุม<br>
small = เล็กไป, corners = จำนวนมุมไม่ผ่าน, not convex = รูปเว้า<br>
roundness / aspect ใช้ตัดสินเฉพาะวงกลม ส่วนสี่เหลี่ยมต้องมี 4 มุม</p>
<p id="status">กำลังโหลด...</p><p id="error"></p><img id="frame" alt="รอภาพ">
<script>
let previousUrl;
async function refresh() {
  const color = document.getElementById('target').value;
  try {
    const state = await fetch('/status', {cache:'no-store'});
    document.getElementById('status').textContent = await state.text();
    const response = await fetch('/frame.jpg?color='+color+'&t='+Date.now());
    if (!response.ok) throw new Error(response.status === 503 ?
      'ยังไม่มีภาพจากกล้อง' : 'โหลดภาพไม่สำเร็จ: '+response.status);
    const blob = await response.blob();
    if (color === document.getElementById('target').value) {
      const url = URL.createObjectURL(blob);
      document.getElementById('frame').src = url;
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      previousUrl = url;
      document.getElementById('error').textContent = '';
    }
  } catch (error) {
    document.getElementById('error').textContent = error.message;
  } finally { setTimeout(refresh, 300); }
}
refresh();
</script></html>"""


class Viewer(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/":
            data, content_type = PAGE.encode(), "text/html; charset=utf-8"
        elif url.path == "/status":
            data, content_type = status.encode(), "text/plain; charset=utf-8"
        elif url.path == "/frame.jpg":
            color = parse_qs(url.query).get("color", ["red"])[0]
            if color not in ("red", "yellow", "green"):
                self.send_error(400, "Unknown color")
                return
            data = latest_images.get(color)
            if data is None:
                self.send_error(503, "Waiting for camera")
                return
            content_type = "image/jpeg"
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


def make_debug_images(frame):
    """สร้างภาพครบสามสีจากเฟรมเดียวกัน ใช้เกณฑ์เดียวกับโปรแกรมยิง"""
    images, summaries = {}, []
    for name, color in TARGETS:
        detection = (find_red_target(frame, debug=True) if color == "red"
                     else find_rectangle(frame, color, debug=True))
        image, target = draw_debug(frame, detection)
        ok, jpeg = cv2.imencode(".jpg", image)
        if not ok:
            raise RuntimeError("แปลงภาพ JPEG ไม่สำเร็จ")
        images[color] = jpeg.tobytes()
        summaries.append(name + (": พบ" if target is not None else ": ไม่พบ"))
    return images, " | ".join(summaries)


def main():
    global latest_images, status
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
                latest_images = {}
                status = "ไม่ได้รับภาพจากกล้อง — ภาพที่ค้างบนหน้าเว็บเป็นภาพเก่า"
                continue
            latest_images, summary = make_debug_images(frame)
            status = time.strftime("%H:%M:%S") + " | " + summary
    except KeyboardInterrupt:
        print("\nหยุด Debug")
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
