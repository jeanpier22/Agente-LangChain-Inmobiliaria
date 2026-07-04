# ============================================
# Dockerfile - AlphaBot (Canal Chatwoot)
# Despliegue del webhook FastAPI: main_chatwoot.py
# ============================================

# Imagen base
FROM python:3.11-slim

# Buenas prácticas de Python en contenedores
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias primero (aprovecha la cache de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del proyecto
COPY . /app

# El servidor FastAPI escucha en el puerto 8000 (ver §5.5 de CLAUDE.md)
EXPOSE 8000

# Arrancar el webhook de Chatwoot
CMD ["uvicorn", "main_chatwoot:app", "--host", "0.0.0.0", "--port", "8000"]
