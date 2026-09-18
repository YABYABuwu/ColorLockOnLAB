# ColorLockOnLAB

ระบบตรวจจับเป้าสีและรูปร่างสำหรับ **RoboMaster EP** ด้วย Python และ OpenCV
พร้อมควบคุม Gimbal ให้เล็งเป้าอัตโนมัติ ยิงได้ทั้ง Infrared และกระสุนเจล
รวมถึงบันทึกข้อมูลการทดลองเป็น CSV, JSON และกราฟ

> **คำเตือน:** โหมด `GEL BULLET` ยิงกระสุนจริง ควรสวมแว่นตานิรภัย
> และห้ามหันหุ่นยนต์เข้าหาคนหรือสัตว์ ทดสอบด้วย `INFRARED (TEST)` ก่อนเสมอ

## ความสามารถ

- ตรวจจับวงกลมสีแดง สี่เหลี่ยมสีเขียว และสี่เหลี่ยมสีเหลือง
- ยิงตามลำดับ: แดง → เขียว → เหลือง
- GUI แบบ Real-time พร้อมภาพกล้อง, Color mask, สถานะ Lock และผล HIT/MISS
- เลือก `INFRARED (TEST)` หรือ `GEL BULLET` บน GUI ได้
- กรอง Error และจำกัดความเร่งเพื่อให้ Gimbal เคลื่อนที่นุ่มขึ้น
- มีสถานะ `REACQUIRING` เมื่อเป้าหายชั่วคราวระหว่าง Gimbal เคลื่อนที่
- ยิงเมื่อเป้าอยู่ในรัศมีกลางภาพและ Gimbal นิ่งต่อเนื่องครบจำนวนเฟรม
- ใช้ Detector กลางชุดเดียวกันทั้ง Debug, GUI และโปรแกรมยิง
- บันทึกข้อมูลรายเฟรม ผลการยิง Configuration สรุปผล และกราฟ

## เป้าหมายที่รองรับ

| ลำดับ | เป้าหมาย | วิธีตรวจจับ |
| ---: | --- | --- |
| 1 | วงกลมสีแดง | HSV สีแดง 2 ช่วง + Circularity + Aspect ratio + จำนวนมุม |
| 2 | สี่เหลี่ยมสีเขียว | HSV + พื้นที่ + 4 มุม + Convex contour |
| 3 | สี่เหลี่ยมสีเหลือง | HSV + พื้นที่ + 4 มุม + Convex contour |

Mask สีใช้ `Morphological Close` และ `Open` ด้วย Kernel ขนาด `3×3`
เพื่อลดจุดรบกวนและเชื่อมช่องว่างขนาดเล็ก

## โครงสร้างไฟล์

| ไฟล์ | หน้าที่ |
| --- | --- |
| `Color_Shape_GUI.py` | GUI หลัก เล็ง ยิง และเก็บข้อมูลการทดลอง |
| `Color_Shape_GUI_IR.py` | ทางลัดสำหรับเปิด GUI ในโหมด Infrared |
| `Color_Shape_Sequence.py` | เล็งและยิงเป้าทั้ง 3 ชนิดตามลำดับ |
| `Color_Shape_Debug.py` | Debug เป้าทั้ง 3 ชนิดผ่านเว็บ โดยไม่หมุนและไม่ยิง |
| `Red_Target_Simple.py` | ตรวจจับ เล็ง และยิงวงกลมสีแดงหนึ่งครั้ง |
| `Red_Target_Debug.py` | Debug วงกลมสีแดงผ่านเว็บ โดยไม่หมุนและไม่ยิง |
| `requirements.txt` | รายการ Python packages |

## ความต้องการ

- Python 3.9 ขึ้นไป
- RoboMaster EP
- คอมพิวเตอร์ที่เชื่อมต่อ Wi-Fi ของ RoboMaster EP ในโหมด AP
- กล้องและ Gimbal ของ RoboMaster ทำงานตามปกติ

## การติดตั้ง

### Windows PowerShell

```powershell
git clone https://github.com/YABYABuwu/ColorLockOnLAB.git
cd ColorLockOnLAB

py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Linux / macOS

```bash
git clone https://github.com/YABYABuwu/ColorLockOnLAB.git
cd ColorLockOnLAB

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## การใช้งาน

### 1. ตรวจสอบ Detection โดยไม่ยิง

เชื่อมต่อ Wi-Fi ของ RoboMaster EP แล้วรัน:

```powershell
.\.venv\Scripts\python.exe .\Color_Shape_Debug.py
```

เปิด URL ที่แสดงใน Terminal หน้า Debug จะแสดง:

- ภาพกล้องและกรอบ Contour
- Color mask
- พื้นที่ (`A`), Circularity (`C`), Aspect ratio (`R`) และจำนวนมุม (`N`)
- กรอบเขียวเมื่อผ่าน และกรอบส้มเมื่อไม่ผ่าน

### 2. เปิด GUI

```powershell
.\.venv\Scripts\python.exe .\Color_Shape_GUI.py
```

ขั้นตอนการทดลอง:

1. เปิด `SETTINGS` แล้วกรอก Experiment ID, ระยะเป้า, สภาพแสง และหมายเหตุ
2. เลือก `FIRE MODE`
3. ตรวจสอบพื้นที่ด้านหน้าหุ่นยนต์
4. กด `START SEQUENCE`
5. ยืนยันโหมดยิงในกล่องข้อความ
6. กด `EMERGENCY STOP` ได้ตลอดเวลาหากพบความผิดปกติ

GUI เริ่มต้นด้วยโหมด `INFRARED (TEST)` และไม่เชื่อมต่อหรือยิงจนกว่าจะกด
`START SEQUENCE` และยืนยันอีกครั้ง

### 3. รันแบบไม่มี GUI

```powershell
# ยิงเป้าทั้งสามตามลำดับด้วยกระสุนเจล
.\.venv\Scripts\python.exe .\Color_Shape_Sequence.py

# ตรวจจับและยิงวงกลมแดงหนึ่งครั้ง
.\.venv\Scripts\python.exe .\Red_Target_Simple.py
```

## โหมดยิง

| โหมด | ค่าใน RoboMaster SDK | รายละเอียด |
| --- | --- | --- |
| `INFRARED (TEST)` | `blaster.INFRARED_FIRE` / `"ir"` | ทดสอบระบบโดยไม่ใช้กระสุนเจล |
| `GEL BULLET` | `blaster.WATER_FIRE` / `"water"` | ยิงกระสุนเจลจริง |

SDK ไม่รองรับการปรับกำลังยิง รองรับเฉพาะชนิดการยิงและจำนวนครั้ง

## การตรวจจับและการเล็ง

```text
Camera frame
    → HSV color mask
    → Morphological Close 3×3
    → Morphological Open 3×3
    → Contour and shape validation
    → Smooth Gimbal controller
    → Center and settle verification
    → Fire
```

ระบบใช้:

- Exponential Moving Average สำหรับลดการสั่นของตำแหน่งเป้า
- Slew-rate limit สำหรับจำกัดการเปลี่ยนความเร็วของ Gimbal
- รัศมี Lock `1.5%` รอบจุดเล็ง
- Lock ต่อเนื่อง `10` เฟรมก่อนยิง
- `REACQUIRING` สูงสุด `8` เฟรมเมื่อเป้าหายชั่วคราว
- จุดเล็งแนวตั้งประมาณ `35%` จากขอบบนของเป้า
- Offset เป้าสีแดงไปทางซ้าย `4.5%` ของความกว้างเป้า

Marker บน GUI แสดงจุดที่ Controller ใช้เล็งจริง

## ค่าที่ปรับได้

ค่าหลักอยู่ใน `Red_Target_Simple.py` และ `Color_Shape_Sequence.py`

| ตัวแปร | ค่าเริ่มต้น | หน้าที่ |
| --- | ---: | --- |
| `KP` | `150` | Gain ของ P Controller |
| `MAX_SPEED` | `40` | ความเร็ว Gimbal สูงสุด (องศา/วินาที) |
| `DEAD_ZONE` | `0.015` | รัศมี Lock รอบจุดเล็ง |
| `LOCK_FRAMES` | `10` | จำนวนเฟรมที่ต้อง Lock ต่อเนื่อง |
| `ERROR_SMOOTHING_ALPHA` | `0.25` | ความไวของตัวกรอง Error |
| `MAX_ACCELERATION` | `120.0` | อัตราการเปลี่ยนความเร็วสูงสุด |
| `SETTLE_SPEED` | `0.75` | ความเร็วสูงสุดที่ถือว่านิ่ง |
| `LOST_TARGET_GRACE_FRAMES` | `8` | ระยะรอ Reacquire เป้า |
| `AIM_HEIGHT_RATIO` | `0.35` | ตำแหน่งเล็งจากขอบบนของเป้า |
| `RED_AIM_X_OFFSET_RATIO` | `-0.045` | Offset แนวนอนของเป้าแดง |
| `MIN_AREA` | `800` | พื้นที่ Contour ขั้นต่ำ |

ช่วงสีใน `Color_Shape_Sequence.py`:

```python
COLOR_RANGES = {
    "yellow": ((20, 100, 70), (35, 255, 255)),
    "green": ((36, 80, 20), (95, 255, 255)),
}
```

ควรปรับ HSV ใหม่เมื่อสภาพแสง กล้อง หรือวัสดุเป้าเปลี่ยนไป

## ข้อมูลการทดลอง

แต่ละรอบจะสร้างโฟลเดอร์:

```text
experiment_logs/<timestamp>_<experiment_id>/
```

| ไฟล์ | ข้อมูล |
| --- | --- |
| `frames.csv` | Detection, จุดเล็ง, Error, Gimbal speed, Lock, Target loss และ FPS |
| `shots.csv` | เวลา Detection/Aiming, จำนวนเฟรม, ผลคำสั่งยิง และ HIT/MISS |
| `config.json` | HSV, Controller, โหมดยิง และเงื่อนไขการทดลอง |
| `summary.json` | เวลาเฉลี่ย, Detection rate และ Hit rate |
| `session_plots.png` | กราฟ Aiming error, Gimbal speed และเวลาเล็ง |

ไฟล์ Log และวิดีโอทดสอบไม่ถูก Commit เข้า Repository ตาม `.gitignore`

## ความปลอดภัย

- เริ่มทดสอบด้วย `Color_Shape_Debug.py` และ `INFRARED (TEST)`
- สวมแว่นตานิรภัยเมื่อใช้ `GEL BULLET`
- ห้ามเล็งไปที่ใบหน้า คน สัตว์ หรือสิ่งของที่เสียหายได้
- ใช้กระสุนเจลที่เตรียมตามคู่มือ RoboMaster EP
- ตรวจสอบเป้า Magazine และพื้นที่ด้านหน้าหุ่นก่อนเริ่ม
- ใช้ `EMERGENCY STOP` หรือ `Ctrl+C` เมื่อพบพฤติกรรมผิดปกติ

## การแก้ปัญหาเบื้องต้น

### ไม่พบเป้า

- ตรวจ Color mask ใน `Color_Shape_Debug.py`
- ตรวจช่วง HSV และสภาพแสง
- ตรวจว่า Contour มีพื้นที่มากกว่า `MIN_AREA`
- ตรวจจำนวนมุมและรูปทรงของเป้า

### Gimbal เคลื่อนที่เลยเป้า

- ลด `KP`, `MAX_SPEED` หรือ `MAX_ACCELERATION`
- ลด `ERROR_SMOOTHING_ALPHA` เพื่อให้นุ่มขึ้น แต่ระบบจะตอบสนองช้าลง

### ยิงคลาดตำแหน่ง

- ปรับ `AIM_HEIGHT_RATIO`
- ปรับ `RED_AIM_X_OFFSET_RATIO` สำหรับเป้าสีแดง
- ทดสอบระยะและสภาพแสงเดิมทุกครั้ง เพราะ Parallax เปลี่ยนตามระยะ

## License

โปรเจกต์นี้จัดทำเพื่อการศึกษาและการทดลองกับ RoboMaster EP
