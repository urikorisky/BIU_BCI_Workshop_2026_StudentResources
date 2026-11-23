#include <Servo.h>

Servo myServo;  // Create a servo object

void setup() {
  myServo.attach(3); // Attaches the servo on pin 3 to the servo object
}

void loop() {
  for (int pos = 0; pos <= 180; pos += 1) { // goes from 0 degrees to 180 degrees
    myServo.write(pos); // tell servo to go to position in variable 'pos'
    delay(50);          // waits 15ms for the servo to reach the position
  }
  for (int pos = 180; pos >= 0; pos -= 1) { // goes from 180 degrees to 0 degrees
    myServo.write(pos); // tell servo to go to position in variable 'pos'
    delay(15);          // waits 15ms for the servo to reach the position
  }
}