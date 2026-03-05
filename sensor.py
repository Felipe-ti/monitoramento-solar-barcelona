import os
import time
import random
import psycopg2

class SolarInverter:
    def __init__(self, db_params):
        self.db_params = db_params
        self.status = "ONLINE"
        # Valores base realistas para um inversor residencial
        self.base_voltage = 230.0
        self.base_current = 15.0

    def read_metrics(self):
        """Simula a leitura física dos sensores do inversor."""
        # Pequenas flutuações simulando nuvens ou picos da rede
        voltage = self.base_voltage + random.uniform(-2.5, 2.5)
        current = self.base_current + random.uniform(-1.0, 3.0)
        power = voltage * current
        
        return {
            "voltage": round(voltage, 2),
            "current": round(current, 2),
            "power": round(power, 2),
            "status": self.status
        }

    def save_to_db(self, metrics):
        """Conecta ao banco e injeta os dados de forma isolada."""
        try:
            with psycopg2.connect(**self.db_params) as conn:
                with conn.cursor() as cursor:
                    query = """
                        INSERT INTO inverter_metrics (voltage_v, current_a, power_w, status)
                        VALUES (%s, %s, %s, %s)
                    """
                    cursor.execute(query, (
                        metrics["voltage"], 
                        metrics["current"], 
                        metrics["power"], 
                        metrics["status"]
                    ))
                conn.commit()
                print(f"📡 Dados enviados: {metrics['power']}W")
        except Exception as e:
            print(f"❌ Erro de conexão do sensor: {e}")

    def run(self, interval_seconds=5):
        """Loop principal do dispositivo de hardware."""
        print("🔌 Simulador do Inversor (Edge Device) iniciado...")
        while True:
            metrics = self.read_metrics()
            self.save_to_db(metrics)
            time.sleep(interval_seconds)

if __name__ == "__main__":
    db_config = {
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
        "host": "db",
        "port": "5432",
        "database": os.getenv("POSTGRES_DB")
    }
    
    # Instancia e roda o nosso "dispositivo físico"
    inverter = SolarInverter(db_config)
    # Aguarda o banco de dados subir antes de começar a transmitir
    time.sleep(10) 
    inverter.run()