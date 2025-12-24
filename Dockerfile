FROM python:3.12-slim
RUN apt-get update && apt-get install -y curl
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn python-dotenv
COPY . .
# We don't EXPOSE here because the network is handled by the wgcf container
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]