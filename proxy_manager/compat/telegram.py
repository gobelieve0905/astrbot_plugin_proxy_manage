from __future__ import annotations

import asyncio
from typing import Type

TERMINATION_COMMAND_TIMEOUT = 3.0
POLLING_RECOVERY_VERSION = 1


def build_polling_request(proxy: str, on_timeout):
    from telegram.error import TimedOut
    from telegram.request import HTTPXRequest

    class RecoverablePollingRequest(HTTPXRequest):
        async def do_request(self, *args, **kwargs):
            try:
                return await super().do_request(*args, **kwargs)
            except TimedOut as exc:
                # PTB 22.8 retries TimedOut without invoking error_callback.
                # Include pool/connect/read timeouts in AstrBot's existing
                # recovery counter so an unusable client can be rebuilt.
                on_timeout(exc)
                raise

    return RecoverablePollingRequest(connection_pool_size=1, proxy=proxy or None)


def build_proxy_adapter(base: Type, lease):
    """Create a Telegram adapter that configures both Bot API clients."""

    module = __import__(base.__module__, fromlist=["ApplicationBuilder"])
    application_builder = module.ApplicationBuilder

    class ProxyTelegramPlatformAdapter(base):
        _proxy_manager_lease = lease
        _proxy_manager_base = base
        _proxy_manager_polling_recovery_version = POLLING_RECOVERY_VERSION

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
                builder = builder.proxy(proxy)
            builder = builder.get_updates_request(
                build_polling_request(proxy, self._on_polling_error)
            )
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
