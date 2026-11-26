---
sidebar_position: 3
sidebar_label: "Actuators: Servo & PWM Control"
---

# Actuators: Servo Motor & PWM Control

In BCI and neuroengineering systems, actuators allow neural signals or software triggers to effect physical change in the real world (e.g., robotic prosthetics, feedback indicators, or mechanical switches).

In this lab, we explore the **SG90 Micro Servo Motor** and understand how microcontrollers communicate angular positions via **Pulse Width Modulation (PWM)**.

---

## 🛠️ Hardware & Pinout (SG90 Servo)

| Servo Wire Color | Connection | Arduino Pin | Notes |
| :--- | :--- | :--- | :--- |
| **Brown / Black** | Ground | `GND` | Common ground |
| **Red** | Power | `5V` | Standard 5V operating voltage |
| **Orange / Yellow**| PWM Signal | `Pin 3` | Digital output pin |

---

## 📜 Available Scripts

All scripts are located in the **[`Scripts/`](https://github.com/urikorisky/BIU_BCI_Workshop_2026_StudentResources/tree/main/Scripts)** directory:

### 1. Manual PWM Demo (`Servo_Manual_PWM_Demo.ino`)
* **Location**: `Scripts/Servo_Manual_PWM_Demo/Servo_Manual_PWM_Demo.ino`
* **Concept**: Explains how hobby servos interpret a ~50 Hz PWM signal without using any external library:
  - Total frame period: **~20 ms** (50 Hz).
  - High pulse width encodes the target angle:
    - **600 µs** → `0°` (approx. 3.0% duty cycle)
    - **1500 µs** → `90°` (approx. 7.5% duty cycle)
    - **2400 µs** → `180°` (approx. 12.0% duty cycle)
* **What it does**: Sweeps the servo smoothly across its full range by manually pulsing Pin 3 HIGH and LOW with precise microsecond delays.

---

### 2. Servo Library Sweep (`Servo_Using_Library_Demo.ino`)
* **Location**: `Scripts/Servo_Using_Library_Demo/Servo_Using_Library_Demo.ino`
* **Concept**: Shows how the standard Arduino `<Servo.h>` library abstracts timer interrupts and pulse generation.
* **What it does**: Sweeps the servo angle from `0°` to `180°` and back continuously using simple `myServo.write(pos)` commands.

---

### 3. Interactive Serial Angle Control (`Servo_Using_Library_SerialControl.ino`)
* **Location**: `Scripts/Servo_Using_Library_SerialControl/Servo_Using_Library_SerialControl.ino`
* **Concept**: Introduces microcontroller serial communication (`Serial.begin(9600)`), parsing numerical input from the computer, and immediately commanding the hardware actuator.
* **What it does**:
  1. Opens the Arduino Serial Monitor at **9600 baud**.
  2. Type any target angle between `0` and `180` and press Enter.
  3. The servo rotates immediately to the requested angle.
