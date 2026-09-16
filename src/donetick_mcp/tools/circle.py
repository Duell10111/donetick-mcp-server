"""Circle member and user profile tools."""

import asyncio
import logging

from mcp.server.mcpserver import Context, MCPServer

from ..models import AUTH_PROVIDER_NAMES, NOTIFICATION_PLATFORM_NAMES
from ._common import READ_ONLY, get_client, handle_errors

logger = logging.getLogger(__name__)


def _megabytes(value: int) -> str:
    return f"{value / (1024 * 1024):.2f} MB"


def register(mcp: MCPServer) -> None:
    """Register circle and user tools."""

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def get_circle_members(ctx: Context) -> str:
        """Get all members in the circle (household/team).

        Returns user information including user IDs, usernames, display names, roles (admin/member),
        active status, and points. Use this to see who you can assign chores to.
        """
        members = await get_client(ctx).get_circle_members()

        member_list = []
        for member in members:
            role_emoji = "👑" if member.role == "admin" else "👤"
            status_emoji = "✅" if member.isActive else "❌"
            display_name = member.displayName or "(no display name)"
            member_list.append(
                f"{role_emoji} {status_emoji} {member.username}\n"
                f"  User ID: {member.userId}\n"
                f"  Display Name: {display_name}\n"
                f"  Role: {member.role}\n"
                f"  Points: {member.points} (Redeemed: {member.pointsRedeemed})"
            )

        return f"Found {len(members)} member(s) in your circle:\n\n" + "\n\n".join(member_list)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def list_circle_users(ctx: Context) -> str:
        """List all user accounts in the circle with account details.

        Returns user IDs, usernames, display names, email addresses, account type (regular/child),
        timezone and whether the account is disabled. Roles and points are not part of Donetick's
        user data; use get_circle_members for those.
        """
        users = await get_client(ctx).list_users()

        user_list = []
        for user in users:
            status_emoji = "❌" if user.disabled else "✅"
            lines = [
                f"{status_emoji} {user.username or '(no username)'}",
                f"  User ID: {user.id}",
                f"  Display Name: {user.displayName or '(no display name)'}",
                f"  Email: {user.email or '(no email)'}",
                f"  Account: {'child account' if user.userType == 1 else 'regular'}"
                f"{' (disabled)' if user.disabled else ''}",
            ]
            if user.timezone:
                lines.append(f"  Timezone: {user.timezone}")
            user_list.append("\n".join(lines))

        return (
            f"Found {len(users)} user(s) in your circle:\n\n"
            + "\n\n".join(user_list)
            + "\n\nRoles and points: see get_circle_members."
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def get_user_profile(ctx: Context) -> str:
        """Get the current user's profile.

        Returns account details (username, display name, email, account type, login provider,
        timezone, MFA), the circle role and points, the circle's storage usage and the configured
        notification target.
        """
        client = get_client(ctx)
        profile = await client.get_user_profile()

        # Role/points and storage come from other endpoints; show "unavailable" if they fail
        members, storage = await asyncio.gather(
            client.get_circle_members(), client.get_storage_usage(), return_exceptions=True
        )
        if isinstance(members, Exception):
            logger.warning(f"Could not fetch circle members for profile: {members}")
            member = None
        else:
            member = next((m for m in members if m.userId == profile.id), None)
        if isinstance(storage, Exception):
            logger.warning(f"Could not fetch storage usage for profile: {storage}")
            storage = None

        account_type = "child account" if profile.userType == 1 else "regular"
        provider = AUTH_PROVIDER_NAMES.get(profile.provider, "unknown")
        lines = [
            f"👤 User Profile for {profile.username or profile.displayName}",
            "",
            "📝 Basic Information:",
            f"  User ID: {profile.id}",
            f"  Username: {profile.username or '(not set)'}",
            f"  Display Name: {profile.displayName or '(not set)'}",
            f"  Email: {profile.email or '(not set)'}",
            f"  Account: {account_type}, login via {provider}",
            f"  Timezone: {profile.timezone or '(not set)'}",
            f"  MFA: {'enabled' if profile.mfaEnabled else 'disabled'}",
            f"  Status: {'❌ disabled' if profile.disabled else '✅ active'}",
            "",
            "🏆 Circle:",
            f"  Circle ID: {profile.circleId}",
        ]
        if member:
            lines += [
                f"  Role: {member.role}",
                f"  Points Earned: {member.points}",
                f"  Points Redeemed: {member.pointsRedeemed}",
                f"  Net Points: {(member.points or 0) - (member.pointsRedeemed or 0)}",
            ]
        else:
            lines.append("  Role and points: unavailable")

        lines += ["", "💾 Storage (whole circle):"]
        if storage is None:
            lines.append("  unavailable")
        else:
            lines.append(f"  Used: {_megabytes(storage['used'])}")
            if storage["total"]:
                lines += [
                    f"  Limit: {_megabytes(storage['total'])}",
                    f"  Available: {_megabytes(max(storage['total'] - storage['used'], 0))}",
                ]
            else:
                lines.append("  Limit: not configured")

        target = profile.notificationTarget or {}
        platform = NOTIFICATION_PLATFORM_NAMES.get(target.get("type"), "none")
        lines += [
            "",
            "🔔 Notifications:",
            f"  Notification target: {platform}",
            # The URL itself is not shown, webhook URLs often contain secrets
            f"  Circle webhook: {'configured' if profile.webhookURL else 'not configured'}",
        ]

        if profile.subscription:
            expiration = f" (until {profile.expiration})" if profile.expiration else ""
            lines += ["", f"⭐ Subscription: {profile.subscription}{expiration}"]

        lines += [
            "",
            "🕐 Account Dates:",
            f"  Created: {profile.createdAt or 'Unknown'}",
            f"  Updated: {profile.updatedAt or 'Unknown'}",
        ]
        return "\n".join(lines)
