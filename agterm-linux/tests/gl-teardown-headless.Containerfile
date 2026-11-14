# gl_teardown_headless.sh's image; its usage comment says how to build this and the image below.
FROM localhost/agterm-linux-ci:local
RUN apt-get update && apt-get install -y --no-install-recommends sway fonts-dejavu-core procps \
    && rm -rf /var/lib/apt/lists/*
# gl_teardown_headless.sh runs every container with --userns=keep-id, i.e. as the invoking user, never
# as root; the image says so too, and it is a one-shot test runner that no health check could observe.
USER 1000:1000
HEALTHCHECK NONE
