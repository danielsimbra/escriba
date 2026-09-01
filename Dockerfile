FROM python:3.12-slim

LABEL org.opencontainers.image.title="Escriba" \
      org.opencontainers.image.description="Transcricao limpa de audios do WhatsApp" \
      org.opencontainers.image.source="https://github.com/danielsimbra/escriba"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    TZ=America/Sao_Paulo

# tzdata: os horarios do log seguem o fuso configurado, nao UTC.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

# Usuario sem privilegios; os volumes montados pertencem a ele.
RUN useradd --create-home --shell /bin/bash escriba \
    && mkdir -p /app/data /app/logs \
    && chown -R escriba:escriba /app
USER escriba

EXPOSE 8000

# O healthcheck bate no proprio endpoint /saude: se o servidor parou de
# responder, o Docker reinicia o container sem esperar voce perceber.
HEALTHCHECK --interval=1m --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/saude', timeout=5).status == 200 else 1)" \
    || exit 1

ENTRYPOINT ["python", "-m", "escriba"]
CMD ["servir"]
