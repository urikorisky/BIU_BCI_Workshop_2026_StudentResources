// ============================================================================
// Serial-Controlled Servo (SG90) - Works with Serial Monitor OR Python
// ============================================================================
// Listens on Serial (115200 baud) for a number between 0.0 and 1.0 followed
// by a newline (e.g. "0.50\n"), and maps it to 0-180 degrees on pin 3.
//
// How to use:
//   1. Upload this sketch from the Arduino IDE.
//   2. Test manually in Serial Monitor (set baud to 115200):
//      type 0, 0.5, or 1 and press Enter.
//   3. Close Serial Monitor, then run mic_to_servo.py with your COM port.
// ============================================================================

#include <Servo.h>

Servo myServo;

void setup() {
  Serial.begin(115200);   // Must match BAUD_RATE in mic_to_servo.py
  Serial.setTimeout(50);  // Fast fallback if Serial Monitor has "No line ending"
  myServo.attach(3);
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      int angle = line.toFloat() * 180;
      myServo.write(constrain(angle, 0, 180));
    }
  }
}
