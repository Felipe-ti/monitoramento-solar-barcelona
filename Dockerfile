# Padrão da indústria: versão mais recente e estável (3.13) usando base slim para compatibilidade de wheels
FROM python:3.13-slim

# Evita que o Python grave arquivos .pyc e força o log a aparecer imediatamente no terminal
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Define a pasta de trabalho dentro do contêiner
WORKDIR /app

# Atualiza pacotes do sistema e limpa o cache para manter a imagem leve
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia os requerimentos e instala
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia o código do projeto
COPY . .

# Comando para rodar o script
CMD ["python", "app.py"]