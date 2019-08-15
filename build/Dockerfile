FROM quay.io/operator-framework/ansible-operator:v0.5.0

RUN set -ex && \
    curl -s -L https://storage.googleapis.com/kubernetes-release/release/v1.14.2/bin/linux/amd64/kubectl -o ${HOME}/kubectl && \
    chmod 755 ${HOME}/kubectl

COPY roles/ ${HOME}/roles/
COPY watches.yaml ${HOME}/watches.yaml
