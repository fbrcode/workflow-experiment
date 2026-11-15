# Kestra OSS 2.0.10 (the current LTS) with only the plugins this experiment uses.
# The full kestra/kestra:v2.0.10 image bundles every plugin (about 5 GB compressed); this one
# starts from the 243 MB no-plugins image. Plugin versions are the newest that core 2.0.10
# accepts, as resolved by `kestra plugins install <plugin>:LATEST` on 2026-11-14.
# plugin-telegram isn't used: TelegramSend returns no response (SND-4 needs it) and can't set
# disable_web_page_preview, so both Telegram calls use the core HTTP Request task.
FROM kestra/kestra:v2.0.10-no-plugins

RUN /app/kestra plugins install --locally \
      io.kestra.plugin:plugin-jdbc-postgres:3.1.1 \
      io.kestra.plugin:plugin-script-python:1.18.8 \
      io.kestra.plugin:plugin-ai:2.9.0 \
    > /tmp/plugins-install.log 2>&1 \
    || (cat /tmp/plugins-install.log; exit 1)

COPY --chmod=0755 secrets-entrypoint.sh /usr/local/bin/wfx-secrets-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/wfx-secrets-entrypoint.sh"]
CMD ["server", "standalone"]
