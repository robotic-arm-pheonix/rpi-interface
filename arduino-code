// Emre Kalem | Eskisehir, Turkiye | 2025
// PCA9685 servo driver version (NO deadband, NO smoothing)

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

// --- CONFIGURATION ---

const int NUM_SERVOS = 7;
const int NUM_ANGLES = 6;

// FINAL correct PCA9685 channel mapping (0-based)
uint8_t servoChannel[NUM_SERVOS] = { 0, 1, 2, 3, 4, 5, 6 };
/*
0 = Root
1 = Arm A1
2 = Arm A2 (mirrors A1)
3 = Arm B
<!-- 4 = Wrist A
5 = Wrist B
6 = Gripper -->
*/

// PCA9685 settings
const uint16_t SERVO_FREQ = 50;
const uint16_t SERVOMIN  = 102;  // ~500µs
const uint16_t SERVOMAX  = 512;  // ~2500µs

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

int angleValues[NUM_ANGLES];

// --- Helpers ---

uint16_t angleToPulse(int angleDeg)
{
  angleDeg = constrain(angleDeg, 0, 180);
  return map(angleDeg, 0, 180, SERVOMIN, SERVOMAX);
}

void writeServoAngle(uint8_t ch, int angleDeg)
{
  uint16_t pulse = angleToPulse(angleDeg);
  pwm.setPWM(ch, 0, pulse);
}

// --- SETUP ---

void setup()
{
  Serial.begin(9600);

  Wire.begin();
  pwm.begin();
  pwm.setPWMFreq(SERVO_FREQ);

  // Initialize all servos to center
  for (int i = 0; i < NUM_SERVOS; i++)
  {
    writeServoAngle(servoChannel[i], 90);
  }

  delay(200);
}

// --- LOOP ---

void loop()
{
  // Expecting: "90 90 90 90 90 90\n"
  if (Serial.available() > 0)
  {
    for (int i = 0; i < NUM_ANGLES; i++)
    {
      angleValues[i] = Serial.parseInt();
      angleValues[i] = constrain(angleValues[i], 0, 180);
    }

    while (Serial.available()) Serial.read();

    // --- Apply angles directly ---

    writeServoAngle(servoChannel[0], angleValues[0]);          // Root
    writeServoAngle(servoChannel[1], angleValues[1]);          // Arm A1
    writeServoAngle(servoChannel[2], 180 - angleValues[1]);    // Arm A2 mirror
    writeServoAngle(servoChannel[3], angleValues[2]);          // Arm B
    writeServoAngle(servoChannel[4], angleValues[3]);          // Wrist A
    writeServoAngle(servoChannel[5], angleValues[4]);          // Wrist B
    writeServoAngle(servoChannel[6], angleValues[5]);          // Gripper
  }
}
