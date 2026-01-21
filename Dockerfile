FROM python:3.12-slim

WORKDIR /app

# schneller/sauberer: nur runtime deps
RUN pip install --no-cache-dir fastapi uvicorn requests
RUN pip install --no-cache-dir fastapi uvicorn requests ldap3
RUN pip install --no-cache-dir python-multipart

COPY app /app/app

EXPOSE 8088

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
