#!/usr/bin/env bash
# Runs on the EC2 instance (sent over SSH by .github/workflows/deploy.yml).
# Pulls the new image and replaces the running bot container. It only ever
# touches the "rent-receipt-bot" container and this app's own images, so
# other containers on the server are left alone.
#
# Expects these variables to be set before the script runs:
#   IMAGE             image name with tag, e.g. ghcr.io/owner/repo/rent-receipt-bot:sha-abc1234
#   IMAGE_REPOSITORY  GitHub repository the image is built from, e.g. owner/repo
#   GHCR_USER         GitHub user name for pulling the image
#   GHCR_TOKEN        token for pulling the image (expires when the workflow run ends)
set -euo pipefail

CONTAINER=rent-receipt-bot
CONFIG_DIR=/etc/rent-receipt-bot
DATA_DIR=/var/lib/rent-receipt-bot

for required in "$CONFIG_DIR/config.toml" "$CONFIG_DIR/secrets.env"; do
    if [[ ! -f "$required" ]]; then
        echo "Missing $required. Do the one-time server setup in the README first." >&2
        exit 1
    fi
done
if [[ ! -d "$DATA_DIR" ]]; then
    echo "Missing $DATA_DIR. Do the one-time server setup in the README first." >&2
    exit 1
fi

echo "$GHCR_TOKEN" | docker login ghcr.io --username "$GHCR_USER" --password-stdin
docker pull "$IMAGE"
docker logout ghcr.io

docker rm --force "$CONTAINER" 2>/dev/null || true
# No ports are published: the bot only makes outgoing connections.
docker run --detach \
    --name "$CONTAINER" \
    --restart unless-stopped \
    --env-file "$CONFIG_DIR/secrets.env" \
    --volume "$CONFIG_DIR/config.toml:$CONFIG_DIR/config.toml:ro" \
    --volume "$DATA_DIR:$DATA_DIR" \
    --read-only \
    --tmpfs /tmp \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --memory 256m \
    --cpus 0.25 \
    --log-opt max-size=10m \
    --log-opt max-file=3 \
    "$IMAGE"

# Give it a moment, then make sure it didn't exit straight away.
sleep 10
if [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER")" != "true" ]]; then
    echo "The bot stopped right after starting. Its last log lines:" >&2
    docker logs --tail 50 "$CONTAINER" >&2
    exit 1
fi
echo "Deployed $IMAGE"

# Remove this app's unused images older than a week (matched by the source
# label the build adds), so old versions don't fill the disk.
docker image prune --force --all \
    --filter "label=org.opencontainers.image.source=https://github.com/$IMAGE_REPOSITORY" \
    --filter "until=168h"
