from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.services.traceability_service import TraceabilityService
from src.services.upload_service import UploadService


CARD_PATH = Path(__file__).parents[1] / "Watermark" / "default_Seraphina.png"


@pytest.mark.asyncio
async def test_secure_upload_persists_author_trace_opt_in():
    bot = MagicMock()
    bot.traceability_service = TraceabilityService(
        key=bytes(range(32)), key_id="test-v1"
    )
    resource_repo = MagicMock()
    resource_repo.create = AsyncMock()
    service = UploadService(bot, resource_repo, MagicMock(), MagicMock())
    thread_model = SimpleNamespace(id=5)
    warehouse_thread = MagicMock()
    warehouse_thread.id = 456
    warehouse_thread.send = AsyncMock(return_value=SimpleNamespace(id=999))
    service._get_or_create_thread = AsyncMock(return_value=thread_model)
    service._find_or_create_warehouse_thread = AsyncMock(
        return_value=warehouse_thread
    )
    attachment = MagicMock()
    attachment.filename = "card.png"
    attachment.read = AsyncMock(return_value=CARD_PATH.read_bytes())
    attachment.to_file = AsyncMock()
    interaction = MagicMock()
    interaction.channel = MagicMock(spec=discord.Thread)
    session = MagicMock()

    result = await service._handle_secure_upload(
        session,
        interaction=interaction,
        file=attachment,
        version_info="v1",
        password=None,
        trace_enabled=True,
    )

    assert "已开启动态溯源" in result
    attachment.to_file.assert_not_awaited()
    created = resource_repo.create.await_args.kwargs["obj_in"]
    assert created.trace_enabled is True
    assert created.source_message_id == 999
