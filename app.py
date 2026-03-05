import os
import time
import requests
import psycopg2
from psycopg2 import OperationalError

# Configurações via Variáveis de Ambiente (.env)
DB_USER = os.getenv("POSTGRES_USER")
DB_PASS = os.getenv("POSTGRES_PASSWORD")
DB_NAME = os.getenv("POSTGRES_DB")
DB_HOST = "db"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Erro ao enviar Telegram: {e}")

def connect_db():
    # Retry logic: Aguarda o banco de dados estar 100% pronto
    for i in range(5):
        try:
            connection = psycopg2.connect(
                user=DB_USER,
                password=DB_PASS,
                host=DB_HOST,
                port="5432",
                database=DB_NAME
            )
            print("✅ Conexão com o PostgreSQL bem-sucedida!")
            return connection
        except OperationalError as e:
            print(f"⏳ Aguardando banco de dados... ({i+1}/5)")
            time.sleep(5)
    return None

def init_db(conn):
    """Cria a tabela de métricas caso ela não exista, seguindo padrões modernos."""
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
        # O 'with' garante que o cursor será fechado automaticamente (Best Practice)
        with conn.cursor() as cursor:
            cursor.execute(create_table_query)
        conn.commit()
        print("✅ Estrutura do banco de dados verificada/criada com sucesso.")
    except Exception as e:
        print(f"❌ Erro ao inicializar o banco: {e}")
        conn.rollback()

def main():
    print("🚀 Iniciando Monitoramento Fotovoltaico...")
    
    conn = connect_db()
    
    if conn:
        # Prepara a estrutura do banco
        init_db(conn)
        
        send_telegram_msg("☀️ Sistema Fotovoltaico: Monitoramento Online. Banco de dados inicializado.")
        
        # Loop principal (futuramente fará a leitura real do inversor)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("Encerrando o serviço...")
        finally:
            conn.close()
            print("Conexão com o banco encerrada.")
    else:
        send_telegram_msg("⚠️ ALERTA CRÍTICO: Falha de conexão com o banco de dados.")

if __name__ == "__main__":
    main()