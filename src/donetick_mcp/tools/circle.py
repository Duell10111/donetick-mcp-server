"""Circle member and user profile tools."""

from mcp.server.mcpserver import Context, MCPServer

from ._common import READ_ONLY, get_client, handle_errors


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
        """List all users in the circle with basic information.

        Returns user IDs, usernames, display names, email addresses, roles, points earned,
        and active status. Similar to get_circle_members but may include additional user details.
        """
        users = await get_client(ctx).list_users()

        user_list = []
        for user in users:
            status_emoji = "✅" if user.isActive else "❌"
            display_name = user.displayName or "(no display name)"
            email = user.email or "(no email)"
            role = user.role or "member"
            user_list.append(
                f"{status_emoji} {user.username}\n"
                f"  User ID: {user.id}\n"
                f"  Display Name: {display_name}\n"
                f"  Email: {email}\n"
                f"  Role: {role}\n"
                f"  Points: {user.points} (Redeemed: {user.pointsRedeemed})"
            )

        return f"Found {len(users)} user(s) in your circle:\n\n" + "\n\n".join(user_list)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def get_user_profile(ctx: Context) -> str:
        """Get the current user's detailed profile information.

        Returns user data including notification preferences, webhook configuration, storage usage,
        points, and account metadata.
        """
        profile = await get_client(ctx).get_user_profile()

        display_name = profile.displayName or "(not set)"
        email = profile.email or "(not set)"
        webhook = profile.webhook or "(not configured)"
        storage_used_mb = (profile.storageUsed or 0) / (1024 * 1024)
        storage_limit_mb = (profile.storageLimit or 0) / (1024 * 1024)

        return (
            f"👤 User Profile for {profile.username}\n\n"
            f"📝 Basic Information:\n"
            f"  User ID: {profile.id}\n"
            f"  Username: {profile.username}\n"
            f"  Display Name: {display_name}\n"
            f"  Email: {email}\n"
            f"  Active: {'✅ Yes' if profile.isActive else '❌ No'}\n\n"
            f"🏆 Gamification:\n"
            f"  Points Earned: {profile.points}\n"
            f"  Points Redeemed: {profile.pointsRedeemed}\n"
            f"  Net Points: {profile.points - profile.pointsRedeemed}\n\n"
            f"💾 Storage:\n"
            f"  Used: {storage_used_mb:.2f} MB\n"
            f"  Limit: {storage_limit_mb:.2f} MB\n"
            f"  Available: {storage_limit_mb - storage_used_mb:.2f} MB\n\n"
            f"🔔 Notifications:\n"
            f"  Webhook: {webhook}\n\n"
            f"🕐 Account Dates:\n"
            f"  Created: {profile.createdAt or 'Unknown'}\n"
            f"  Updated: {profile.updatedAt or 'Unknown'}"
        )
