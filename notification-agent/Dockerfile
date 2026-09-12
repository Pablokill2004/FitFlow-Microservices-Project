FROM python:3.11-slim

WORKDIR /app
COPY notification-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY notification-agent/app.py .
COPY fitflow-mcp/server.py /opt/fitflow-mcp/server.py

EXPOSE 9002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9002"]
