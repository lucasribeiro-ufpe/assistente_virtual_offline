#ifdef ARDUINO_ARCH_ESP32
#include <WiFi.h>
#include <Preferences.h>
#else
#include <ESP8266WiFi.h>
#endif

#include <PubSubClient.h>
#include <ArduinoJson.h>

// ========= WIFI / MQTT =========
const char* ssid = "Ana&Miro";
const char* password = "01142120";

const char* MQTT_HOST = "192.168.0.25";
const int MQTT_PORT = 1883;

const char* CLIENT_ID = "esp_32_quarto";

String TOPIC_RESETMICRO = String("assistente/") + CLIENT_ID + "/reset";
String TOPIC_CFG = String("assistente/") + CLIENT_ID + "/config_io";
String TOPIC_CMD = String("assistente/comando");


WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

// ========= PREFERENCES =========
Preferences prefs;

// ========= MAPA =========
const int Tamanho_Mapa = 40;
String mapaObjetoLocal[Tamanho_Mapa];
int mapaPinoGpio[Tamanho_Mapa];
int ContagemPinosAtribuidos = 0;

// ========= ENCONTRAR PINO POR OBJETO LOCAL  =========
int EncontrarPinoPorObjetoLocal(const String& Objeto_Local) {
  for (int i = 0; i < ContagemPinosAtribuidos; i++) {
    if (mapaObjetoLocal[i] == Objeto_Local) return mapaPinoGpio[i];
  }
  return -1;
}

// ========= SALVAR CONFIG =========
void salvarConfig() {
  prefs.begin("config", false);

  prefs.putInt("count", ContagemPinosAtribuidos);

  for (int i = 0; i < ContagemPinosAtribuidos; i++) {
    prefs.putString(("obj" + String(i)).c_str(), mapaObjetoLocal[i]);
    prefs.putInt(("pin" + String(i)).c_str(), mapaPinoGpio[i]);
  }

  prefs.end();
}

// ========= CARREGAR CONFIG =========
void carregarConfig() {
  prefs.begin("config", true);

  ContagemPinosAtribuidos = prefs.getInt("count", 0);

  for (int i = 0; i < ContagemPinosAtribuidos; i++) {
    mapaObjetoLocal[i] = prefs.getString(("obj" + String(i)).c_str(), "");
    mapaPinoGpio[i] = prefs.getInt(("pin" + String(i)).c_str(), -1);

    if (mapaPinoGpio[i] >= 0) {
      pinMode(mapaPinoGpio[i], OUTPUT);
      digitalWrite(mapaPinoGpio[i], LOW);
    }
  }

  prefs.end();

  Serial.println("Config carregada da flash");
}

// ========= LIMPAR CONFIG =========
void limparConfig() {
  prefs.begin("config", false);
  prefs.clear();
  prefs.end();

  Serial.println("Configuracoes apagadas");
}


// ========= CONFIGURAR GPIO =========
void configurarGPIO(int gpio, String objeto, String local) {
  String Objeto_Local = objeto + "_" + local;

  for (int i = 0; i < ContagemPinosAtribuidos; i++) {
    if (mapaObjetoLocal[i] == Objeto_Local) {
      mapaPinoGpio[i] = gpio;
      pinMode(gpio, OUTPUT);
      digitalWrite(gpio, LOW);
      salvarConfig();
      Serial.printf("ATUALIZADO: %s -> GPIO %d\n", Objeto_Local.c_str(), gpio);
      return;
    }
  }

  if (ContagemPinosAtribuidos < Tamanho_Mapa) {
    mapaObjetoLocal[ContagemPinosAtribuidos] = Objeto_Local;
    mapaPinoGpio[ContagemPinosAtribuidos] = gpio;
    ContagemPinosAtribuidos++;

    pinMode(gpio, OUTPUT);
    digitalWrite(gpio, LOW);

    salvarConfig();

    Serial.printf("CONFIGURADO: %s -> GPIO %d\n", Objeto_Local.c_str(), gpio);
  } else {
    Serial.println("Mapa cheio");
  }
}

// ========= EXECUTAR COMANDO =========
void executarComando(String acao, String objeto, String local) {
  String Objeto_Local = objeto + "_" + local;

  int gpio = EncontrarPinoPorObjetoLocal(Objeto_Local);

  if (gpio < 0) {
    Serial.printf("Sem mapeamento para %s\n", Objeto_Local.c_str());
    return;
  }

  if (acao == "ligar") {
    digitalWrite(gpio, HIGH);
    Serial.printf("LIGAR: %s (GPIO %d)\n", Objeto_Local.c_str(), gpio);
  }
  else if (acao == "desligar") {
    digitalWrite(gpio, LOW);
    Serial.printf("DESLIGAR: %s (GPIO %d)\n", Objeto_Local.c_str(), gpio);
  }
  else {
    Serial.println("Acao nao suportada");
  }
}

// ========= MQTT CALLBACK =========
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String topico = String(topic);

  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];

  Serial.print("Mensagem: ");
  Serial.println(msg);

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, msg)) {
    Serial.println("JSON invalido");
    return;
  }

  if (topico == TOPIC_CFG) {
    configurarGPIO(
      doc["gpio"] | -1,
      doc["objeto"] | "",
      doc["local"] | ""
    );
  }

  if (topico == TOPIC_CMD) {
    executarComando(
      doc["acao"] | "",
      doc["objeto"] | "",
      doc["local"] | ""
    );
  }
  if (topico == TOPIC_RESETMICRO) {
  if (msg == "Resetar") {
    Serial.println("Reset recebido via MQTT");

    limparConfig();

    prefs.begin("system", false);
    prefs.putInt("resetCount", 0);
    prefs.end();

    delay(1000);
    ESP.restart();
  }
}
}

// ========= CONEXÕES =========
void FuncaoConectarWifi() {
  WiFi.begin(ssid, password);

  Serial.print("Conectando WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi conectado");
}

void FuncaoConectarMQTT() {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);

  while (!mqtt.connected()) {
    Serial.print("Conectando MQTT...");
    if (mqtt.connect(CLIENT_ID)) {
      Serial.println("OK");

      mqtt.subscribe(TOPIC_CFG.c_str());
      mqtt.subscribe(TOPIC_CMD.c_str());
      mqtt.subscribe(TOPIC_RESETMICRO.c_str());
    } else {
      Serial.println("Erro MQTT");
      delay(1500);
    }
  }
}

// ========= SETUP =========
void setup() {
  Serial.begin(9600);
  delay(100);

  carregarConfig();   //CARREGAR DA FLASH PINOS E OBJETOS_LOCAIS JÁ CONFIGURADOS

  FuncaoConectarWifi();
  FuncaoConectarMQTT();
}

// ========= LOOP =========
void loop() {
  if (!mqtt.connected()) FuncaoConectarMQTT();
  mqtt.loop();
}