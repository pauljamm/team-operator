FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY operator.py .
RUN chmod +x operator.py

# Создаем директорию для возможного монтирования kubeconfig
RUN mkdir -p /root/.kube

# Устанавливаем переменную окружения для kubeconfig
ENV KUBECONFIG=/root/.kube/config

# Запускаем оператор
CMD ["python", "operator.py"]
