FROM python:3.12-slim

WORKDIR /app

# Install package + deps from pyproject.toml
COPY pyproject.toml /app/pyproject.toml
# optional: README, falls du eins hast (sonst weglassen)
COPY README.md /app/README.md

COPY src /app/src
COPY app /app/app

RUN pip install --no-cache-dir .

EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
