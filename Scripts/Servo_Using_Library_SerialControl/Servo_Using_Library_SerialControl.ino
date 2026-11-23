#include <Servo.h>

Servo myServo;  // Create a servo object

void setup() {
  Serial.begin(9600);
  myServo.attach(3); // Attaches the servo on pin 3 to the servo object
  myServo.write(0); //  Start at 0 degrees
}

void loop() {
  if (Serial.available()) {
    int pos = Serial.parseInt();
    if (pos >= 0 && pos <= 180) {
      myServo.write(pos);
      Serial.readStringUntil('\n'); // Empties the incoming buffer (stupid bug)
      Serial.println(pos);
      delay(15);          // waits 15ms for the servo to reach the position
    }
  }
}