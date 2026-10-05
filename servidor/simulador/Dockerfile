FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir requests
COPY simulador.py .
CMD ["python", "simulador.py"]
