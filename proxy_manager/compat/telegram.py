from __future__ import annotations

import asyncio
from typing import Type

TERMINATION_COMMAND_TIMEOUT = 3.0


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

        async def _shutdown_application(self, *, delete_commands: bool) -> None:
            if delete_commands and self.enable_command_register:
                try:
                    await asyncio.wait_for(
                        self.client.delete_my_commands(),
                        timeout=TERMINATION_COMMAND_TIMEOUT,
                    )
                except Exception:
                    module.logger.debug("Telegram command cleanup was skipped during shutdown")
            await super()._shutdown_application(delete_commands=False)

    ProxyTelegramPlatformAdapter.__name__ = "ProxyManagedTelegramPlatformAdapter"
    ProxyTelegramPlatformAdapter.__qualname__ = "ProxyManagedTelegramPlatformAdapter"
    return ProxyTelegramPlatformAdapter
