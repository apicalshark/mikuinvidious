FROM python:3.12-slim
RUN apt-get update && apt-get install -y curl
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn python-dotenv
COPY . .
EXPOSE 8000
CMD [ "python", "main.py" ]
#CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker" , "--bind", "0.0.0.0:8000", "app:app"]