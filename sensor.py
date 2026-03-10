import os
import time
import random
import logging
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2 import OperationalError
from typing import Dict, Any, List

# Configurar logging estruturado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ConfigError(Exception):
    """Exceção para erros de configuração."""
    pass

class SolarInverter:
    # Constantes de operação
    DB_TIMEOUT = 5  # segundos
    BATCH_SIZE = 10  # Enviar múltiplas leituras de uma vez
    MAX_RETRIES = 3
    
    def __init__(self, db_params: Dict[str, Any]):
        """Inicializa o sensor com pool de conexões."""
        self.db_params = self._validate_config(db_params)
        
        # Pool de conexões para o sensor (menor pool que o monitor)
        self.db_pool = pg_pool.SimpleConnectionPool(
            1, 2,  # min/max connections
            **self.db_params,
            connect_timeout=self.DB_TIMEOUT
        )
        
        self.status = "ONLINE"
        
        # Valores base realistas para um inversor residencial
        self.base_voltage = 230.0
        self.base_current = 15.0
        
        # Cache para reduzir cálculos
        self.metrics_buffer: List[Dict[str, Any]] = []
        
        logger.info("SolarInverter inicializado com sucesso")
    
    def _validate_config(self, db_params: Dict[str, Any]) -> Dict[str, Any]:
        """Valida configuração de BD."""
        if not all(db_params.values()):
            raise ConfigError("Parâmetros de BD incompletos")
        return db_params

    def read_metrics(self) -> Dict[str, Any]:
        """Simula a leitura física dos sensores do inversor com validação."""
        # Pequenas flutuações simulando nuvens ou picos da rede
        voltage = self.base_voltage + random.uniform(-2.5, 2.5)
        current = self.base_current + random.uniform(-1.0, 3.0)
        power = voltage * current
        
        # Validações de segurança (inversor não pode ter valores negativos ou inválidos)
        voltage = max(0, min(voltage, 300))  # Clamp entre 0 e 300V
        current = max(0, current)  # Corrente não negativa
        power = max(0, power)  # Potência não negativa
        
        return {
            "voltage": round(voltage, 2),
            "current": round(current, 2),
            "power": round(power, 2),
            "status": self.status
        }

    def save_to_db(self, metrics: Dict[str, Any]) -> bool:
        """Tenta salvar métrica com retry logic."""
        self.metrics_buffer.append(metrics)
        
        # Fazer batch insert quando buffer atinge o limite (otimização)
        if len(self.metrics_buffer) >= self.BATCH_SIZE:
            return self._flush_buffer()
        
        return True
    
    def _flush_buffer(self) -> bool:
        """Faz insert em batch de todas as métricas no buffer."""
        if not self.metrics_buffer:
            return True
        
        conn = None
        for attempt in range(self.MAX_RETRIES):
            try:
                conn = self.db_pool.getconn()
                with conn.cursor() as cursor:
                    # Query com %s placeholders (parametrizada contra SQL injection)
                    query = """
                        INSERT INTO inverter_metrics 
                        (voltage_v, current_a, power_w, status)
                        VALUES (%s, %s, %s, %s)
                    """
                    
                    # Executar múltiplos inserts de uma vez
                    cursor.executemany(query, [
                        (m["voltage"], m["current"], m["power"], m["status"])
                        for m in self.metrics_buffer
                    ])
                
                conn.commit()
                total_power = sum(m["power"] for m in self.metrics_buffer)
                logger.info(f"📡 {len(self.metrics_buffer)} dados enviados (Total: {total_power:.2f}W)")
                self.metrics_buffer.clear()
                return True
                
            except OperationalError as e:
                logger.warning(f"Erro de conexão ao salvar (tentativa {attempt + 1}/{self.MAX_RETRIES}): {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
            except Exception as e:
                logger.error(f"Erro ao salvar métricas: {e}")
                return False
            finally:
                if conn:
                    self.db_pool.putconn(conn)
        
        logger.error("Falha ao salvar métricas após todas as tentativas")
        return False

    def __del__(self):
        """Cleanup: fecha buffer pendente e conexões do pool."""
        if hasattr(self, 'metrics_buffer') and self.metrics_buffer:
            logger.warning("Flushing buffer não commitado ao destruir")
            self._flush_buffer()
        
        if hasattr(self, 'db_pool'):
            self.db_pool.closeall()
            logger.info("Pool de conexões do sensor fechado")
    
    def run(self, interval_seconds: int = 5) -> None:
        """Loop principal do dispositivo otimizado."""
        logger.info("🔌 Simulador do Inversor (Edge Device) iniciado...")
        
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        try:
            while True:
                try:
                    metrics = self.read_metrics()
                    self.save_to_db(metrics)
                    consecutive_errors = 0  # Reset
                    time.sleep(interval_seconds)
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Erro no loop do sensor: {e} ({consecutive_errors}/{max_consecutive_errors})")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        logger.critical("Limite de erros consecutivos atingido. Abortando sensor.")
                        raise
                    
                    time.sleep(1)  # Aguarda antes de tentar novamente
                    
        except KeyboardInterrupt:
            logger.info("Sensor interrompido pelo utilizador (KeyboardInterrupt)")
        except Exception as e:
            logger.critical(f"Erro fatal no sensor: {e}")
            raise
        finally:
            # Flush final de dados pendentes
            if self.metrics_buffer:
                logger.info(f"Enviando {len(self.metrics_buffer)} métricas pendentes...")
                self._flush_buffer()
            logger.info("Sensor encerrado")

if __name__ == "__main__":
    # Validar variáveis de ambiente obrigatórias
    required_vars = ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        logger.error(f"Variáveis de ambiente ausentes: {', '.join(missing)}")
        exit(1)
    
    db_config = {
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
        "host": os.getenv("DB_HOST", "db"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "database": os.getenv("POSTGRES_DB")
    }
    
    try:
        # Instancia sensor
        inverter = SolarInverter(db_config)
        
        # Aguarda BD com retry inteligente (em vez de sleep fixo)
        max_wait = 30
        wait_time = 0
        while wait_time < max_wait:
            try:
                conn = inverter.db_pool.getconn()
                conn.close()
                inverter.db_pool.putconn(conn)
                logger.info("BD está disponível. Iniciando leitura...")
                break
            except:
                wait_time += 1
                if wait_time < max_wait:
                    logger.info(f"Aguardando BD ({wait_time}/{max_wait}s)...")
                    time.sleep(1)
        
        inverter.run()
        
    except ConfigError as e:
        logger.critical(f"Erro de configuração: {e}")
        exit(1)
    except Exception as e:
        logger.critical(f"Erro fatal ao iniciar sensor: {e}")
        exit(1)