#!/usr/bin/env python3

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
import kubernetes
import yaml
import os
import io
import base64
import logging
import tempfile
from kubernetes.client.rest import ApiException

# Настройка логирования
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Определение группы, версии и типа ресурса
GROUP = 'team.example.com'
VERSION = 'v1'
PLURAL_TEAMS = 'teams'
PLURAL_USERS = 'users'

# Пространство имен для пользователей
USERS_NAMESPACE = 'users'

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-for-testing')
csrf = CSRFProtect(app)

# Загрузка конфигурации Kubernetes
def load_kubernetes_config():
    try:
        kubernetes.config.load_incluster_config()
        logger.info("Загружена конфигурация из кластера Kubernetes")
    except kubernetes.config.config_exception.ConfigException:
        try:
            kubernetes.config.load_kube_config()
            logger.info("Загружена конфигурация из kubeconfig")
        except kubernetes.config.config_exception.ConfigException:
            logger.error("Не удалось загрузить конфигурацию Kubernetes")
            raise

# Функция для создания пространства имен пользователей
def ensure_users_namespace():
    """Создает пространство имен для пользователей, если оно не существует"""
    api = kubernetes.client.CoreV1Api()
    
    try:
        # Проверяем, существует ли пространство имен
        api.read_namespace(name=USERS_NAMESPACE)
        logger.info(f"Пространство имен {USERS_NAMESPACE} уже существует")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:  # Не найдено - создаем
            # Создаем пространство имен
            namespace = {
                'apiVersion': 'v1',
                'kind': 'Namespace',
                'metadata': {
                    'name': USERS_NAMESPACE,
                    'labels': {
                        'managed-by': 'team-operator',
                        'purpose': 'user-accounts'
                    }
                }
            }
            
            try:
                api.create_namespace(body=namespace)
                logger.info(f"Пространство имен {USERS_NAMESPACE} создано")
            except kubernetes.client.exceptions.ApiException as e:
                logger.error(f"Ошибка при создании пространства имен {USERS_NAMESPACE}: {e}")
                raise
        else:
            logger.error(f"Ошибка при проверке пространства имен {USERS_NAMESPACE}: {e}")
            raise

# Получение списка команд
def get_teams():
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        teams = custom_api.list_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_TEAMS
        )
        return teams.get('items', [])
    except ApiException as e:
        logger.error(f"Ошибка при получении списка команд: {e}")
        return []

# Получение команды по имени
def get_team(name):
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        return custom_api.get_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_TEAMS,
            name=name
        )
    except ApiException as e:
        logger.error(f"Ошибка при получении команды {name}: {e}")
        return None

# Создание команды
def create_team(team_data):
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        return custom_api.create_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_TEAMS,
            body=team_data
        )
    except ApiException as e:
        logger.error(f"Ошибка при создании команды: {e}")
        raise

# Обновление команды
def update_team(name, team_data):
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        return custom_api.replace_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_TEAMS,
            name=name,
            body=team_data
        )
    except ApiException as e:
        logger.error(f"Ошибка при обновлении команды {name}: {e}")
        raise

# Удаление команды
def delete_team(name):
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        return custom_api.delete_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_TEAMS,
            name=name
        )
    except ApiException as e:
        logger.error(f"Ошибка при удалении команды {name}: {e}")
        raise

# Получение списка пользователей
def get_users():
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        users = custom_api.list_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_USERS
        )
        return users.get('items', [])
    except ApiException as e:
        logger.error(f"Ошибка при получении списка пользователей: {e}")
        return []

# Получение пользователя по имени
def get_user(name):
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        return custom_api.get_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_USERS,
            name=name
        )
    except ApiException as e:
        logger.error(f"Ошибка при получении пользователя {name}: {e}")
        return None

# Создание пользователя
def create_user(user_data):
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        return custom_api.create_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_USERS,
            body=user_data
        )
    except ApiException as e:
        logger.error(f"Ошибка при создании пользователя: {e}")
        raise

# Обновление пользователя
def update_user(name, user_data):
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        return custom_api.replace_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_USERS,
            name=name,
            body=user_data
        )
    except ApiException as e:
        logger.error(f"Ошибка при обновлении пользователя {name}: {e}")
        raise

# Удаление пользователя
def delete_user(name):
    custom_api = kubernetes.client.CustomObjectsApi()
    try:
        return custom_api.delete_cluster_custom_object(
            group=GROUP,
            version=VERSION,
            plural=PLURAL_USERS,
            name=name
        )
    except ApiException as e:
        logger.error(f"Ошибка при удалении пользователя {name}: {e}")
        raise

# Получение kubeconfig пользователя
def get_user_kubeconfig(name):
    api = kubernetes.client.CoreV1Api()
    
    try:
        config_map = api.read_namespaced_config_map(
            name=f"{name}-kubeconfig",
            namespace=USERS_NAMESPACE
        )
        
        if config_map.data and 'config' in config_map.data:
            return config_map.data['config']
        else:
            logger.warning(f"ConfigMap {name}-kubeconfig не содержит данных конфигурации")
            return None
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:  # Не найдено
            logger.warning(f"ConfigMap {name}-kubeconfig не существует в пространстве имен {USERS_NAMESPACE}")
        else:
            logger.error(f"Ошибка при получении ConfigMap {name}-kubeconfig: {e}")
        return None

# Вспомогательная функция для преобразования значений памяти в Mi
def convert_memory_to_mi(memory_str):
    memory_str = str(memory_str).upper()
    if 'KI' in memory_str:
        return float(memory_str.replace('KI', '')) / 1024
    elif 'MI' in memory_str:
        return float(memory_str.replace('MI', ''))
    elif 'GI' in memory_str:
        return float(memory_str.replace('GI', '')) * 1024
    elif 'TI' in memory_str:
        return float(memory_str.replace('TI', '')) * 1024 * 1024
    elif 'K' in memory_str:
        return float(memory_str.replace('K', '')) / 1024
    elif 'M' in memory_str:
        return float(memory_str.replace('M', ''))
    elif 'G' in memory_str:
        return float(memory_str.replace('G', '')) * 1024
    elif 'T' in memory_str:
        return float(memory_str.replace('T', '')) * 1024 * 1024
    else:
        return float(memory_str) / (1024 * 1024)  # Предполагаем, что это байты

# Маршруты Flask

@app.route('/')
def index():
    # Получаем статистику для главной страницы
    teams = get_teams()
    users = get_users()
    
    # Подсчитываем общее количество окружений
    total_environments = sum(len(team.get('spec', {}).get('environments', [])) for team in teams)
    
    # Подсчитываем количество пользователей по ролям и командам
    admin_count = sum(1 for user in users if user.get('spec', {}).get('role') == 'admin')
    developer_count = sum(1 for user in users if user.get('spec', {}).get('role') == 'developer')
    viewer_count = sum(1 for user in users if user.get('spec', {}).get('role') == 'viewer')
    
    stats = {
        'teams_count': len(teams),
        'users_count': len(users),
        'environments_count': total_environments,
        'admin_count': admin_count,
        'developer_count': developer_count,
        'viewer_count': viewer_count
    }
    
    return render_template('index.html', stats=stats)

# Маршруты для команд
@app.route('/teams')
def list_teams():
    teams = get_teams()
    return render_template('teams/list.html', teams=teams)

@app.route('/teams/new', methods=['GET', 'POST'])
def new_team():
    if request.method == 'POST':
        try:
            team_data = {
                'apiVersion': f"{GROUP}/{VERSION}",
                'kind': 'Team',
                'metadata': {
                    'name': request.form['name']
                },
                'spec': {
                    'description': request.form['description'],
                    'environments': []
                }
            }
            
            # Добавляем окружения
            env_count = int(request.form.get('env_count', 0))
            for i in range(env_count):
                env_name = request.form.get(f'env_name_{i}')
                env_desc = request.form.get(f'env_description_{i}')
                
                if env_name:
                    environment = {
                        'name': env_name,
                        'description': env_desc,
                        'labels': {},
                        'quota': {
                            'cpu': request.form.get(f'env_cpu_{i}', '10'),
                            'memory': request.form.get(f'env_memory_{i}', '20Gi'),
                            'cpu_limit': request.form.get(f'env_cpu_limit_{i}', '20'),
                            'memory_limit': request.form.get(f'env_memory_limit_{i}', '40Gi'),
                            'pods': request.form.get(f'env_pods_{i}', '20'),
                            'services': request.form.get(f'env_services_{i}', '10')
                        }
                    }
                    team_data['spec']['environments'].append(environment)
            
            create_team(team_data)
            flash('Команда успешно создана', 'success')
            return redirect(url_for('list_teams'))
        except Exception as e:
            flash(f'Ошибка при создании команды: {str(e)}', 'danger')
    
    return render_template('teams/new.html')

@app.route('/teams/<name>')
def show_team(name):
    team = get_team(name)
    if not team:
        flash(f'Команда {name} не найдена', 'danger')
        return redirect(url_for('list_teams'))
    
    return render_template('teams/show.html', team=team)

@app.route('/teams/<name>/edit', methods=['GET', 'POST'])
def edit_team(name):
    team = get_team(name)
    if not team:
        flash(f'Команда {name} не найдена', 'danger')
        return redirect(url_for('list_teams'))
    
    if request.method == 'POST':
        try:
            # Обновляем основные данные
            team['spec']['description'] = request.form['description']
            
            # Обновляем окружения
            team['spec']['environments'] = []
            env_count = int(request.form.get('env_count', 0))
            for i in range(env_count):
                env_name = request.form.get(f'env_name_{i}')
                env_desc = request.form.get(f'env_description_{i}')
                
                if env_name:
                    environment = {
                        'name': env_name,
                        'description': env_desc,
                        'labels': {},
                        'quota': {
                            'cpu': request.form.get(f'env_cpu_{i}', '10'),
                            'memory': request.form.get(f'env_memory_{i}', '20Gi'),
                            'cpu_limit': request.form.get(f'env_cpu_limit_{i}', '20'),
                            'memory_limit': request.form.get(f'env_memory_limit_{i}', '40Gi'),
                            'pods': request.form.get(f'env_pods_{i}', '20'),
                            'services': request.form.get(f'env_services_{i}', '10')
                        }
                    }
                    team['spec']['environments'].append(environment)
            
            
            update_team(name, team)
            flash('Команда успешно обновлена', 'success')
            return redirect(url_for('show_team', name=name))
        except Exception as e:
            flash(f'Ошибка при обновлении команды: {str(e)}', 'danger')
    
    return render_template('teams/edit.html', team=team)

@app.route('/teams/<name>/delete', methods=['POST'])
def delete_team_route(name):
    try:
        delete_team(name)
        flash(f'Команда {name} успешно удалена', 'success')
    except Exception as e:
        flash(f'Ошибка при удалении команды: {str(e)}', 'danger')
    
    return redirect(url_for('list_teams'))

# Маршруты для пользователей
@app.route('/users')
def list_users():
    users = get_users()
    return render_template('users/list.html', users=users)

@app.route('/users/new', methods=['GET', 'POST'])
def new_user():
    if request.method == 'POST':
        try:
            user_data = {
                'apiVersion': f"{GROUP}/{VERSION}",
                'kind': 'User',
                'metadata': {
                    'name': request.form['name']
                },
                'spec': {
                    'fullName': request.form['fullName'],
                    'email': request.form['email'],
                    'role': request.form['role']
                }
            }
            
            # Добавляем команды
            teams = request.form.getlist('teams')
            if teams:
                user_data['spec']['teams'] = teams
            
            create_user(user_data)
            flash('Пользователь успешно создан', 'success')
            return redirect(url_for('list_users'))
        except Exception as e:
            flash(f'Ошибка при создании пользователя: {str(e)}', 'danger')
    
    teams = get_teams()
    return render_template('users/new.html', teams=teams)

@app.route('/users/<name>')
def show_user(name):
    user = get_user(name)
    if not user:
        flash(f'Пользователь {name} не найден', 'danger')
        return redirect(url_for('list_users'))
    
    return render_template('users/show.html', user=user)

@app.route('/users/<name>/edit', methods=['GET', 'POST'])
def edit_user(name):
    user = get_user(name)
    if not user:
        flash(f'Пользователь {name} не найден', 'danger')
        return redirect(url_for('list_users'))
    
    if request.method == 'POST':
        try:
            # Обновляем основные данные
            user['spec']['fullName'] = request.form['fullName']
            user['spec']['email'] = request.form['email']
            user['spec']['role'] = request.form['role']
            
            # Обновляем команды
            teams = request.form.getlist('teams')
            if teams:
                user['spec']['teams'] = teams
            else:
                user['spec']['teams'] = []
            
            update_user(name, user)
            flash('Пользователь успешно обновлен', 'success')
            return redirect(url_for('show_user', name=name))
        except Exception as e:
            flash(f'Ошибка при обновлении пользователя: {str(e)}', 'danger')
    
    teams = get_teams()
    return render_template('users/edit.html', user=user, teams=teams)

@app.route('/users/<name>/delete', methods=['POST'])
def delete_user_route(name):
    try:
        delete_user(name)
        flash(f'Пользователь {name} успешно удален', 'success')
    except Exception as e:
        flash(f'Ошибка при удалении пользователя: {str(e)}', 'danger')
    
    return redirect(url_for('list_users'))

@app.route('/users/<name>/kubeconfig')
def download_kubeconfig(name):
    kubeconfig = get_user_kubeconfig(name)
    if not kubeconfig:
        flash(f'Не удалось получить kubeconfig для пользователя {name}', 'danger')
        return redirect(url_for('show_user', name=name))
    
    # Создаем временный файл с kubeconfig
    with tempfile.NamedTemporaryFile(delete=False, suffix='.yaml') as temp:
        temp.write(kubeconfig.encode())
        temp_path = temp.name
    
    return send_file(
        temp_path,
        as_attachment=True,
        download_name=f"{name}-kubeconfig.yaml",
        mimetype='application/x-yaml'
    )

@app.route('/api/namespaces/<namespace>/quota')
def get_namespace_quota(namespace):
    api = kubernetes.client.CoreV1Api()
    try:
        quotas = api.list_namespaced_resource_quota(namespace=namespace)
        if quotas.items:
            # Берем первую ResourceQuota в namespace
            quota = quotas.items[0]
            
            # Получаем спецификацию (лимиты) и статус (использование)
            hard = quota.spec.hard
            used = quota.status.used if quota.status else {}
            
            # Формируем данные для отображения
            quota_data = {
                'name': quota.metadata.name,
                'resources': []
            }
            
            # Добавляем данные по каждому ресурсу
            for resource in hard:
                if resource in used:
                    # Преобразуем значения в числа для расчета процента
                    hard_value = hard[resource]
                    used_value = used[resource]
                    
                    # Пытаемся преобразовать значения в числа для расчета процента
                    try:
                        # Для CPU (может быть в формате "100m")
                        if resource in ['requests.cpu', 'limits.cpu']:
                            hard_num = float(hard_value.replace('m', '')) / 1000 if 'm' in hard_value else float(hard_value)
                            used_num = float(used_value.replace('m', '')) / 1000 if 'm' in used_value else float(used_value)
                        # Для памяти (может быть в формате "100Mi" или "1Gi")
                        elif resource in ['requests.memory', 'limits.memory']:
                            # Преобразуем все в Mi для единообразия
                            hard_num = convert_memory_to_mi(hard_value)
                            used_num = convert_memory_to_mi(used_value)
                        # Для остальных ресурсов (pods, services и т.д.)
                        else:
                            hard_num = int(hard_value)
                            used_num = int(used_value)
                        
                        # Рассчитываем процент использования
                        percentage = (used_num / hard_num * 100) if hard_num > 0 else 0
                        
                        # Добавляем данные в список
                        quota_data['resources'].append({
                            'name': resource,
                            'hard': hard_value,
                            'used': used_value,
                            'percentage': min(percentage, 100)  # Ограничиваем максимум 100%
                        })
                    except (ValueError, TypeError) as e:
                        # Если не удалось преобразовать в числа, добавляем без процента
                        quota_data['resources'].append({
                            'name': resource,
                            'hard': hard_value,
                            'used': used_value,
                            'percentage': None
                        })
            
            return jsonify(quota_data)
        return jsonify({'error': 'ResourceQuota не найдена'})
    except ApiException as e:
        return jsonify({'error': f'Ошибка при получении ResourceQuota: {e}'}), 500

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash('Ошибка CSRF-токена. Пожалуйста, попробуйте еще раз.', 'danger')
    return redirect(url_for('index'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

if __name__ == '__main__':
    # Загружаем конфигурацию Kubernetes
    load_kubernetes_config()
    
    # Убеждаемся, что пространство имен пользователей существует
    ensure_users_namespace()
    
    # Запускаем приложение
    app.run(host='0.0.0.0', port=8080, debug=True)
