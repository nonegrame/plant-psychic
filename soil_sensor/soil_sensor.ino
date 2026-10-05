#define SOIL_PIN 34

# 放在乾燥情況下的數值
int dry_val = 2650;
# 感測器插入水中時的數值
int wet_val = 1270;
float smooth = 0;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  Serial.println("=== 土壤濕度感測器 (Capacitive v2) ===");

  long sum = 0;
  for (int i = 0; i < 10; i++) {
    sum += analogRead(SOIL_PIN);
    delay(50);
  }
  int avg = sum / 10;

  if (avg < 100 || avg > 4000) {
    Serial.print("未偵測到感測器! (ADC avg: ");
    Serial.print(avg);
    Serial.println(")");Pap
    while (1) delay(1000);
  }

  smooth = avg;
  Serial.print("感測器已偵測 (ADC avg: ");
  Serial.print(avg);
  Serial.println(")");
  Serial.println("\n指令: d=設定乾燥點  w=設定濕潤點  r=重設  s=顯示校正值\n");
}

void loop() {
  int raw = analogRead(SOIL_PIN);
  smooth = raw * 0.1 + smooth * 0.9;

  int pct = map((int)smooth, dry_val, wet_val, 0, 100);
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;

  Serial.print("raw: ");
  Serial.print(raw);
  Serial.print("  avg: ");
  Serial.print((int)smooth);
  Serial.print("  ->  ");
  Serial.print(pct);
  Serial.println("%");

  handle_command();

  delay(200);
}

int read_avg() {
  long sum = 0;
  for (int i = 0; i < 20; i++) {
    sum += analogRead(SOIL_PIN);
    delay(10);
  }
  return sum / 20;
}

void handle_command() {
  if (!Serial.available()) return;

  char cmd = Serial.read();
  switch (cmd) {
    case 'd':
      dry_val = read_avg();
      Serial.print(">> 乾燥點: ");
      Serial.println(dry_val);
      break;
    case 'w':
      wet_val = read_avg();
      Serial.print(">> 濕潤點: ");
      Serial.println(wet_val);
      break;
    case 'r':
      dry_val = 1200;
      wet_val = 3000;
      Serial.println(">> 校正值已重設");
      break;
    case 's':
      Serial.print(">> dry=");
      Serial.print(dry_val);
      Serial.print("  wet=");
      Serial.println(wet_val);
      break;
  }
}
