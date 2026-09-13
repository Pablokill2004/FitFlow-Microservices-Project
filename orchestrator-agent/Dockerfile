FROM python:3.11-slim

WORKDIR /app
COPY orchestrator-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY orchestrator-agent/app.py .
COPY orchestrator-agent/observability.py .
COPY orchestrator-agent/auth.py .
# No se copia fitflow-mcp/server.py: el orchestrator habla solo A2A y no
# tiene cliente MCP propio (decision de diseno 1).

EXPOSE 9003
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9003"]
