"""เปิด Color Lock GUI ในโหมดยิง Infrared สำหรับทดสอบโดยไม่ใช้กระสุนเจล."""

from robomaster import blaster

from Color_Shape_GUI import main


if __name__ == "__main__":
    main(fire_type=blaster.INFRARED_FIRE, fire_label="Infrared")
