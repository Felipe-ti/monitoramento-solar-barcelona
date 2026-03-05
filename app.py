import os
import time
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError

class SolarMonitor:
    def __init__(self):
        self.db_params = {
            "user": os.getenv("POSTGRES_USER"),
            "password": os.getenv("POSTGRES_PASSWORD"),
            "host": "db",
            "port": "5432",
            "database": os.getenv("POSTGRES_DB")
        }
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        # Regras de Negócio (Limites Críticos para Barcelona)
        self.MIN_VOLTAGE = 228.0 # Tensão mínima aceitável
        self.MAX_VOLTAGE = 232.0 # Tensão máxima aceitável
        
        # Gestão de Estado (Evita spam no Telegram)
        self.alert_active = False
        self.last_alert_time = 0
        self.COOLDOWN_SECONDS = 60 # Em produção usaríamos 3600 (1 hora)

    def send_telegram_msg(self, text):
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            print(f"❌ Erro ao comunicar com a API do Telegram: {e}")

    def init_db(self):
        """Garante que a estrutura existe antes de tentar ler."""
        create_table_query = """
        CREATE TABLE IF NOT EXISTS inverter_metrics (
            id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            voltage_v NUMERIC(6, 2) NOT NULL,
            current_a NUMERIC(6, 2) NOT NULL,
            power_w NUMERIC(8, 2) NOT NULL,
            status VARCHAR(50) NOT NULL
        );
        """
        try:
            with psycopg2.connect(**self.db_params) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(create_table_query)
                conn.commit()
        except OperationalError:
            print("⏳ A aguardar que o PostgreSQL inicie...")
            time.sleep(5)
            self.init_db()

    def fetch_latest_metric(self):
        """Lê o último registo inserido pelo sensor."""
        query = "SELECT * FROM inverter_metrics ORDER BY timestamp DESC LIMIT 1;"
        try:
            with psycopg2.connect(**self.db_params) as conn:
                # RealDictCursor retorna os dados como um Dicionário em vez de Tupla
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(query)
                    return cursor.fetchone()
        except Exception as e:
            print(f"❌ Falha ao ler dados: {e}")
            return None

    def analyze_data(self, data):
        """Aplica as regras de engenharia e decide se deve alertar."""
        if not data:
            return

        voltage = float(data['voltage_v'])
        power = float(data['power_w'])
        current_time = time.time()

        # Condição de Anomalia: Tensão fora dos limites
        is_anomalous = voltage < self.MIN_VOLTAGE or voltage > self.MAX_VOLTAGE

        if is_anomalous:
            # Só envia alerta se não houver um ativo OU se o cooldown já passou
            if not self.alert_active or (current_time - self.last_alert_time) > self.COOLDOWN_SECONDS:
                msg = (
                    f"⚠️ *ALERTA CRÍTICO: Inversor Solar*\n\n"
                    f"Anomalia detetada na rede de Barcelona.\n"
                    f"⚡ *Tensão Atual:* {voltage}V\n"
                    f"🔋 *Potência:* {power}W\n"
                    f"🕒 *Registo:* {data['timestamp'].strftime('%H:%M:%S UTC')}"
                )
                self.send_telegram_msg(msg)
                self.alert_active = True
                self.last_alert_time = current_time
                print(f"🚨 Alerta disparado! Tensão: {voltage}V")
        else:
            # Condição de Recuperação: Sistema voltou ao normal
            if self.alert_active:
                msg = (
                    f"✅ *SISTEMA RECUPERADO*\n\n"
                    f"A tensão estabilizou em {voltage}V. Monitorização normalizada."
                )
                self.send_telegram_msg(msg)
                self.alert_active = False
                print("✅ Sistema normalizado. Alerta desativado.")

    def run(self):
        print("🚀 Monitorização Analítica Iniciada...")
        self.init_db()
        self.send_telegram_msg("🛡️ *Sistema de Monitorização Fotovoltaica:* Online e a analisar métricas.")
        
        try:
            while True:
                latest_data = self.fetch_latest_metric()
                self.analyze_data(latest_data)
                # Verifica a base de dados a cada 10 segundos
                time.sleep(10)
        except KeyboardInterrupt:
            print("A encerrar o serviço de monitorização...")

if __name__ == "__main__":
    monitor = SolarMonitor()
    # Pequeno atraso para dar tempo ao sensor de inserir os primeiros dados
    time.sleep(5)
    monitor.run()