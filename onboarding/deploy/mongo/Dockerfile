# Slim MongoDB 8.0 (the official mongo image has no slim variant): mongod and mongosh on debian:bookworm-slim, from
# MongoDB's own apt repository, without the database tools, entrypoint scripts or gosu. Runs as uid 999 like the
# official image, so existing data volumes keep working. mongosh stays: the readiness probe, the replica-set init job,
# dev.sh and leak-scan.sh use it.
FROM debian:bookworm-slim
ARG MONGO_SERIES=8.0
RUN set -eux; \
    groupadd -r -g 999 mongodb; \
    useradd -r -u 999 -g mongodb -d /data/db -s /usr/sbin/nologin mongodb; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -fsSL "https://pgp.mongodb.com/server-${MONGO_SERIES}.asc" | gpg --dearmor -o /usr/share/keyrings/mongodb.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/mongodb.gpg] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/${MONGO_SERIES} main" \
      >/etc/apt/sources.list.d/mongodb.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends mongodb-org-server mongodb-mongosh-shared-openssl3; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/mongodb.list /usr/share/keyrings/mongodb.gpg; \
    mkdir -p /data/db /data/configdb; \
    chown -R mongodb:mongodb /data
USER 999
VOLUME /data/db
EXPOSE 27017
ENTRYPOINT ["mongod"]
