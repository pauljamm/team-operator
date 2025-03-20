#!/usr/bin/env python3

import kopf
import kubernetes
import yaml
import logging
import argparse
import os
import sys
import base64
import json
import datetime

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

# Функция для создания пространства имен пользователей
def ensure_users_namespace(logger):
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
                raise kopf.PermanentError(f"Не удалось создать пространство имен {USERS_NAMESPACE}: {e}")
        else:
            logger.error(f"Ошибка при проверке пространства имен {USERS_NAMESPACE}: {e}")
            raise kopf.PermanentError(f"Не удалось проверить пространство имен {USERS_NAMESPACE}: {e}")

@kopf.on.create(group=GROUP, version=VERSION, plural=PLURAL_TEAMS)
def create_fn(body, spec, name, logger, **kwargs):
    """Обработчик создания ресурса Team"""
    logger.info(f"Создание ресурса Team {name}")
    
    # Получаем данные из спецификации
    environments = spec.get('environments', [])
    
    if not environments:
        logger.warning(f"Для команды {name} не указаны окружения")
        kopf.warn(body, reason='NoEnvironments', message=f'Для команды {name} не указаны окружения')
        return {'environments_created': 0}
    
    # Создаем API-клиент Kubernetes
    api = kubernetes.client.CoreV1Api()
    rbac_api = kubernetes.client.RbacAuthorizationV1Api()
    
    created_namespaces = []
    
    # Создаем namespace для каждого окружения
    for env in environments:
        env_name = env.get('name')
        env_description = env.get('description', '')
        env_labels = env.get('labels', {})
        
        if not env_name:
            logger.warning(f"Пропускаем окружение без имени для команды {name}")
            continue
        
        # Формируем имя namespace на основе имени команды и окружения
        namespace_name = f"{name}-{env_name}".lower()
        
        # Создаем namespace
        namespace = {
            'apiVersion': 'v1',
            'kind': 'Namespace',
            'metadata': {
                'name': namespace_name,
                'labels': {
                    'team': name,
                    'environment': env_name,
                    'managed-by': 'team-operator',
                    **env_labels
                },
                'annotations': {
                    'description': env_description
                }
            }
        }
        
        # Создаем ResourceQuota для namespace
        resource_quota = {
            'apiVersion': 'v1',
            'kind': 'ResourceQuota',
            'metadata': {
                'name': f"{namespace_name}-quota",
                'namespace': namespace_name
            },
            'spec': {
                'hard': {
                    'requests.cpu': env.get('quota', {}).get('cpu', '10'),
                    'requests.memory': env.get('quota', {}).get('memory', '20Gi'),
                    'limits.cpu': env.get('quota', {}).get('cpu_limit', '20'),
                    'limits.memory': env.get('quota', {}).get('memory_limit', '40Gi'),
                    'pods': env.get('quota', {}).get('pods', '20'),
                    'services': env.get('quota', {}).get('services', '10')
                }
            }
        }
        
        # Создаем NetworkPolicy для namespace
        network_policy = {
            'apiVersion': 'networking.k8s.io/v1',
            'kind': 'NetworkPolicy',
            'metadata': {
                'name': f"{namespace_name}-default-deny",
                'namespace': namespace_name
            },
            'spec': {
                'podSelector': {},
                'policyTypes': ['Ingress', 'Egress'],
                'ingress': env.get('network_policy', {}).get('ingress', []),
                'egress': env.get('network_policy', {}).get('egress', [])
            }
        }
        
        # Создаем Role и RoleBinding для команды
        role = {
            'apiVersion': 'rbac.authorization.k8s.io/v1',
            'kind': 'Role',
            'metadata': {
                'name': f"{name}-admin",
                'namespace': namespace_name
            },
            'rules': [
                {
                    'apiGroups': ['*'],
                    'resources': ['*'],
                    'verbs': ['*']
                }
            ]
        }
        
        role_binding = {
            'apiVersion': 'rbac.authorization.k8s.io/v1',
            'kind': 'RoleBinding',
            'metadata': {
                'name': f"{name}-admin-binding",
                'namespace': namespace_name
            },
            'subjects': [
                {
                    'kind': 'Group',
                    'name': f"{name}-admins",
                    'apiGroup': 'rbac.authorization.k8s.io'
                }
            ],
            'roleRef': {
                'kind': 'Role',
                'name': f"{name}-admin",
                'apiGroup': 'rbac.authorization.k8s.io'
            }
        }
        
        try:
            # Создаем namespace
            api.create_namespace(body=namespace)
            logger.info(f"Namespace {namespace_name} создан для команды {name}, окружение {env_name}")
            
            # Создаем ResourceQuota
            api.create_namespaced_resource_quota(
                namespace=namespace_name,
                body=resource_quota
            )
            logger.info(f"ResourceQuota создана для namespace {namespace_name}")
            
            # Создаем NetworkPolicy
            net_api = kubernetes.client.NetworkingV1Api()
            net_api.create_namespaced_network_policy(
                namespace=namespace_name,
                body=network_policy
            )
            logger.info(f"NetworkPolicy создана для namespace {namespace_name}")
            
            # Создаем Role
            rbac_api.create_namespaced_role(
                namespace=namespace_name,
                body=role
            )
            logger.info(f"Role создана для namespace {namespace_name}")
            
            # Создаем RoleBinding
            rbac_api.create_namespaced_role_binding(
                namespace=namespace_name,
                body=role_binding
            )
            logger.info(f"RoleBinding создан для namespace {namespace_name}")
            
            created_namespaces.append({
                'name': namespace_name,
                'environment': env_name,
                'description': env_description
            })
            
        except kubernetes.client.exceptions.ApiException as e:
            logger.error(f"Ошибка при создании ресурсов для namespace {namespace_name}: {e}")
            if e.status == 409:  # Конфликт - ресурс уже существует
                logger.info(f"Namespace {namespace_name} уже существует")
                created_namespaces.append({
                    'name': namespace_name,
                    'environment': env_name,
                    'description': env_description,
                    'status': 'already_exists'
                })
            else:
                raise kopf.PermanentError(f"Не удалось создать ресурсы для namespace {namespace_name}: {e}")
    
    
    # Обновляем статус ресурса
    message = f'Созданы окружения для команды {name}: {len(created_namespaces)}'
    kopf.info(body, reason='Created', message=message)
    
    # Возвращаем информацию о созданных ресурсах
    return {
        'environments_created': len(created_namespaces),
        'namespaces': created_namespaces
    }

@kopf.on.update(group=GROUP, version=VERSION, plural=PLURAL_TEAMS)
def update_fn(body, spec, status, name, logger, **kwargs):
    """Обработчик обновления ресурса Team"""
    logger.info(f"Обновление ресурса Team {name}")
    
    # Получаем данные из спецификации
    environments = spec.get('environments', [])
    
    # Получаем текущие окружения из статуса
    current_namespaces = status.get('team-operator', {}).get('namespaces', [])
    current_namespace_names = [ns['name'] for ns in current_namespaces if 'name' in ns]
    
    # Создаем API-клиент Kubernetes
    api = kubernetes.client.CoreV1Api()
    rbac_api = kubernetes.client.RbacAuthorizationV1Api()
    
    updated_namespaces = []
    created_namespaces = []
    
    # Обрабатываем каждое окружение из спецификации
    for env in environments:
        env_name = env.get('name')
        env_description = env.get('description', '')
        env_labels = env.get('labels', {})
        
        if not env_name:
            logger.warning(f"Пропускаем окружение без имени для команды {name}")
            continue
        
        # Формируем имя namespace на основе имени команды и окружения
        namespace_name = f"{name}-{env_name}".lower()
        
        # Проверяем, существует ли уже namespace
        try:
            existing_ns = api.read_namespace(name=namespace_name)
            
            # Обновляем метки и аннотации namespace
            existing_ns.metadata.labels.update({
                'team': name,
                'environment': env_name,
                'managed-by': 'team-operator',
                **env_labels
            })
            existing_ns.metadata.annotations['description'] = env_description
            
            # Применяем изменения
            api.patch_namespace(name=namespace_name, body=existing_ns)
            logger.info(f"Namespace {namespace_name} обновлен")
            
            # Обновляем ResourceQuota
            try:
                resource_quota = {
                    'apiVersion': 'v1',
                    'kind': 'ResourceQuota',
                    'metadata': {
                        'name': f"{namespace_name}-quota",
                    },
                    'spec': {
                        'hard': {
                            'requests.cpu': env.get('quota', {}).get('cpu', '10'),
                            'requests.memory': env.get('quota', {}).get('memory', '20Gi'),
                            'limits.cpu': env.get('quota', {}).get('cpu_limit', '20'),
                            'limits.memory': env.get('quota', {}).get('memory_limit', '40Gi'),
                            'pods': env.get('quota', {}).get('pods', '20'),
                            'services': env.get('quota', {}).get('services', '10')
                        }
                    }
                }
                
                api.patch_namespaced_resource_quota(
                    name=f"{namespace_name}-quota",
                    namespace=namespace_name,
                    body=resource_quota
                )
                logger.info(f"ResourceQuota обновлена для namespace {namespace_name}")
            except kubernetes.client.exceptions.ApiException as e:
                if e.status == 404:  # Не найдено - создаем заново
                    resource_quota['metadata']['namespace'] = namespace_name
                    api.create_namespaced_resource_quota(
                        namespace=namespace_name,
                        body=resource_quota
                    )
                    logger.info(f"ResourceQuota создана для namespace {namespace_name}")
                else:
                    logger.error(f"Ошибка при обновлении ResourceQuota для {namespace_name}: {e}")
            
            updated_namespaces.append({
                'name': namespace_name,
                'environment': env_name,
                'description': env_description,
                'status': 'updated'
            })
            
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:  # Не найдено - создаем новый namespace
                # Создаем namespace
                namespace = {
                    'apiVersion': 'v1',
                    'kind': 'Namespace',
                    'metadata': {
                        'name': namespace_name,
                        'labels': {
                            'team': name,
                            'environment': env_name,
                            'managed-by': 'team-operator',
                            **env_labels
                        },
                        'annotations': {
                            'description': env_description
                        }
                    }
                }
                
                # Создаем ResourceQuota для namespace
                resource_quota = {
                    'apiVersion': 'v1',
                    'kind': 'ResourceQuota',
                    'metadata': {
                        'name': f"{namespace_name}-quota",
                        'namespace': namespace_name
                    },
                    'spec': {
                        'hard': {
                            'requests.cpu': env.get('quota', {}).get('cpu', '10'),
                            'requests.memory': env.get('quota', {}).get('memory', '20Gi'),
                            'limits.cpu': env.get('quota', {}).get('cpu_limit', '20'),
                            'limits.memory': env.get('quota', {}).get('memory_limit', '40Gi'),
                            'pods': env.get('quota', {}).get('pods', '20'),
                            'services': env.get('quota', {}).get('services', '10')
                        }
                    }
                }
                
                # Создаем NetworkPolicy для namespace
                network_policy = {
                    'apiVersion': 'networking.k8s.io/v1',
                    'kind': 'NetworkPolicy',
                    'metadata': {
                        'name': f"{namespace_name}-default-deny",
                        'namespace': namespace_name
                    },
                    'spec': {
                        'podSelector': {},
                        'policyTypes': ['Ingress', 'Egress'],
                        'ingress': env.get('network_policy', {}).get('ingress', []),
                        'egress': env.get('network_policy', {}).get('egress', [])
                    }
                }
                
                # Создаем Role и RoleBinding для команды
                role = {
                    'apiVersion': 'rbac.authorization.k8s.io/v1',
                    'kind': 'Role',
                    'metadata': {
                        'name': f"{name}-admin",
                        'namespace': namespace_name
                    },
                    'rules': [
                        {
                            'apiGroups': ['*'],
                            'resources': ['*'],
                            'verbs': ['*']
                        }
                    ]
                }
                
                role_binding = {
                    'apiVersion': 'rbac.authorization.k8s.io/v1',
                    'kind': 'RoleBinding',
                    'metadata': {
                        'name': f"{name}-admin-binding",
                        'namespace': namespace_name
                    },
                    'subjects': [
                        {
                            'kind': 'Group',
                            'name': f"{name}-admins",
                            'apiGroup': 'rbac.authorization.k8s.io'
                        }
                    ],
                    'roleRef': {
                        'kind': 'Role',
                        'name': f"{name}-admin",
                        'apiGroup': 'rbac.authorization.k8s.io'
                    }
                }
                
                try:
                    # Создаем namespace
                    api.create_namespace(body=namespace)
                    logger.info(f"Namespace {namespace_name} создан для команды {name}, окружение {env_name}")
                    
                    # Создаем ResourceQuota
                    api.create_namespaced_resource_quota(
                        namespace=namespace_name,
                        body=resource_quota
                    )
                    logger.info(f"ResourceQuota создана для namespace {namespace_name}")
                    
                    # Создаем NetworkPolicy
                    net_api = kubernetes.client.NetworkingV1Api()
                    net_api.create_namespaced_network_policy(
                        namespace=namespace_name,
                        body=network_policy
                    )
                    logger.info(f"NetworkPolicy создана для namespace {namespace_name}")
                    
                    # Создаем Role
                    rbac_api.create_namespaced_role(
                        namespace=namespace_name,
                        body=role
                    )
                    logger.info(f"Role создана для namespace {namespace_name}")
                    
                    # Создаем RoleBinding
                    rbac_api.create_namespaced_role_binding(
                        namespace=namespace_name,
                        body=role_binding
                    )
                    logger.info(f"RoleBinding создан для namespace {namespace_name}")
                    
                    created_namespaces.append({
                        'name': namespace_name,
                        'environment': env_name,
                        'description': env_description,
                        'status': 'created'
                    })
                    
                except kubernetes.client.exceptions.ApiException as e:
                    logger.error(f"Ошибка при создании ресурсов для namespace {namespace_name}: {e}")
                    raise kopf.PermanentError(f"Не удалось создать ресурсы для namespace {namespace_name}: {e}")
            else:
                logger.error(f"Ошибка при обновлении namespace {namespace_name}: {e}")
                raise kopf.PermanentError(f"Не удалось обновить namespace {namespace_name}: {e}")
    
    # Проверяем, есть ли окружения, которые нужно удалить
    env_names_in_spec = [f"{name}-{env.get('name')}".lower() for env in environments if env.get('name')]
    namespaces_to_delete = [ns for ns in current_namespace_names if ns not in env_names_in_spec]
    
    deleted_namespaces = []
    for ns_name in namespaces_to_delete:
        try:
            api.delete_namespace(name=ns_name)
            logger.info(f"Namespace {ns_name} удален")
            deleted_namespaces.append(ns_name)
        except kubernetes.client.exceptions.ApiException as e:
            logger.error(f"Ошибка при удалении namespace {ns_name}: {e}")
    
    
    # Обновляем статус ресурса
    message = f'Обновлены окружения для команды {name}: создано {len(created_namespaces)}, обновлено {len(updated_namespaces)}, удалено {len(deleted_namespaces)}'
    kopf.info(body, reason='Updated', message=message)
    
    # Возвращаем информацию об обновленных ресурсах
    return {
        'environments_created': len(created_namespaces),
        'environments_updated': len(updated_namespaces),
        'environments_deleted': len(deleted_namespaces),
        'namespaces': created_namespaces + updated_namespaces,
        'deleted_namespaces': deleted_namespaces
    }

@kopf.on.delete(group=GROUP, version=VERSION, plural=PLURAL_TEAMS)
def delete_fn(body, spec, name, logger, **kwargs):
    """Обработчик удаления ресурса Team"""
    logger.info(f"Удаление ресурса Team {name}")
    
    # Получаем данные из спецификации
    environments = spec.get('environments', [])
    
    # Создаем API-клиент Kubernetes
    api = kubernetes.client.CoreV1Api()
    
    deleted_namespaces = []
    
    # Удаляем namespace для каждого окружения
    for env in environments:
        env_name = env.get('name')
        
        if not env_name:
            continue
        
        # Формируем имя namespace на основе имени команды и окружения
        namespace_name = f"{name}-{env_name}".lower()
        
        try:
            # Удаляем namespace (это автоматически удалит все ресурсы внутри)
            api.delete_namespace(name=namespace_name)
            logger.info(f"Namespace {namespace_name} удален")
            deleted_namespaces.append(namespace_name)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:  # Не найдено
                logger.info(f"Namespace {namespace_name} не существует или уже удален")
            else:
                logger.warning(f"Ошибка при удалении namespace {namespace_name}: {e}")
    
    
    # Обновляем статус ресурса (хотя это не будет сохранено, так как ресурс удаляется)
    kopf.info(body, reason='Deleted', message=f'Удалены окружения для команды {name}: {len(deleted_namespaces)}')
    
    # Возвращаем информацию об удаленных ресурсах
    return {
        'environments_deleted': len(deleted_namespaces),
        'deleted_namespaces': deleted_namespaces
    }

@kopf.on.create(group=GROUP, version=VERSION, plural=PLURAL_USERS)
def create_user(body, spec, name, logger, **kwargs):
    """Обработчик создания ресурса User"""
    logger.info(f"Создание ресурса User {name}")
    
    # Убеждаемся, что пространство имен пользователей существует
    ensure_users_namespace(logger)
    
    # Получаем данные из спецификации
    full_name = spec.get('fullName', '')
    email = spec.get('email', '')
    teams = spec.get('teams', [])
    role = spec.get('role', 'developer')
    
    # Создаем API-клиент Kubernetes
    api = kubernetes.client.CoreV1Api()
    rbac_api = kubernetes.client.RbacAuthorizationV1Api()
    
    # Создаем ServiceAccount для пользователя
    service_account = {
        'apiVersion': 'v1',
        'kind': 'ServiceAccount',
        'metadata': {
            'name': name,
            'namespace': USERS_NAMESPACE,
            'labels': {
                'managed-by': 'team-operator',
                'user-email': email.replace('@', '-at-').replace('.', '-dot-'),
                'user-role': role
            },
            'annotations': {
                'team.example.com/full-name': full_name,
                'team.example.com/email': email
            }
        }
    }
    
    # Создаем Secret для токена ServiceAccount
    token_secret = {
        'apiVersion': 'v1',
        'kind': 'Secret',
        'metadata': {
            'name': f"{name}-token",
            'namespace': USERS_NAMESPACE,
            'annotations': {
                'kubernetes.io/service-account.name': name
            }
        },
        'type': 'kubernetes.io/service-account-token'
    }
    
    try:
        # Создаем ServiceAccount
        api.create_namespaced_service_account(
            namespace=USERS_NAMESPACE,
            body=service_account
        )
        logger.info(f"ServiceAccount {name} создан в пространстве имен {USERS_NAMESPACE}")
        
        # Создаем Secret для токена
        try:
            api.create_namespaced_secret(
                namespace=USERS_NAMESPACE,
                body=token_secret
            )
            logger.info(f"Secret {name}-token создан в пространстве имен {USERS_NAMESPACE}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 409:  # Конфликт - ресурс уже существует
                logger.info(f"Secret {name}-token уже существует в пространстве имен {USERS_NAMESPACE}")
            else:
                logger.error(f"Ошибка при создании Secret {name}-token: {e}")
                
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 409:  # Конфликт - ресурс уже существует
            logger.info(f"ServiceAccount {name} уже существует в пространстве имен {USERS_NAMESPACE}")
            
            # Проверяем, существует ли Secret для токена
            try:
                api.read_namespaced_secret(
                    name=f"{name}-token",
                    namespace=USERS_NAMESPACE
                )
                logger.info(f"Secret {name}-token уже существует в пространстве имен {USERS_NAMESPACE}")
            except kubernetes.client.exceptions.ApiException as e:
                if e.status == 404:  # Не найдено - создаем
                    try:
                        api.create_namespaced_secret(
                            namespace=USERS_NAMESPACE,
                            body=token_secret
                        )
                        logger.info(f"Secret {name}-token создан в пространстве имен {USERS_NAMESPACE}")
                    except kubernetes.client.exceptions.ApiException as e:
                        logger.error(f"Ошибка при создании Secret {name}-token: {e}")
        else:
            logger.error(f"Ошибка при создании ServiceAccount {name}: {e}")
            raise kopf.PermanentError(f"Не удалось создать ServiceAccount {name}: {e}")
    
    # Создаем RoleBinding для каждой команды
    for team_name in teams:
        # Проверяем, существует ли команда
        try:
            custom_api = kubernetes.client.CustomObjectsApi()
            team = custom_api.get_cluster_custom_object(
                group=GROUP,
                version=VERSION,
                plural=PLURAL_TEAMS,
                name=team_name
            )
            
            # Получаем окружения команды
            environments = team.get('spec', {}).get('environments', [])
            
            # Создаем RoleBinding для каждого окружения
            for env in environments:
                env_name = env.get('name')
                if not env_name:
                    continue
                
                # Формируем имя namespace на основе имени команды и окружения
                namespace_name = f"{team_name}-{env_name}".lower()
                
                # Определяем роль в зависимости от роли пользователя
                role_name = ""
                if role == "admin":
                    role_name = f"{team_name}-admin"
                elif role == "developer":
                    role_name = f"{team_name}-developer"
                else:  # viewer
                    role_name = f"{team_name}-viewer"
                
                # Проверяем, существует ли роль, если нет - создаем
                try:
                    rbac_api.read_namespaced_role(name=role_name, namespace=namespace_name)
                except kubernetes.client.exceptions.ApiException as e:
                    if e.status == 404:  # Не найдено - создаем
                        # Создаем соответствующую роль
                        if role == "admin":
                            role_def = {
                                'apiVersion': 'rbac.authorization.k8s.io/v1',
                                'kind': 'Role',
                                'metadata': {
                                    'name': role_name,
                                    'namespace': namespace_name
                                },
                                'rules': [
                                    {
                                        'apiGroups': ['*'],
                                        'resources': ['*'],
                                        'verbs': ['*']
                                    }
                                ]
                            }
                        elif role == "developer":
                            role_def = {
                                'apiVersion': 'rbac.authorization.k8s.io/v1',
                                'kind': 'Role',
                                'metadata': {
                                    'name': role_name,
                                    'namespace': namespace_name
                                },
                                'rules': [
                                    {
                                        'apiGroups': [''],
                                        'resources': ['pods', 'services', 'configmaps', 'secrets'],
                                        'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']
                                    },
                                    {
                                        'apiGroups': ['apps'],
                                        'resources': ['deployments', 'statefulsets', 'daemonsets'],
                                        'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']
                                    },
                                    {
                                        'apiGroups': ['batch'],
                                        'resources': ['jobs', 'cronjobs'],
                                        'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']
                                    }
                                ]
                            }
                        else:  # viewer
                            role_def = {
                                'apiVersion': 'rbac.authorization.k8s.io/v1',
                                'kind': 'Role',
                                'metadata': {
                                    'name': role_name,
                                    'namespace': namespace_name
                                },
                                'rules': [
                                    {
                                        'apiGroups': ['*'],
                                        'resources': ['*'],
                                        'verbs': ['get', 'list', 'watch']
                                    }
                                ]
                            }
                        
                        try:
                            rbac_api.create_namespaced_role(
                                namespace=namespace_name,
                                body=role_def
                            )
                            logger.info(f"Role {role_name} создана в пространстве имен {namespace_name}")
                        except kubernetes.client.exceptions.ApiException as e:
                            logger.error(f"Ошибка при создании Role {role_name}: {e}")
                
                # Создаем RoleBinding
                role_binding = {
                    'apiVersion': 'rbac.authorization.k8s.io/v1',
                    'kind': 'RoleBinding',
                    'metadata': {
                        'name': f"{name}-{role_name}-binding",
                        'namespace': namespace_name
                    },
                    'subjects': [
                        {
                            'kind': 'ServiceAccount',
                            'name': name,
                            'namespace': USERS_NAMESPACE
                        }
                    ],
                    'roleRef': {
                        'kind': 'Role',
                        'name': role_name,
                        'apiGroup': 'rbac.authorization.k8s.io'
                    }
                }
                
                try:
                    rbac_api.create_namespaced_role_binding(
                        namespace=namespace_name,
                        body=role_binding
                    )
                    logger.info(f"RoleBinding {name}-{role_name}-binding создан в пространстве имен {namespace_name}")
                except kubernetes.client.exceptions.ApiException as e:
                    if e.status == 409:  # Конфликт - ресурс уже существует
                        logger.info(f"RoleBinding {name}-{role_name}-binding уже существует в пространстве имен {namespace_name}")
                    else:
                        logger.error(f"Ошибка при создании RoleBinding {name}-{role_name}-binding: {e}")
            
        except kubernetes.client.exceptions.ApiException as e:
            logger.warning(f"Команда {team_name} не найдена: {e}")
    
    # Создаем kubeconfig для пользователя
    try:
        # Получаем токен из Secret
        secret = api.read_namespaced_secret(
            name=f"{name}-token",
            namespace=USERS_NAMESPACE
        )
        
        if secret.data and 'token' in secret.data:
            token = base64.b64decode(secret.data['token']).decode('utf-8')
            logger.info(f"Токен для пользователя {name} получен")
            
            # Получаем информацию о кластере
            try:
                # Получаем URL API-сервера
                kube_config = kubernetes.config.load_kube_config()
                contexts, active_context = kubernetes.config.list_kube_config_contexts()
                if active_context:
                    cluster_name = active_context['context']['cluster']
                    cluster_info = None
                    for context in contexts:
                        if context['context']['cluster'] == cluster_name:
                            cluster_info = context
                            break
                    
                    if cluster_info:
                        # Получаем информацию о кластере
                        k8s_client = kubernetes.client.ApiClient()
                        k8s_configuration = k8s_client.configuration
                        
                        # Создаем kubeconfig
                        kubeconfig = {
                            "apiVersion": "v1",
                            "kind": "Config",
                            "current-context": name,
                            "clusters": [
                                {
                                    "name": cluster_name,
                                    "cluster": {
                                        "server": k8s_configuration.host,
                                        "certificate-authority-data": base64.b64encode(k8s_configuration.ssl_ca_cert.encode()).decode() if k8s_configuration.ssl_ca_cert else None
                                    }
                                }
                            ],
                            "users": [
                                {
                                    "name": name,
                                    "user": {
                                        "token": token
                                    }
                                }
                            ],
                            "contexts": [
                                {
                                    "name": name,
                                    "context": {
                                        "cluster": cluster_name,
                                        "user": name
                                    }
                                }
                            ]
                        }
                        
                        # Создаем ConfigMap с kubeconfig
                        kubeconfig_cm = {
                            'apiVersion': 'v1',
                            'kind': 'ConfigMap',
                            'metadata': {
                                'name': f"{name}-kubeconfig",
                                'namespace': USERS_NAMESPACE,
                                'labels': {
                                    'managed-by': 'team-operator',
                                    'user': name
                                },
                                'ownerReferences': [
                                    {
                                        'apiVersion': f"{GROUP}/{VERSION}",
                                        'kind': 'User',
                                        'name': name,
                                        'uid': body['metadata']['uid'],
                                        'controller': True
                                    }
                                ]
                            },
                            'data': {
                                'config': yaml.dump(kubeconfig)
                            }
                        }
                        
                        # Удаляем None значения из kubeconfig
                        if kubeconfig["clusters"][0]["cluster"]["certificate-authority-data"] is None:
                            del kubeconfig["clusters"][0]["cluster"]["certificate-authority-data"]
                            # Если используется небезопасное соединение, добавляем insecure-skip-tls-verify
                            if not k8s_configuration.verify_ssl:
                                kubeconfig["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = True
                        
                        # Удаляем None значения из kubeconfig
                        if kubeconfig["clusters"][0]["cluster"]["certificate-authority-data"] is None:
                            del kubeconfig["clusters"][0]["cluster"]["certificate-authority-data"]
                            # Если используется небезопасное соединение, добавляем insecure-skip-tls-verify
                            if not k8s_configuration.verify_ssl:
                                kubeconfig["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = True
                        
                        try:
                            # Создаем или обновляем ConfigMap
                            try:
                                api.read_namespaced_config_map(
                                    name=f"{name}-kubeconfig",
                                    namespace=USERS_NAMESPACE
                                )
                                # ConfigMap существует - обновляем
                                api.replace_namespaced_config_map(
                                    name=f"{name}-kubeconfig",
                                    namespace=USERS_NAMESPACE,
                                    body=kubeconfig_cm
                                )
                                logger.info(f"ConfigMap {name}-kubeconfig обновлен в пространстве имен {USERS_NAMESPACE}")
                            except kubernetes.client.exceptions.ApiException as e:
                                if e.status == 404:  # Не найдено - создаем
                                    api.create_namespaced_config_map(
                                        namespace=USERS_NAMESPACE,
                                        body=kubeconfig_cm
                                    )
                                    logger.info(f"ConfigMap {name}-kubeconfig создан в пространстве имен {USERS_NAMESPACE}")
                                else:
                                    logger.error(f"Ошибка при проверке ConfigMap {name}-kubeconfig: {e}")
                        except kubernetes.client.exceptions.ApiException as e:
                            logger.error(f"Ошибка при создании/обновлении ConfigMap {name}-kubeconfig: {e}")
                    else:
                        logger.warning(f"Не удалось получить информацию о кластере для пользователя {name}")
                else:
                    logger.warning(f"Не удалось получить активный контекст для пользователя {name}")
            except Exception as e:
                logger.error(f"Ошибка при создании kubeconfig для пользователя {name}: {e}")
        else:
            logger.warning(f"Токен для пользователя {name} не найден в Secret")
    except kubernetes.client.exceptions.ApiException as e:
        logger.warning(f"Не удалось получить Secret с токеном для пользователя {name}: {e}")
    
    # Обновляем статус ресурса
    kopf.info(body, reason='Created', message=f'Пользователь {name} создан, добавлен в команды: {teams}')
    
    return {
        'service_account_created': True,
        'teams': teams,
        'kubeconfig_created': True
    }

@kopf.on.update(group=GROUP, version=VERSION, plural=PLURAL_USERS)
def update_user(body, spec, status, name, logger, **kwargs):
    """Обработчик обновления ресурса User"""
    logger.info(f"Обновление ресурса User {name}")
    
    # Убеждаемся, что пространство имен пользователей существует
    ensure_users_namespace(logger)
    
    # Получаем данные из спецификации
    full_name = spec.get('fullName', '')
    email = spec.get('email', '')
    new_teams = spec.get('teams', [])
    role = spec.get('role', 'developer')
    
    # Получаем текущие команды из статуса
    current_teams = status.get('team-operator', {}).get('teams', [])
    
    # Создаем API-клиент Kubernetes
    api = kubernetes.client.CoreV1Api()
    rbac_api = kubernetes.client.RbacAuthorizationV1Api()
    
    # Обновляем ServiceAccount
    try:
        # Получаем текущий ServiceAccount
        service_account = api.read_namespaced_service_account(
            name=name,
            namespace=USERS_NAMESPACE
        )
        
        # Обновляем метки и аннотации
        service_account.metadata.labels['user-email'] = email.replace('@', '-at-').replace('.', '-dot-')
        service_account.metadata.labels['user-role'] = role
        service_account.metadata.annotations['team.example.com/full-name'] = full_name
        service_account.metadata.annotations['team.example.com/email'] = email
        
        # Применяем изменения
        api.replace_namespaced_service_account(
            name=name,
            namespace=USERS_NAMESPACE,
            body=service_account
        )
        logger.info(f"ServiceAccount {name} обновлен")
        
        # Проверяем, существует ли Secret для токена
        try:
            api.read_namespaced_secret(
                name=f"{name}-token",
                namespace=USERS_NAMESPACE
            )
            logger.info(f"Secret {name}-token уже существует в пространстве имен {USERS_NAMESPACE}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:  # Не найдено - создаем
                # Создаем Secret для токена ServiceAccount
                token_secret = {
                    'apiVersion': 'v1',
                    'kind': 'Secret',
                    'metadata': {
                        'name': f"{name}-token",
                        'namespace': USERS_NAMESPACE,
                        'annotations': {
                            'kubernetes.io/service-account.name': name
                        }
                    },
                    'type': 'kubernetes.io/service-account-token'
                }
                
                try:
                    api.create_namespaced_secret(
                        namespace=USERS_NAMESPACE,
                        body=token_secret
                    )
                    logger.info(f"Secret {name}-token создан в пространстве имен {USERS_NAMESPACE}")
                except kubernetes.client.exceptions.ApiException as e:
                    logger.error(f"Ошибка при создании Secret {name}-token: {e}")
            else:
                logger.warning(f"Ошибка при проверке Secret {name}-token: {e}")
                
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:  # Не найдено - создаем
            # Создаем ServiceAccount
            service_account = {
                'apiVersion': 'v1',
                'kind': 'ServiceAccount',
                'metadata': {
                    'name': name,
                    'namespace': USERS_NAMESPACE,
                    'labels': {
                        'managed-by': 'team-operator',
                        'user-email': email.replace('@', '-at-').replace('.', '-dot-'),
                        'user-role': role
                    },
                    'annotations': {
                        'team.example.com/full-name': full_name,
                        'team.example.com/email': email
                    }
                }
            }
            
            # Создаем Secret для токена ServiceAccount
            token_secret = {
                'apiVersion': 'v1',
                'kind': 'Secret',
                'metadata': {
                    'name': f"{name}-token",
                    'namespace': USERS_NAMESPACE,
                    'annotations': {
                        'kubernetes.io/service-account.name': name
                    }
                },
                'type': 'kubernetes.io/service-account-token'
            }
            
            try:
                api.create_namespaced_service_account(
                    namespace=USERS_NAMESPACE,
                    body=service_account
                )
                logger.info(f"ServiceAccount {name} создан")
                
                try:
                    api.create_namespaced_secret(
                        namespace=USERS_NAMESPACE,
                        body=token_secret
                    )
                    logger.info(f"Secret {name}-token создан в пространстве имен {USERS_NAMESPACE}")
                except kubernetes.client.exceptions.ApiException as e:
                    logger.error(f"Ошибка при создании Secret {name}-token: {e}")
                    
            except kubernetes.client.exceptions.ApiException as e:
                logger.error(f"Ошибка при создании ServiceAccount {name}: {e}")
                raise kopf.PermanentError(f"Не удалось создать ServiceAccount {name}: {e}")
        else:
            logger.error(f"Ошибка при обновлении ServiceAccount {name}: {e}")
            raise kopf.PermanentError(f"Не удалось обновить ServiceAccount {name}: {e}")
    
    # Находим команды, которые нужно добавить и удалить
    teams_to_add = [team for team in new_teams if team not in current_teams]
    teams_to_remove = [team for team in current_teams if team not in new_teams]
    
    # Добавляем пользователя в новые команды
    for team_name in teams_to_add:
        try:
            custom_api = kubernetes.client.CustomObjectsApi()
            team = custom_api.get_cluster_custom_object(
                group=GROUP,
                version=VERSION,
                plural=PLURAL_TEAMS,
                name=team_name
            )
            
            # Получаем окружения команды
            environments = team.get('spec', {}).get('environments', [])
            
            # Создаем RoleBinding для каждого окружения
            for env in environments:
                env_name = env.get('name')
                if not env_name:
                    continue
                
                # Формируем имя namespace на основе имени команды и окружения
                namespace_name = f"{team_name}-{env_name}".lower()
                
                # Определяем роль в зависимости от роли пользователя
                role_name = ""
                if role == "admin":
                    role_name = f"{team_name}-admin"
                elif role == "developer":
                    role_name = f"{team_name}-developer"
                else:  # viewer
                    role_name = f"{team_name}-viewer"
                
                # Проверяем, существует ли роль, если нет - создаем
                try:
                    rbac_api.read_namespaced_role(name=role_name, namespace=namespace_name)
                except kubernetes.client.exceptions.ApiException as e:
                    if e.status == 404:  # Не найдено - создаем
                        # Создаем соответствующую роль (код аналогичен create_user)
                        if role == "admin":
                            role_def = {
                                'apiVersion': 'rbac.authorization.k8s.io/v1',
                                'kind': 'Role',
                                'metadata': {
                                    'name': role_name,
                                    'namespace': namespace_name
                                },
                                'rules': [
                                    {
                                        'apiGroups': ['*'],
                                        'resources': ['*'],
                                        'verbs': ['*']
                                    }
                                ]
                            }
                        elif role == "developer":
                            role_def = {
                                'apiVersion': 'rbac.authorization.k8s.io/v1',
                                'kind': 'Role',
                                'metadata': {
                                    'name': role_name,
                                    'namespace': namespace_name
                                },
                                'rules': [
                                    {
                                        'apiGroups': [''],
                                        'resources': ['pods', 'services', 'configmaps', 'secrets'],
                                        'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']
                                    },
                                    {
                                        'apiGroups': ['apps'],
                                        'resources': ['deployments', 'statefulsets', 'daemonsets'],
                                        'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']
                                    },
                                    {
                                        'apiGroups': ['batch'],
                                        'resources': ['jobs', 'cronjobs'],
                                        'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']
                                    }
                                ]
                            }
                        else:  # viewer
                            role_def = {
                                'apiVersion': 'rbac.authorization.k8s.io/v1',
                                'kind': 'Role',
                                'metadata': {
                                    'name': role_name,
                                    'namespace': namespace_name
                                },
                                'rules': [
                                    {
                                        'apiGroups': ['*'],
                                        'resources': ['*'],
                                        'verbs': ['get', 'list', 'watch']
                                    }
                                ]
                            }
                        
                        try:
                            rbac_api.create_namespaced_role(
                                namespace=namespace_name,
                                body=role_def
                            )
                            logger.info(f"Role {role_name} создана в пространстве имен {namespace_name}")
                        except kubernetes.client.exceptions.ApiException as e:
                            logger.error(f"Ошибка при создании Role {role_name}: {e}")
                
                # Создаем RoleBinding
                role_binding = {
                    'apiVersion': 'rbac.authorization.k8s.io/v1',
                    'kind': 'RoleBinding',
                    'metadata': {
                        'name': f"{name}-{role_name}-binding",
                        'namespace': namespace_name
                    },
                    'subjects': [
                        {
                            'kind': 'ServiceAccount',
                            'name': name,
                            'namespace': USERS_NAMESPACE
                        }
                    ],
                    'roleRef': {
                        'kind': 'Role',
                        'name': role_name,
                        'apiGroup': 'rbac.authorization.k8s.io'
                    }
                }
                
                try:
                    rbac_api.create_namespaced_role_binding(
                        namespace=namespace_name,
                        body=role_binding
                    )
                    logger.info(f"RoleBinding {name}-{role_name}-binding создан в пространстве имен {namespace_name}")
                except kubernetes.client.exceptions.ApiException as e:
                    if e.status == 409:  # Конфликт - ресурс уже существует
                        logger.info(f"RoleBinding {name}-{role_name}-binding уже существует в пространстве имен {namespace_name}")
                    else:
                        logger.error(f"Ошибка при создании RoleBinding {name}-{role_name}-binding: {e}")
            
        except kubernetes.client.exceptions.ApiException as e:
            logger.warning(f"Команда {team_name} не найдена: {e}")
    
    # Удаляем пользователя из команд, которые больше не указаны
    for team_name in teams_to_remove:
        try:
            custom_api = kubernetes.client.CustomObjectsApi()
            team = custom_api.get_cluster_custom_object(
                group=GROUP,
                version=VERSION,
                plural=PLURAL_TEAMS,
                name=team_name
            )
            
            # Получаем окружения команды
            environments = team.get('spec', {}).get('environments', [])
            
            # Удаляем RoleBinding для каждого окружения
            for env in environments:
                env_name = env.get('name')
                if not env_name:
                    continue
                
                # Формируем имя namespace на основе имени команды и окружения
                namespace_name = f"{team_name}-{env_name}".lower()
                
                # Определяем возможные имена ролей
                role_names = [f"{team_name}-admin", f"{team_name}-developer", f"{team_name}-viewer"]
                
                # Удаляем все возможные RoleBinding
                for role_name in role_names:
                    try:
                        rbac_api.delete_namespaced_role_binding(
                            name=f"{name}-{role_name}-binding",
                            namespace=namespace_name
                        )
                        logger.info(f"RoleBinding {name}-{role_name}-binding удален из пространства имен {namespace_name}")
                    except kubernetes.client.exceptions.ApiException as e:
                        if e.status == 404:  # Не найдено
                            logger.info(f"RoleBinding {name}-{role_name}-binding не существует в пространстве имен {namespace_name}")
                        else:
                            logger.warning(f"Ошибка при удалении RoleBinding {name}-{role_name}-binding: {e}")
            
        except kubernetes.client.exceptions.ApiException as e:
            logger.warning(f"Команда {team_name} не найдена: {e}")
    
    # Обновляем kubeconfig для пользователя
    try:
        # Получаем токен из Secret
        secret = api.read_namespaced_secret(
            name=f"{name}-token",
            namespace=USERS_NAMESPACE
        )
        
        if secret.data and 'token' in secret.data:
            token = base64.b64decode(secret.data['token']).decode('utf-8')
            logger.info(f"Токен для пользователя {name} получен")
            
            # Получаем информацию о кластере
            try:
                # Получаем URL API-сервера
                kube_config = kubernetes.config.load_kube_config()
                contexts, active_context = kubernetes.config.list_kube_config_contexts()
                if active_context:
                    cluster_name = active_context['context']['cluster']
                    cluster_info = None
                    for context in contexts:
                        if context['context']['cluster'] == cluster_name:
                            cluster_info = context
                            break
                    
                    if cluster_info:
                        # Получаем информацию о кластере
                        k8s_client = kubernetes.client.ApiClient()
                        k8s_configuration = k8s_client.configuration
                        
                        # Создаем kubeconfig
                        kubeconfig = {
                            "apiVersion": "v1",
                            "kind": "Config",
                            "current-context": name,
                            "clusters": [
                                {
                                    "name": cluster_name,
                                    "cluster": {
                                        "server": k8s_configuration.host,
                                        "certificate-authority-data": base64.b64encode(k8s_configuration.ssl_ca_cert.encode()).decode() if k8s_configuration.ssl_ca_cert else None
                                    }
                                }
                            ],
                            "users": [
                                {
                                    "name": name,
                                    "user": {
                                        "token": token
                                    }
                                }
                            ],
                            "contexts": [
                                {
                                    "name": name,
                                    "context": {
                                        "cluster": cluster_name,
                                        "user": name
                                    }
                                }
                            ]
                        }
                        
                        # Создаем ConfigMap с kubeconfig
                        kubeconfig_cm = {
                            'apiVersion': 'v1',
                            'kind': 'ConfigMap',
                            'metadata': {
                                'name': f"{name}-kubeconfig",
                                'namespace': USERS_NAMESPACE,
                                'labels': {
                                    'managed-by': 'team-operator',
                                    'user': name
                                },
                                'ownerReferences': [
                                    {
                                        'apiVersion': f"{GROUP}/{VERSION}",
                                        'kind': 'User',
                                        'name': name,
                                        'uid': body['metadata']['uid'],
                                        'controller': True
                                    }
                                ]
                            },
                            'data': {
                                'config': yaml.dump(kubeconfig)
                            }
                        }
                        
                        try:
                            # Создаем или обновляем ConfigMap
                            try:
                                api.read_namespaced_config_map(
                                    name=f"{name}-kubeconfig",
                                    namespace=USERS_NAMESPACE
                                )
                                # ConfigMap существует - обновляем
                                api.replace_namespaced_config_map(
                                    name=f"{name}-kubeconfig",
                                    namespace=USERS_NAMESPACE,
                                    body=kubeconfig_cm
                                )
                                logger.info(f"ConfigMap {name}-kubeconfig обновлен в пространстве имен {USERS_NAMESPACE}")
                            except kubernetes.client.exceptions.ApiException as e:
                                if e.status == 404:  # Не найдено - создаем
                                    api.create_namespaced_config_map(
                                        namespace=USERS_NAMESPACE,
                                        body=kubeconfig_cm
                                    )
                                    logger.info(f"ConfigMap {name}-kubeconfig создан в пространстве имен {USERS_NAMESPACE}")
                                else:
                                    logger.error(f"Ошибка при проверке ConfigMap {name}-kubeconfig: {e}")
                        except kubernetes.client.exceptions.ApiException as e:
                            logger.error(f"Ошибка при создании/обновлении ConfigMap {name}-kubeconfig: {e}")
                    else:
                        logger.warning(f"Не удалось получить информацию о кластере для пользователя {name}")
                else:
                    logger.warning(f"Не удалось получить активный контекст для пользователя {name}")
            except Exception as e:
                logger.error(f"Ошибка при создании kubeconfig для пользователя {name}: {e}")
        else:
            logger.warning(f"Токен для пользователя {name} не найден в Secret")
    except kubernetes.client.exceptions.ApiException as e:
        logger.warning(f"Не удалось получить Secret с токеном для пользователя {name}: {e}")
    
    # Обновляем статус ресурса
    message = f'Пользователь {name} обновлен, добавлен в команды: {teams_to_add}, удален из команд: {teams_to_remove}'
    kopf.info(body, reason='Updated', message=message)
    
    return {
        'service_account_updated': True,
        'teams': new_teams,
        'teams_added': teams_to_add,
        'teams_removed': teams_to_remove,
        'kubeconfig_updated': True
    }

@kopf.on.field(group=GROUP, version=VERSION, plural=PLURAL_USERS, field='spec.fullName')
def get_user_kubeconfig_handler(body, name, logger, **kwargs):
    """Обработчик для получения kubeconfig пользователя"""
    logger.info(f"Запрос kubeconfig для пользователя {name}")
    
    # Получаем kubeconfig из ConfigMap
    kubeconfig = get_user_kubeconfig(name, USERS_NAMESPACE, logger)
    
    if kubeconfig:
        logger.info(f"Kubeconfig для пользователя {name} получен")
        # Обновляем аннотацию с временем последнего запроса kubeconfig
        try:
            custom_api = kubernetes.client.CustomObjectsApi()
            patch = {
                'metadata': {
                    'annotations': {
                        'team.example.com/last-kubeconfig-request': kubernetes.client.ApiClient().select_header_value(
                            [kubernetes.client.ApiClient().RFC3339_DATETIME_FORMATTER(datetime.datetime.now(datetime.timezone.utc))
                            ]
                        )
                    }
                }
            }
            custom_api.patch_cluster_custom_object(
                group=GROUP,
                version=VERSION,
                plural=PLURAL_USERS,
                name=name,
                body=patch
            )
        except Exception as e:
            logger.warning(f"Не удалось обновить аннотацию для пользователя {name}: {e}")
    else:
        logger.warning(f"Не удалось получить kubeconfig для пользователя {name}")

@kopf.on.delete(group=GROUP, version=VERSION, plural=PLURAL_USERS)
def delete_user(body, spec, name, logger, **kwargs):
    """Обработчик удаления ресурса User"""
    logger.info(f"Удаление ресурса User {name}")
    
    # Получаем данные из спецификации
    teams = spec.get('teams', [])
    
    # Создаем API-клиент Kubernetes
    api = kubernetes.client.CoreV1Api()
    rbac_api = kubernetes.client.RbacAuthorizationV1Api()
    
    # Удаляем RoleBinding для каждой команды
    for team_name in teams:
        try:
            custom_api = kubernetes.client.CustomObjectsApi()
            team = custom_api.get_cluster_custom_object(
                group=GROUP,
                version=VERSION,
                plural=PLURAL_TEAMS,
                name=team_name
            )
            
            # Получаем окружения команды
            environments = team.get('spec', {}).get('environments', [])
            
            # Удаляем RoleBinding для каждого окружения
            for env in environments:
                env_name = env.get('name')
                if not env_name:
                    continue
                
                # Формируем имя namespace на основе имени команды и окружения
                namespace_name = f"{team_name}-{env_name}".lower()
                
                # Определяем возможные имена ролей
                role_names = [f"{team_name}-admin", f"{team_name}-developer", f"{team_name}-viewer"]
                
                # Удаляем все возможные RoleBinding
                for role_name in role_names:
                    try:
                        rbac_api.delete_namespaced_role_binding(
                            name=f"{name}-{role_name}-binding",
                            namespace=namespace_name
                        )
                        logger.info(f"RoleBinding {name}-{role_name}-binding удален из пространства имен {namespace_name}")
                    except kubernetes.client.exceptions.ApiException as e:
                        if e.status == 404:  # Не найдено
                            logger.info(f"RoleBinding {name}-{role_name}-binding не существует в пространстве имен {namespace_name}")
                        else:
                            logger.warning(f"Ошибка при удалении RoleBinding {name}-{role_name}-binding: {e}")
            
        except kubernetes.client.exceptions.ApiException as e:
            logger.warning(f"Команда {team_name} не найдена: {e}")
    
    # Удаляем ConfigMap с kubeconfig
    try:
        api.delete_namespaced_config_map(
            name=f"{name}-kubeconfig",
            namespace=USERS_NAMESPACE
        )
        logger.info(f"ConfigMap {name}-kubeconfig удален из пространства имен {USERS_NAMESPACE}")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:  # Не найдено
            logger.info(f"ConfigMap {name}-kubeconfig не существует в пространстве имен {USERS_NAMESPACE}")
        else:
            logger.warning(f"Ошибка при удалении ConfigMap {name}-kubeconfig: {e}")
    
    # Удаляем Secret с токеном
    try:
        api.delete_namespaced_secret(
            name=f"{name}-token",
            namespace=USERS_NAMESPACE
        )
        logger.info(f"Secret {name}-token удален из пространства имен {USERS_NAMESPACE}")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:  # Не найдено
            logger.info(f"Secret {name}-token не существует в пространстве имен {USERS_NAMESPACE}")
        else:
            logger.warning(f"Ошибка при удалении Secret {name}-token: {e}")
    
    # Удаляем ServiceAccount
    try:
        api.delete_namespaced_service_account(
            name=name,
            namespace=USERS_NAMESPACE
        )
        logger.info(f"ServiceAccount {name} удален из пространства имен {USERS_NAMESPACE}")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:  # Не найдено
            logger.info(f"ServiceAccount {name} не существует в пространстве имен {USERS_NAMESPACE}")
        else:
            logger.warning(f"Ошибка при удалении ServiceAccount {name}: {e}")
    
    # Обновляем статус ресурса (хотя это не будет сохранено, так как ресурс удаляется)
    kopf.info(body, reason='Deleted', message=f'Пользователь {name} удален из команд: {teams}')
    
    return {
        'service_account_deleted': True,
        'teams_removed': teams
    }

def get_user_kubeconfig(name, namespace, logger):
    """Получает kubeconfig пользователя из ConfigMap"""
    api = kubernetes.client.CoreV1Api()
    
    try:
        config_map = api.read_namespaced_config_map(
            name=f"{name}-kubeconfig",
            namespace=namespace
        )
        
        if config_map.data and 'config' in config_map.data:
            return config_map.data['config']
        else:
            logger.warning(f"ConfigMap {name}-kubeconfig не содержит данных конфигурации")
            return None
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:  # Не найдено
            logger.warning(f"ConfigMap {name}-kubeconfig не существует в пространстве имен {namespace}")
        else:
            logger.error(f"Ошибка при получении ConfigMap {name}-kubeconfig: {e}")
        return None

def main():
    """Основная функция для запуска оператора"""
    # Пытаемся загрузить конфигурацию из кластера, если не получается - из локального kubeconfig
    try:
        kubernetes.config.load_incluster_config()
        logger.info("Запуск оператора внутри кластера Kubernetes")
    except kubernetes.config.config_exception.ConfigException:
        try:
            kubernetes.config.load_kube_config()
            logger.info("Запуск оператора вне кластера Kubernetes с использованием kubeconfig")
        except kubernetes.config.config_exception.ConfigException:
            logger.error("Не удалось загрузить конфигурацию Kubernetes. Убедитесь, что kubeconfig доступен или оператор запущен в кластере.")
            raise
    
    # Убеждаемся, что пространство имен пользователей существует
    ensure_users_namespace(logger)
    
    # Запускаем оператор
    kopf.run()

if __name__ == "__main__":
    # Парсинг аргументов командной строки
    parser = argparse.ArgumentParser(description='Kubernetes оператор для управления командами и пользователями')
    parser.add_argument('--kubeconfig', help='Путь к файлу kubeconfig для запуска вне кластера')
    parser.add_argument('--namespace', help='Пространство имен для наблюдения (по умолчанию - все)')
    parser.add_argument('--verbose', action='store_true', help='Включить подробное логирование')
    args = parser.parse_args()
    
    # Настройка уровня логирования
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Включен режим подробного логирования")
    
    # Установка переменной окружения KUBECONFIG, если указан путь к kubeconfig
    if args.kubeconfig:
        os.environ['KUBECONFIG'] = args.kubeconfig
        logger.info(f"Используется kubeconfig из {args.kubeconfig}")
    
    # Запуск оператора
    try:
        main()
    except Exception as e:
        logger.error(f"Ошибка при запуске оператора: {e}")
        sys.exit(1)
