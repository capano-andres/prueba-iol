# === 1. BUILD DEL FRONTEND ===
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

# Copiamos package.json primero para aprovechar el caché
COPY frontend/package*.json ./
RUN npm install

# Copiamos el resto del frontend y compilamos
COPY frontend/ ./
RUN npm run build

# === 2. BUILD DEL BACKEND ===
FROM python:3.11-slim
WORKDIR /app

# Instalar dependencias del sistema requeridas para paquetes de Python (si aplica)
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos todo el backend
COPY . .

# Copiamos el resultado de la compilación de Node (dist) a la carpeta del backend
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Exponemos el puerto
EXPOSE 8000

# Comando de arranque explícito para tu servidor FastAPI
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}
