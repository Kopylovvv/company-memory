FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv==0.8.22
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
# The MAX API certificate chains to the Russian Trusted Root CA, which neither the
# system store nor certifi trust. One bundle = certifi + the two public CA certs.
COPY certs ./certs
# `echo` between files: a PEM without a trailing newline would otherwise be glued to the
# next block and silently break the bundle. The last step makes that fail the build
# instead of crash-looping the bot at startup.
RUN { cat "$(.venv/bin/python -c 'import certifi; print(certifi.where())')"; echo; \
      cat certs/russian_trusted_root_ca.pem; echo; \
      cat certs/russian_trusted_sub_ca.pem; echo; } > certs/max-ca-bundle.pem \
    && .venv/bin/python -c "import ssl; ssl.create_default_context(cafile='certs/max-ca-bundle.pem')"
ENV MAX_API_CA_BUNDLE=/app/certs/max-ca-bundle.pem
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD .venv/bin/alembic upgrade head && exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
