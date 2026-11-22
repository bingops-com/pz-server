FROM hashicorp/terraform:1.10.5 AS terraform
FROM debian:bookworm-slim

ARG KUBECTL_VERSION=v1.36.1
ARG HELM_VERSION=v3.19.4
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl git python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/* \
    && curl --fail --silent --show-error --location "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" --output /usr/local/bin/kubectl \
    && curl --fail --silent --show-error --location "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" --output /tmp/kubectl.sha256 \
    && printf '%s  /usr/local/bin/kubectl\n' "$(cat /tmp/kubectl.sha256)" | sha256sum --check \
    && chmod 0755 /usr/local/bin/kubectl \
    && rm /tmp/kubectl.sha256 \
    && curl --fail --silent --show-error --location "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" --output /tmp/helm.tar.gz \
    && curl --fail --silent --show-error --location "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz.sha256sum" --output /tmp/helm.sha256sum \
    && printf '%s  /tmp/helm.tar.gz\n' "$(cut -d ' ' -f 1 /tmp/helm.sha256sum)" | sha256sum --check \
    && tar -xzf /tmp/helm.tar.gz -C /tmp \
    && install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm \
    && rm -rf /tmp/linux-amd64 /tmp/helm.tar.gz /tmp/helm.sha256sum \
    && useradd --create-home --uid 1000 gaas

COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY gaas /opt/gaas/gaas
COPY charts/pz-server /opt/gaas/charts/pz-server
ENV PYTHONPATH=/opt/gaas
USER 1000:1000
WORKDIR /opt/gaas
ENTRYPOINT ["python3", "-m", "gaas.controller"]
