// ============================================================================
// Manual PWM Servo Control (SG90) - Without Servo.h
// ============================================================================
// Demonstrates how a hobby servo interprets a ~50 Hz PWM signal:
//   - Period       : ~20 ms (~50 Hz)
//   - Pulse width  : HIGH time encodes the target angle
//       600 us  ->   0 deg  ( 3.0% duty cycle)
//      1500 us  ->  90 deg  ( 7.5% duty cycle)
//      2400 us  -> 180 deg  (12.0% duty cycle)
//       (Every +10 us of HIGH time rotates the SG90 by +1 degree.)
//
// Note: The textbook RC servo spec is 1000-2000 us (5%-10% duty cycle),
//       while most SG90 servos (and Arduino's Servo.h) use ~544-2400 us
//       to cover the full 0-180 degree arc.
// ============================================================================

int pulse = 600;   // Current HIGH pulse width (us)
int step  = 20;    // Change per cycle (controls sweep speed)

void setup() {
  pinMode(3, OUTPUT);
}

void loop() {
  digitalWrite(3, HIGH);     // Start PWM pulse
  delayMicroseconds(pulse);  // Hold HIGH for target angle
  digitalWrite(3, LOW);      // End PWM pulse
  delay(20);                 // Wait ~20 ms until next cycle (~50 Hz)

  pulse += step;
  if (pulse >= 2400 || pulse <= 600){
    step = -step;
    delay(1000);
  } 
}
