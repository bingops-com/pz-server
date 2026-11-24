FROM hashicorp/terraform:1.10.5 AS terraform
FROM terraform AS providers
# Download public providers once in CI, rather than on every gateway start/stop.
# The module and dependency lock must match the configured gateway source.
ARG GATEWAY_SOURCE_REVISION=2db698e654a317b92694339e90f0bd4ae8ec07db
RUN git init -q /source \
    && git -C /source fetch --depth=1 https://github.com/bingops-com/labops.git "${GATEWAY_SOURCE_REVISION}" \
    && git -C /source checkout --detach FETCH_HEAD \
    && mkdir /fixture \
    && cp /source/terraform/edge-gateway/main.tf /source/terraform/edge-gateway/.terraform.lock.hcl /fixture/ \
    && terraform -chdir=/fixture providers mirror -platform=linux_amd64 /providers

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
COPY --from=providers /providers /opt/terraform/providers
COPY --from=providers /fixture /opt/terraform/fixture
COPY gaas/terraform.rc /opt/terraform/terraform.rc
COPY gaas /opt/gaas/gaas
COPY charts/pz-server /opt/gaas/charts/pz-server
ENV PYTHONPATH=/opt/gaas
ENV TF_CLI_CONFIG_FILE=/opt/terraform/terraform.rc
USER 1000:1000
WORKDIR /opt/gaas
ENTRYPOINT ["python3", "-m", "gaas.controller"]
