// Emre Kalem | Eskisehir, Turkiye | 2025
// PCA9685 servo driver version (NO deadband, NO smoothing)

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

const int NUM_SERVOS = 7;
const int NUM_ANGLES = 6;

uint8_t servoChannel[NUM_SERVOS] = { 0, 1, 2, 3, 4, 5, 6 };

const uint16_t SERVO_FREQ = 50;
const uint16_t SERVOMIN  = 102;  // ~500µs
const uint16_t SERVOMAX  = 512;  // ~2500µs

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

int angleValues[NUM_ANGLES];

uint16_t angleToPulse(int angleDeg)
{
  angleDeg = constrain(angleDeg, 0, 180);
  return map(angleDeg, 0, 180, SERVOMIN, SERVOMAX);
}

void writeServoAngle(uint8_t ch, int angleDeg)
{
  pwm.setPWM(ch, 0, angleToPulse(angleDeg));
}

bool readAnglesFromSerial(int *outAngles, int count)
{
  static char buf[80];
  static uint8_t idx = 0;

  while (Serial.available() > 0)
  {
    char c = (char)Serial.read();

    // end of line
    if (c == '\n')
    {
      buf[idx] = '\0';
      idx = 0;

      // parse exactly "a b c d e f"
      int parsed = 0;
      char *p = buf;

      for (int i = 0; i < count; i++)
      {
        // skip spaces
        while (*p == ' ') p++;
        if (*p == '\0') return false;

        outAngles[i] = strtol(p, &p, 10);
        outAngles[i] = constrain(outAngles[i], 0, 180);
        parsed++;
      }
      return (parsed == count);
    }
    else if (c != '\r')
    {
      if (idx < sizeof(buf) - 1)
        buf[idx++] = c;
      else
        idx = 0; // overflow -> reset
    }
  }
  return false;
}

void setup()
{
  Serial.begin(115200);

  Wire.begin();
  pwm.begin();
  pwm.setPWMFreq(SERVO_FREQ);

  for (int i = 0; i < NUM_SERVOS; i++)
    writeServoAngle(servoChannel[i], 90);

  delay(200);
}

void loop()
{
  if (readAnglesFromSerial(angleValues, NUM_ANGLES))
  {
    writeServoAngle(servoChannel[0], angleValues[0]);          // Root
    writeServoAngle(servoChannel[1], angleValues[1]);          // Arm A1
    writeServoAngle(servoChannel[2], 180 - angleValues[1]);    // Arm A2 mirror
    writeServoAngle(servoChannel[3], angleValues[2]);          // Arm B
    writeServoAngle(servoChannel[4], angleValues[3]);          // Wrist A
    writeServoAngle(servoChannel[5], angleValues[4]);          // Wrist B
    writeServoAngle(servoChannel[6], angleValues[5]);          // Gripper
  }
}
