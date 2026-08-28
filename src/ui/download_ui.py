# -*- coding: utf-8 -*-
"""下载资源选择、密码校验与动态溯源告知。"""

from __future__ import annotations

import io
import logging
from typing import Sequence

import discord

from src.database.database import AsyncSessionLocal
from src.database.models import Resource, UploadMode
from src.database.repositories.resource import ResourceRepository
from src.services.traceability_service import TraceabilityUnavailableError
from src.traceability.watermark import WatermarkError

logger = logging.getLogger(__name__)


async def _load_resource(resource_id: int) -> Resource | None:
    async with AsyncSessionLocal() as session:
        return await ResourceRepository().get_with_thread(session, id=resource_id)


async def _increment_download_count(resource_id: int) -> None:
    try:
        async with AsyncSessionLocal() as session:
            resource = await ResourceRepository().get(session, resource_id)
            if resource is None:
                return
            resource.download_count += 1
            await session.commit()
    except Exception as exc:
        logger.warning("资源 %s 的下载计数更新失败", resource_id, exc_info=exc)


async def _send_ephemeral(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content=content, embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(
            content=content, embed=embed, ephemeral=True
        )


async def _fetch_source_attachment(
    interaction: discord.Interaction, resource: Resource
) -> discord.Attachment:
    channel_id = (
        resource.thread.warehouse_thread_id or resource.thread.public_thread_id
    )
    source_channel = await interaction.client.fetch_channel(channel_id)
    if not isinstance(source_channel, (discord.TextChannel, discord.Thread)):
        raise ValueError("资源所在频道不支持读取消息。")
    source_message = await source_channel.fetch_message(resource.source_message_id)
    if not source_message.attachments:
        raise ValueError("源消息中没有附件。")
    return source_message.attachments[0]


async def deliver_resource(
    interaction: discord.Interaction,
    *,
    resource_id: int,
    trace_confirmed: bool = False,
) -> None:
    resource = await _load_resource(resource_id)
    if resource is None:
        await _send_ephemeral(
            interaction, content="❌ 找不到所选资源，它可能已被删除。"
        )
        return
    if resource.trace_enabled and not trace_confirmed:
        await _send_ephemeral(interaction, content="❌ 请先确认动态溯源告知。")
        return

    if resource.trace_enabled and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        attachment = await _fetch_source_attachment(interaction, resource)
        if not resource.trace_enabled:
            fresh_url = attachment.url
            embed = discord.Embed(
                title="🔗 下载与导入",
                description=f"[打开下载链接]({fresh_url})",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="📋 SillyTavern 快速导入链接",
                value=f"`{fresh_url}`",
                inline=False,
            )
            await _send_ephemeral(interaction, embed=embed)
        else:
            source_data = await attachment.read()
            traceability_service = getattr(
                interaction.client, "traceability_service", None
            )
            if traceability_service is None:
                raise TraceabilityUnavailableError("Bot 未加载动态溯源服务。")
            personalized = await traceability_service.personalize(
                source_data,
                filename=resource.filename or attachment.filename,
                user_id=interaction.user.id,
                public_thread_id=resource.thread.public_thread_id,
                resource_id=resource.id,
            )
            filesize_limit = getattr(interaction, "filesize_limit", None)
            if filesize_limit and len(personalized.data) > filesize_limit:
                raise ValueError("个性化文件超过当前 Discord 交互允许的附件大小。")

            # ponytail: 首轮生产试验使用 Ephemeral 附件交付；验证稳定后，
            # 将这里替换为 VPS 短时签名 URL，并保留当前生成服务不变。
            await interaction.followup.send(
                content=(
                    "✅ 已生成仅供本次下载的个性化角色卡。\n"
                    "溯源标记仅在发现外部泄露样本后用于被动核验，Bot 不读取本地文件。"
                ),
                file=discord.File(
                    io.BytesIO(personalized.data), filename=personalized.filename
                ),
                ephemeral=True,
            )
        await _increment_download_count(resource.id)
    except (WatermarkError, TraceabilityUnavailableError, ValueError) as exc:
        logger.warning("资源 %s 的下载生成失败: %s", resource.id, exc)
        await _send_ephemeral(interaction, content=f"❌ 无法生成下载文件：{exc}")
    except (discord.HTTPException, discord.Forbidden, discord.NotFound) as exc:
        logger.error(
            "资源 %s 的 Discord 文件读取或发送失败", resource.id, exc_info=exc
        )
        await _send_ephemeral(
            interaction,
            content="❌ 获取或发送资源失败，请稍后重试并联系管理员。",
        )
    except Exception as exc:
        logger.error("资源 %s 的下载流程发生未知错误", resource.id, exc_info=exc)
        await _send_ephemeral(
            interaction, content="❌ 下载流程发生内部错误，请稍后重试。"
        )


class ResourceSelectView(discord.ui.View):
    """一个包含版本选择下拉菜单的交互式视图。"""

    def __init__(self, resources: Sequence[Resource]):
        super().__init__(timeout=None)
        self.add_item(self.ResourceSelect(resources))

    class ResourceSelect(discord.ui.Select):
        def __init__(self, resources: Sequence[Resource]):
            options = []
            for resource in resources[:25]:
                mode_icon = "🔒" if resource.upload_mode == UploadMode.SECURE else "📄"
                trace_icon = " · 溯源" if resource.trace_enabled else ""
                filename = resource.filename or "N/A"
                options.append(
                    discord.SelectOption(
                        label=(
                            f"{mode_icon} 版本: {resource.version_info or '未命名'}"
                        ),
                        description=f"文件名: {filename}{trace_icon}"[:100],
                        value=str(resource.id),
                    )
                )

            if not options:
                options.append(
                    discord.SelectOption(
                        label="没有找到任何受保护的资源",
                        value="disabled",
                        default=True,
                    )
                )

            super().__init__(
                placeholder="请选择一个受保护的版本进行下载...",
                min_values=1,
                max_values=1,
                options=options,
                disabled=options[0].value == "disabled",
            )

        async def callback(self, interaction: discord.Interaction):
            resource = await _load_resource(int(self.values[0]))
            if resource is None:
                await interaction.response.send_message(
                    "❌ 找不到所选资源，它可能已被删除。", ephemeral=True
                )
                return

            if resource.trace_enabled:
                embed = discord.Embed(
                    title="🛡️ 动态溯源告知",
                    description=(
                        "作者已为这个作品开启动态溯源。下载时，Bot 会在副本中写入"
                        "经过加密、并绑定本作品的溯源凭证。\n\n"
                        "凭证不包含明文 Discord ID；系统不会监控或读取你本地的文件。"
                        "只有发现作品已在外部泄露后，管理组才会对具体样本进行受控核验。\n\n"
                        "如果你不接受，请点击取消并不要下载。"
                    ),
                    color=discord.Color.orange(),
                )
                await interaction.response.send_message(
                    embed=embed,
                    view=TraceConsentView(
                        resource_id=resource.id,
                        user_id=interaction.user.id,
                        password_required=bool(resource.password),
                    ),
                    ephemeral=True,
                )
                return

            if resource.password:
                await interaction.response.send_modal(
                    PasswordModal(resource_id=resource.id, trace_confirmed=False)
                )
                return
            await deliver_resource(interaction, resource_id=resource.id)


class TraceConsentView(discord.ui.View):
    def __init__(
        self, *, resource_id: int, user_id: int, password_required: bool
    ) -> None:
        super().__init__(timeout=180)
        self.resource_id = resource_id
        self.user_id = user_id
        self.password_required = password_required

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "❌ 这个确认面板不属于你。", ephemeral=True
        )
        return False

    @discord.ui.button(label="同意并继续", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.password_required:
            await interaction.response.send_modal(
                PasswordModal(resource_id=self.resource_id, trace_confirmed=True)
            )
            return
        await deliver_resource(
            interaction,
            resource_id=self.resource_id,
            trace_confirmed=True,
        )
        self.stop()

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content="已取消下载。", embed=None, view=None
        )
        self.stop()


class PasswordModal(discord.ui.Modal, title="请输入下载密码"):
    def __init__(self, *, resource_id: int, trace_confirmed: bool):
        super().__init__(timeout=180)
        self.resource_id = resource_id
        self.trace_confirmed = trace_confirmed
        self.password_input = discord.ui.TextInput(
            label="密码",
            style=discord.TextStyle.short,
            required=True,
            min_length=1,
            placeholder="请输入该资源版本对应的下载密码",
        )
        self.add_item(self.password_input)

    async def on_submit(self, interaction: discord.Interaction):
        resource = await _load_resource(self.resource_id)
        if resource is None:
            await interaction.response.send_message(
                "❌ 找不到所选资源，它可能已被删除。", ephemeral=True
            )
            return
        if self.password_input.value != resource.password:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ 密码错误",
                    description="你输入的密码不正确，请重试。",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        await deliver_resource(
            interaction,
            resource_id=resource.id,
            trace_confirmed=self.trace_confirmed,
        )
