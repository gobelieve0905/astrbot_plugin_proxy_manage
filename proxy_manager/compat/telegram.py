from __future__ import annotations

from typing import Type


def build_proxy_adapter(base: Type, lease):
    """Create a Telegram adapter that configures both Bot API clients."""

    module = __import__(base.__module__, fromlist=["ApplicationBuilder"])
    application_builder = module.ApplicationBuilder

    class ProxyTelegramPlatformAdapter(base):
        _proxy_manager_lease = lease
        _proxy_manager_base = base

        def __init__(self, platform_config, platform_settings, event_queue):
            self._proxy_manager_http_proxy = lease.http_proxy
            super().__init__(platform_config, platform_settings, event_queue)

        def _build_application(self) -> None:
            builder = (
                application_builder()
                .token(self.config["telegram_token"])
                .base_url(self.base_url)
                .base_file_url(self.file_base_url)
            )
            proxy = self._proxy_manager_http_proxy
            if proxy:
                builder = builder.proxy(proxy).get_updates_proxy(proxy)
            self.application = builder.build()
            message_handler = module.TelegramMessageHandler(
                filters=module.filters.ALL,
                callback=self.message_handler,
            )
            self.application.add_handler(message_handler)
            self.client = self.application.bot
            module.logger.debug(f"Telegram base url: {self.client.base_url}")

    ProxyTelegramPlatformAdapter.__name__ = "ProxyManagedTelegramPlatformAdapter"
    ProxyTelegramPlatformAdapter.__qualname__ = "ProxyManagedTelegramPlatformAdapter"
    return ProxyTelegramPlatformAdapter
