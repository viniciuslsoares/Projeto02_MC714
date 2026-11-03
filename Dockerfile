FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Os nós usam apenas a biblioteca padrão; requirements.txt traz as ferramentas de teste/análise.
COPY src/ ./src/

EXPOSE 8000
HEALTHCHECK --interval=2s --timeout=1s --retries=10 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=1)"

CMD ["python", "-m", "src.node"]

