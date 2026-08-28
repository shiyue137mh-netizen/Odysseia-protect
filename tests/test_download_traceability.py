from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.traceability_service import PersonalizedCard
from src.ui import download_ui


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = 777
    interaction.filesize_limit = 10_000_000
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _resource(*, trace_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=9,
        trace_enabled=trace_enabled,
        filename="card.png",
        password=None,
        download_count=0,
        thread=SimpleNamespace(
            public_thread_id=123,
            warehouse_thread_id=456,
        ),
    )


@pytest.mark.asyncio
async def test_trace_resource_requires_explicit_confirmation(monkeypatch):
    interaction = _interaction()
    fetch_source = AsyncMock()
    monkeypatch.setattr(
        download_ui, "_load_resource", AsyncMock(return_value=_resource(trace_enabled=True))
    )
    monkeypatch.setattr(download_ui, "_fetch_source_attachment", fetch_source)

    await download_ui.deliver_resource(interaction, resource_id=9)

    fetch_source.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert "先确认" in interaction.response.send_message.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_trace_resource_generates_ephemeral_personalized_attachment(monkeypatch):
    interaction = _interaction()
    resource = _resource(trace_enabled=True)
    attachment = MagicMock()
    attachment.read = AsyncMock(return_value=b"source")
    interaction.client.traceability_service.personalize = AsyncMock(
        return_value=PersonalizedCard(b"personalized", "card.personalized.png")
    )
    increment = AsyncMock()
    monkeypatch.setattr(download_ui, "_load_resource", AsyncMock(return_value=resource))
    monkeypatch.setattr(
        download_ui, "_fetch_source_attachment", AsyncMock(return_value=attachment)
    )
    monkeypatch.setattr(download_ui, "_increment_download_count", increment)

    await download_ui.deliver_resource(
        interaction, resource_id=9, trace_confirmed=True
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    interaction.client.traceability_service.personalize.assert_awaited_once_with(
        b"source",
        filename="card.png",
        user_id=777,
        public_thread_id=123,
        resource_id=9,
    )
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    increment.assert_awaited_once_with(9)


@pytest.mark.asyncio
async def test_untraced_resource_keeps_copyable_dynamic_url(monkeypatch):
    interaction = _interaction()
    resource = _resource(trace_enabled=False)
    attachment = SimpleNamespace(url="https://cdn.discordapp.com/card.png?token=fresh")
    increment = AsyncMock()
    monkeypatch.setattr(download_ui, "_load_resource", AsyncMock(return_value=resource))
    monkeypatch.setattr(
        download_ui, "_fetch_source_attachment", AsyncMock(return_value=attachment)
    )
    monkeypatch.setattr(download_ui, "_increment_download_count", increment)

    await download_ui.deliver_resource(interaction, resource_id=9)

    sent_embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "打开下载链接" in sent_embed.description
    assert attachment.url in sent_embed.fields[0].value
    increment.assert_awaited_once_with(9)


@pytest.mark.asyncio
async def test_wrong_password_does_not_generate_or_fetch(monkeypatch):
    interaction = _interaction()
    resource = _resource(trace_enabled=True)
    resource.password = "correct"
    deliver = AsyncMock()
    monkeypatch.setattr(download_ui, "_load_resource", AsyncMock(return_value=resource))
    monkeypatch.setattr(download_ui, "deliver_resource", deliver)
    modal = download_ui.PasswordModal(resource_id=9, trace_confirmed=True)
    modal.password_input._value = "wrong"

    await modal.on_submit(interaction)

    deliver.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert (
        interaction.response.send_message.await_args.kwargs["embed"].title
        == "❌ 密码错误"
    )
