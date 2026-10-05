/*
 * 植物監控系統 - ESP32 物聯網感測器
 * ====================================
 * 硬體：
 *   - ESP32 NodeMCU-32S（主控）
 *   - DHT22（空氣溫濕度）
 *   - BH1750 / GY-30（光照強度，I2C）
 *   - 電容式土壤濕度感測器（ADC）
 *   - SSD1306 OLED 128x64（I2C 顯示）
 *
 * 流程：開機 → 連 Wi-Fi → 讀取感測器 → POST JSON 到雲端 → 休眠 5 分鐘
 */

// ────── 引入函式庫 ──────
#include <WiFi.h>         // Wi-Fi 連線
#include <Wire.h>         // I2C 通訊（OLED、BH1750 共用）
#include <HTTPClient.h>   // HTTP POST 傳資料到雲端
#include <ArduinoJson.h>  // 把感測資料包成 JSON 格式
#include <DHT.h>          // DHT22 溫濕度感測器
#include <U8g2lib.h>      // SSD1306 OLED 螢幕顯示
#include "secrets.h"

// ────── 休眠設定 ──────
#define uS_TO_S_FACTOR 1000000ULL  // 1 秒 = 1000000 微秒
#define SLEEP_MINUTES  5           // 每次休眠 5 分鐘後再醒來測量

// ────── 感測器腳位定義 ──────
#define DHTPIN      17         // DHT22 資料腳位 → GPIO17
#define DHTTYPE     DHT22      // 感測器型號
#define BH1750_ADDR 0x23      // BH1750 的 I2C 位址（0x23 或 0x5C）
#define SOIL_PIN    34        // 土壤濕度感測器 → GPIO34（ADC）

// ────── 土壤濕度校正值（乾／濕對應的 ADC 讀數） ──────
int dry_val = 2650;  // 空氣中乾燥時的 ADC 值
int wet_val = 1270;  // 完全泡水時的 ADC 值（數值越小越濕）

// ────── 建立物件 ──────
DHT dht(DHTPIN, DHTTYPE);                                     // 溫濕度感測器
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);  // OLED 螢幕

// ════════════════════════════════════════════════════════════
//  setup() - ESP32 開機後只會執行一次
// ════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);     // 啟動序列埠監控（115200 baud）
  Wire.begin(21, 22);       // 啟動 I2C：SDA=GPIO21, SCL=GPIO22

  dht.begin();              // 初始化 DHT22
  bh1750_init();            // 初始化光照感測器
  u8g2.begin();             // 初始化 OLED 螢幕

  connectWiFi();            // ① 連線 Wi-Fi（嘗試 2 組）
  postSensorData();         // ② 讀取感測器並 POST 到雲端

  // ③ 顯示休眠提示，1 秒後進入 deep sleep
  String sleepMsg = "Sleep " + String(SLEEP_MINUTES) + "m";
  showMsg(sleepMsg.c_str());
  delay(1000);

  // ④ 設定定時喚醒（SLEEP_MINUTES 分鐘後自動重開機）
  esp_sleep_enable_timer_wakeup(SLEEP_MINUTES * 60 * uS_TO_S_FACTOR);
  esp_deep_sleep_start();   // 進入深度休眠
}

// ════════════════════════════════════════════════════════════
//  loop() - 因為用了 deep sleep，這一段永遠不會執行
// ════════════════════════════════════════════════════════════
void loop() {
}

// ════════════════════════════════════════════════════════════
//  connectWiFi() - 依序嘗試連線 2 組 Wi-Fi
// ════════════════════════════════════════════════════════════
void connectWiFi() {
  showMsg("WiFi...");

  // ── 第 1 組：一般 WPA2-Personal（家用／個人熱點） ──
  Serial.print("\nTry ob_ov ");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 20) {  // 最多等 10 秒
    delay(500);        // 每 0.5 秒檢查一次
    Serial.print("."); // 輸出點點表示正在等
    retry++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" OK");
    return; // 連上了，結束
  }

  // ── 第 2 組：企業網路 WPA2-Enterprise（TTLS + 內部 PAP 認證） ──
  Serial.print(" fail\nTry tm-hq ");
  WiFi.begin(WIFI2_SSID, WPA2_AUTH_TTLS,   // outer: TLS 加密通道
             NULL, WIFI2_USER, WIFI2_PASS,  // inner: PAP 帳號密碼認證
             NULL, NULL, NULL,              // 不使用 CA / client cert
             0, "PAP");                     // channel=自動, phase2=PAP

  retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 5) {  // 最多等 2.5 秒
    delay(500);
    Serial.print(".");
    retry++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" OK");
  } else {
    Serial.println(" fail");
  }
}

// ════════════════════════════════════════════════════════════
//  postSensorData() - 讀取所有感測器 → 打包 JSON → HTTP POST
// ════════════════════════════════════════════════════════════
void postSensorData() {
  // 沒連上 Wi-Fi 就跳過
  if (WiFi.status() != WL_CONNECTED) {
    showMsg("WiFi fail");
    return;
  }

  // ── 讀取四組感測器數值 ──
  float t = dht.readTemperature();        // 空氣溫度 (°C)
  float h = dht.readHumidity();           // 空氣濕度 (%)
  float l = bh1750_read();                // 光照強度 (lux) ── 從 I2C 讀取
  float s_raw = (float)analogRead(SOIL_PIN);  // 土壤 ADC 原始值 (0-4095)

  // ── 把 ADC 換算成土壤濕度百分比（線性映射） ──
  float s = (float)(dry_val - s_raw) / (float)(dry_val - wet_val) * 100.0;
  if (s < 0) s = 0;    // 不低於 0%
  if (s > 100) s = 100; // 不高於 100%

  // ── 任一感測器讀取失敗就回報錯誤 ──
  if (isnan(t) || isnan(h) || isnan(l) || isnan(s)) {
    Serial.println("Sensor err");
    showMsg("Sensor err");
    return;
  }

  // ── 把資料包成 JSON ──
  StaticJsonDocument<256> doc;  // 256 bytes 就夠用了
  doc["temperature"] = t;       // 溫度
  doc["humidity"]    = h;       // 濕度
  doc["lux"]         = l;       // 光照
  doc["soil_moist"]  = s;       // 土壤濕度

  String body;
  serializeJson(doc, body);     // JSON → 字串，準備 POST

  // ── 發送 HTTP POST ──
  HTTPClient http;
  http.begin(SERVER_URL);                        // 指定伺服器網址
  http.addHeader("Content-Type", "application/json"); // 告訴伺服器：這是 JSON

  int code = http.POST(body);                    // 送出 POST，取得回應狀態碼

  // ── 處理回應 ──
  if (code == 200) {  // 200 表示成功
    Serial.printf("OK  | T:%.1f H:%.1f L:%.1f S:%.1f%%\n", t, h, l, s);
    showMsg("OK");
  } else {            // 其他狀態碼表示有問題
    Serial.printf("ERR %d | %s\n", code, http.getString().c_str());
    String errMsg = "HTTP " + String(code);
    showMsg(errMsg.c_str());
  }

  http.end();  // 關閉 HTTP 連線
}

// ════════════════════════════════════════════════════════════
//  bh1750_init() - 初始化光照感測器（I2C）
//  先重設（0x01），再設定連續高解析度模式（0x10）
// ════════════════════════════════════════════════════════════
void bh1750_init() {
  Wire.beginTransmission(BH1750_ADDR); // 開始跟 BH1750 通訊
  Wire.write(0x01);                    // 發送指令：Power On（開機）
  Wire.endTransmission();              // 結束傳輸

  Wire.beginTransmission(BH1750_ADDR);
  Wire.write(0x10);                    // 連續高解析度模式（1 lux 精度）
  Wire.endTransmission();
}

// ════════════════════════════════════════════════════════════
//  bh1750_read() - 讀取光照值（lux），回傳浮點數
// ════════════════════════════════════════════════════════════
float bh1750_read() {
  Wire.beginTransmission(BH1750_ADDR);
  Wire.write(0x10);                    // 設定連續高解析度模式
  Wire.endTransmission();

  delay(180);                          // 等待感測器完成測量（至少 120ms）

  Wire.requestFrom(BH1750_ADDR, 2);   // 要求讀取 2 bytes 資料
  if (Wire.available() == 2) {
    uint16_t raw = Wire.read();       // 高位元組
    raw <<= 8;                        // 往左移 8 bit
    raw |= Wire.read();               // 合併低位元組
    return raw / 1.2;                 // 公式：lux = raw / 1.2
  }
  return NAN;  // 讀取失敗回傳 NaN（Not a Number）
}

// ════════════════════════════════════════════════════════════
//  showMsg() - 在 OLED 螢幕上顯示一行文字（6x10 字型）
// ════════════════════════════════════════════════════════════
void showMsg(const char* msg) {
  u8g2.clearBuffer();                     // 清除畫面緩衝區
  u8g2.setFont(u8g2_font_6x10_tf);       // 設定字型（寬 6 x 高 10）
  u8g2.drawStr(3, 32, msg);              // 在 (3, 32) 畫出文字
  u8g2.sendBuffer();                     // 把緩衝區內容送到螢幕顯示
}
