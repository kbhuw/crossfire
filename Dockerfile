FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip3 install --break-system-packages fastapi 'uvicorn[standard]' httpx python-dotenv \
 && cd auth && npm ci --omit=dev
ENV PORT=8080
EXPOSE 8080
CMD python3 -m uvicorn main:app --host 0.0.0.0 --port $PORT
