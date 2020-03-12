import logging
from collections import ChainMap

from discord.ext.commands import Cog, Context, group

from bot.bot import Bot
from bot.cogs.moderation.utils import post_infraction
from bot.constants import Channels, MODERATION_ROLES, Webhooks
from bot.converters import FetchedMember
from bot.decorators import with_role
from .watchchannel import WatchChannel

log = logging.getLogger(__name__)


class BigBrother(WatchChannel, Cog, name="Big Brother"):
    """Monitors users by relaying their messages to a watch channel to assist with moderation."""

    def __init__(self, bot: Bot) -> None:
        super().__init__(
            bot,
            destination=Channels.big_brother_logs,
            webhook_id=Webhooks.big_brother,
            api_endpoint='bot/infractions',
            api_default_params={'active': 'true', 'type': 'watch', 'ordering': '-inserted_at'},
            logger=log
        )

    @group(name='bigbrother', aliases=('bb',), invoke_without_command=True)
    @with_role(*MODERATION_ROLES)
    async def bigbrother_group(self, ctx: Context) -> None:
        """Monitors users by relaying their messages to the Big Brother watch channel."""
        await ctx.invoke(self.bot.get_command("help"), "bigbrother")

    @bigbrother_group.command(name='watched', aliases=('all', 'list'))
    @with_role(*MODERATION_ROLES)
    async def watched_command(self, ctx: Context, update_cache: bool = True) -> None:
        """
        Shows the users that are currently being monitored by Big Brother.

        The optional kwarg `update_cache` can be used to update the user
        cache using the API before listing the users.
        """
        await self.list_watched_users(ctx, update_cache)

    @bigbrother_group.command(name='watch', aliases=('w',))
    @with_role(*MODERATION_ROLES)
    async def watch_command(self, ctx: Context, user: FetchedMember, *, reason: str) -> None:
        """
        Relay messages sent by the given `user` to the `#big-brother` channel.

        A `reason` for adding the user to Big Brother is required and will be displayed
        in the header when relaying messages of this user to the watchchannel.
        """
        await self.apply_watch(ctx, user, reason)

    @bigbrother_group.command(name='unwatch', aliases=('uw',))
    @with_role(*MODERATION_ROLES)
    async def unwatch_command(self, ctx: Context, user: FetchedMember, *, reason: str) -> None:
        """Stop relaying messages by the given `user`."""
        await self.apply_unwatch(ctx, user, reason)

    async def apply_watch(self, ctx: Context, user: FetchedMember, reason: str) -> None:
        """Handles adding a user to the watch list."""
        if user.bot:
            await ctx.send(f":x: I'm sorry {ctx.author}, I'm afraid I can't do that. I only watch humans.")
            return

        if not await self.fetch_user_cache():
            await ctx.send(f":x: Updating the user cache failed, can't watch user {user}")
            return

        if user.id in self.watched_users:
            await ctx.send(f":x: {user} is already being watched.")
            return

        response = await post_infraction(ctx, user, 'watch', reason, hidden=True, active=True)

        if response is not None:
            self.watched_users[user.id] = response
            msg = f":white_check_mark: Messages sent by {user} will now be relayed to Big Brother."

            history = await self.bot.api_client.get(
                self.api_endpoint,
                params={
                    "user__id": str(user.id),
                    "active": "false",
                    'type': 'watch',
                    'ordering': '-inserted_at'
                }
            )

            if len(history) > 1:
                total = f"({len(history) // 2} previous infractions in total)"
                end_reason = history[0]["reason"]
                start_reason = f"Watched: {history[1]['reason']}"
                msg += f"\n\nUser's previous watch reasons {total}:```{start_reason}\n\n{end_reason}```"
        else:
            msg = ":x: Failed to post the infraction: response was empty."

        await ctx.send(msg)

    async def apply_unwatch(self, ctx: Context, user: FetchedMember, reason: str, banned: bool = False) -> None:
        """Handles the actual user removal from the watch list."""
        active_watches = await self.bot.api_client.get(
            self.api_endpoint,
            params=ChainMap(
                self.api_default_params,
                {"user__id": str(user.id)}
            )
        )
        if active_watches:
            log.trace("Active watches for user found.  Attempting to remove.")
            [infraction] = active_watches

            await self.bot.api_client.patch(
                f"{self.api_endpoint}/{infraction['id']}",
                json={'active': False}
            )

            await post_infraction(ctx, user, 'watch', f"Unwatched: {reason}", hidden=True, active=False)

            if not banned:  # Prevents a message being sent to the channel if part of a permanent ban
                log.trace("User is not banned.  Sending message to channel")
                await ctx.send(f":white_check_mark: Messages sent by {user} will no longer be relayed.")

            self._remove_user(user.id)
        else:
            log.trace("No active watches found for user.")
            if not banned:  # Prevents a message being sent to the channel if part of a permanent ban
                log.trace("User is not perma banned. Send the error message.")
                await ctx.send(":x: The specified user is currently not being watched.")
