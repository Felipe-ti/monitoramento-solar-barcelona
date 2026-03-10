import os
import time
import logging
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError
from psycopg2 import pool as pg_pool
from functools import lru_cache
from typing import Optional, Dict, Any

# Configurar logging estruturado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ConfigError(Exception):
    """Exceção para erros de configuração."""
    pass

class SolarMonitor:
    # Constantes imutáveis
    MIN_VOLTAGE = 228.0
    MAX_VOLTAGE = 232.0
    COOLDOWN_SECONDS = 60
    DB_TIMEOUT = 5  # segundos
    REQUEST_TIMEOUT = 10  # segundos
    MAX_RETRIES = 3
    
    def __init__(self):
        """Inicializa o monitor com validação de config e pool de conexões."""
        self._validate_config()
        
        # Pool de conexões (evita criar nova conexão a cada query)
        self.db_pool = pg_pool.SimpleConnectionPool(
            1, 5,  # min/max connections
            **self.db_params,
            connect_timeout=self.DB_TIMEOUT
        )
        
        # Gestão de Estado
        self.alert_active = False
        self.last_alert_time = 0
        logger.info("SolarMonitor inicializado com sucesso")
    
    def _validate_config(self) -> None:
        """Valida todas as variáveis de ambiente necessárias."""
        required_vars = {
            "POSTGRES_USER": "Utilizador PostgreSQL",
            "POSTGRES_PASSWORD": "Palavra-passe PostgreSQL",
            "POSTGRES_DB": "Nome da BD PostgreSQL",
            "TELEGRAM_TOKEN": "Token Telegram",
            "TELEGRAM_CHAT_ID": "Chat ID Telegram"
        }
        
        missing = []
        for var, desc in required_vars.items():
            if not os.getenv(var):
                missing.append(f"{var} ({desc})")
        
        if missing:
            raise ConfigError(f"Variáveis de ambiente ausentes: {', '.join(missing)}")
        
        self.db_params = {
            "user": os.getenv("POSTGRES_USER"),
            "password": os.getenv("POSTGRES_PASSWORD"),
            "host": os.getenv("DB_HOST", "db"),  # Permitir override
            "port": int(os.getenv("DB_PORT", "5432")),
            "database": os.getenv("POSTGRES_DB")
        }
        
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    def send_telegram_msg(self, text: str) -> bool:
        """Envia mensagem Telegram com retry e tratamento de erros específicos."""
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        
        for attempt in range(self.MAX_RETRIES):
            try:
                response = requests.post(
                    url, 
                    json=payload, 
                    timeout=self.REQUEST_TIMEOUT
                )
                response.raise_for_status()
                logger.info(f"Mensagem Telegram enviada com sucesso")
                return True
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout ao enviar Telegram (tentativa {attempt + 1}/{self.MAX_RETRIES})")
            except requests.exceptions.ConnectionError:
                logger.warning(f"Erro de conexão ao enviar Telegram (tentativa {attempt + 1}/{self.MAX_RETRIES})")
            except requests.exceptions.HTTPError as e:
                if response.status_code == 401:
                    logger.error("Token Telegram inválido ou expirado")
                    return False
                else:
                    logger.error(f"Erro HTTP {response.status_code}: {e}")
                    return False
            except Exception as e:
                logger.error(f"Erro inesperado ao comunicar com Telegram: {e}")
                return False
            
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
        
        logger.error("Falha ao enviar Telegram após todas as tentativas")
        return False

    def init_db(self) -> None:
        """Garante que a estrutura existe com índices para performance."""
        create_table_query = """
        CREATE TABLE IF NOT EXISTS inverter_metrics (
            id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            voltage_v NUMERIC(6, 2) NOT NULL,
            current_a NUMERIC(6, 2) NOT NULL,
            power_w NUMERIC(8, 2) NOT NULL,
            status VARCHAR(50) NOT NULL
        );
        
        -- Índices para otimizar queries (crucial!)
        CREATE INDEX IF NOT EXISTS idx_timestamp_desc 
            ON inverter_metrics(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_status 
            ON inverter_metrics(status);
        
        -- Partição por data para tabelas muito grandes (opcional, futuro)
        """
        
        retry_count = 0
        max_retries = 5
        
        while retry_count < max_retries:
            conn = None
            try:
                conn = self.db_pool.getconn()
                with conn.cursor() as cursor:
                    cursor.execute(create_table_query)
                conn.commit()
                logger.info("Estrutura de BD inicializada com sucesso")
                return
            except OperationalError as e:
                retry_count += 1
                if retry_count < max_retries:
                    logger.warning(f"BD não disponível. Tentativa {retry_count}/{max_retries}...")
                    time.sleep(2 ** retry_count)  # Exponential backoff
                else:
                    logger.error(f"Falha ao conectar à BD após {max_retries} tentativas: {e}")
                    raise
            except Exception as e:
                logger.error(f"Erro ao inicializar BD: {e}")
                raise
            finally:
                if conn:
                    self.db_pool.putconn(conn)

    def fetch_latest_metric(self) -> Optional[Dict[str, Any]]:
        """Lê o último registo com conexão do pool (sem criar nova cada vez)."""
        query = "SELECT * FROM inverter_metrics ORDER BY timestamp DESC LIMIT 1;"
        conn = None
        try:
            conn = self.db_pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query)
                return cursor.fetchone()
        except psycopg2.DatabaseError as e:
            logger.error(f"Erro ao ler dados da BD: {e}")
            return None
        except Exception as e:
            logger.error(f"Erro inesperado ao buscar métrica: {e}")
            return None
        finally:
            if conn:
                self.db_pool.putconn(conn)

    def analyze_data(self, data: Optional[Dict[str, Any]]) -> None:
        """Analisa dados com algoritmo otimizado de detecção de anomalias."""
        if not data:
            return
        
        try:
            voltage = float(data['voltage_v'])
            power = float(data['power_w'])
            current_time = time.time()
            
            # Validação de limites (evita valores absurdos)
            if voltage < 0 or power < 0:
                logger.warning(f"Valores inválidos detectados: V={voltage}, P={power}")
                return
            
            is_anomalous = voltage < self.MIN_VOLTAGE or voltage > self.MAX_VOLTAGE
            
            if is_anomalous:
                # Cooldown inteligente: evita spam mesmo se múltiplas anomalias
                time_since_last_alert = current_time - self.last_alert_time
                
                if not self.alert_active or time_since_last_alert > self.COOLDOWN_SECONDS:
                    msg = (
                        f"⚠️ *ALERTA CRÍTICO: Inversor Solar*\n\n"
                        f"Anomalia detetada na rede de Barcelona.\n"
                        f"⚡ *Tensão Atual:* {voltage:.2f}V "
                        f"(Limites: {self.MIN_VOLTAGE}-{self.MAX_VOLTAGE}V)\n"
                        f"🔋 *Potência:* {power:.2f}W\n"
                        f"🕒 *Registo:* {data['timestamp'].strftime('%H:%M:%S UTC')}"
                    )
                    
                    if self.send_telegram_msg(msg):
                        self.alert_active = True
                        self.last_alert_time = current_time
                        logger.warning(f"Alerta disparado! Tensão: {voltage:.2f}V")
            else:
                # Recuperação: sistema voltou ao normal
                if self.alert_active:
                    msg = (
                        f"✅ *SISTEMA RECUPERADO*\n\n"
                        f"A tensão estabilizou em {voltage:.2f}V. "
                        f"Monitorização normalizada."
                    )
                    if self.send_telegram_msg(msg):
                        self.alert_active = False
                        logger.info("Sistema normalizado. Alerta desativado.")
        except (ValueError, KeyError, AttributeError) as e:
            logger.error(f"Erro ao processar dados: {e}")

    def __del__(self):
        """Cleanup: fecha conexões do pool ao destruir o objeto."""
        if hasattr(self, 'db_pool'):
            self.db_pool.closeall()
            logger.info("Pool de conexões fechado")
    
    def run(self) -> None:
        """Loop principal otimizado com tratamento robusto de erros."""
        logger.info("🚀 Monitorização Analítica Iniciada...")
        
        try:
            self.init_db()
            self.send_telegram_msg(
                "🛡️ *Sistema de Monitorização Fotovoltaica:* Online e a analisar métricas."
            )
            
            consecutive_errors = 0
            max_consecutive_errors = 5
            
            while True:
                try:
                    latest_data = self.fetch_latest_metric()
                    self.analyze_data(latest_data)
                    consecutive_errors = 0  # Reset error counter on success
                    
                    # Verifica a base de dados a cada 10 segundos
                    time.sleep(10)
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Erro no loop de monitorização: {e} ({consecutive_errors}/{max_consecutive_errors})")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        logger.critical("Limite de erros consecutivos atingido. Abortando.")
                        self.send_telegram_msg(
                            "🚨 *ERRO CRÍTICO:* Sistema de monitorização falhou múltiplas vezes. Verificar servidor."
                        )
                        raise
                    
                    time.sleep(2)  # Aguarda antes de tentar novamente
                    
        except KeyboardInterrupt:
            logger.info("A encerrar o serviço de monitorização (KeyboardInterrupt)...")
        except ConfigError as e:
            logger.critical(f"Erro de configuração: {e}")
            raise
        except Exception as e:
            logger.critical(f"Erro fatal no monitorização: {e}")
            raise
        finally:
            logger.info("Limpeza de recursos da aplicação...")

if __name__ == "__main__":
    try:
        monitor = SolarMonitor()
        monitor.run()
    except ConfigError as e:
        logger.critical(f"Configuração inválida: {e}")
        exit(1)
    except Exception as e:
        logger.critical(f"Erro fatal: {e}")
        exit(1)