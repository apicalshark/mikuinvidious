FROM python:3.14.2-alpine

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run the app from the python directory
CMD ["python", "python/main.py"]