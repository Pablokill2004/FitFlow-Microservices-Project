FROM python:3.11-slim

WORKDIR /app
COPY booking-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY booking-agent/app.py .
COPY fitflow-mcp/server.py /opt/fitflow-mcp/server.py

EXPOSE 9001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9001"]