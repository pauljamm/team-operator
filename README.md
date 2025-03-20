# Kubernetes оператор для управления командами и пользователями

## Локальный запуск

1. Сконфигурируйте доступ к Kubernetes кластеру. Оператор и UI берут доступ к кубу из стандартного kubeconfig.
2. Примените CRD
```bash
$ kubectl apply -f crd.yaml
```
3. Установите зависимости
```bash
$ pip install -r requirements.txt
```
4. Запустите оператор
```bash
python operator.py
```
5. Запустите UI
```bash
python app.py
```
