FROM debian:bookworm-slim

ARG STEAMCMD_URL=https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        lib32gcc-s1 \
        lib32stdc++6 \
        locales \
        procps \
        python3 \
        restic \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && useradd --create-home --home-dir /home/pz --uid 1000 --shell /bin/bash pz \
    && install -d -o pz -g pz /opt/steamcmd /data \
    && curl --fail --location --silent --show-error "${STEAMCMD_URL}" \
      | tar --extract --gzip --directory /opt/steamcmd \
    && chown -R pz:pz /opt/steamcmd

COPY --chmod=0755 scripts/install-server.sh /usr/local/bin/install-server
COPY --chmod=0755 scripts/start-server.sh /usr/local/bin/start-server
COPY --chmod=0755 scripts/backup.sh /usr/local/bin/backup-server
COPY --chmod=0755 scripts/restore.sh /usr/local/bin/restore-server
COPY --chmod=0755 scripts/pz-rcon.py /usr/local/bin/pz-rcon

ENV HOME=/data \
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    SERVER_DIR=/data/server \
    STEAMCMD_DIR=/opt/steamcmd

USER 1000:1000
WORKDIR /data

EXPOSE 16261/udp 16262/udp 27015/tcp

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/start-server"]
