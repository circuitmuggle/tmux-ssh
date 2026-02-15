"""Command-line interface for tmux_ssh."""

from __future__ import annotations

import argparse
import json
import os
import sys

from tmux_ssh.client import (
    EXIT_COMPLETED,
    Config,
    TmuxSSHClient,
    error,
    warning,
)

# Config file for persisting host/user
CONFIG_FILE = os.path.expanduser("~/.tmux_ssh_config")


def load_saved_config() -> dict[str, str | bool | int]:
    """Load saved host/user/settings from config file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                data: dict[str, str | bool | int] = json.load(f)
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(
    host: str,
    user: str,
    last_server: str | None = None,
    auto_new_session: bool = True,
    port: int = 22,
) -> None:
    """Save host/user/last_server/settings to config file for future use."""
    config: dict[str, str | bool | int] = {
        "host": host,
        "user": user,
        "port": port,
        "auto_new_session": auto_new_session,
    }
    if last_server:
        config["last_server"] = last_server
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f)
    except OSError:
        pass  # Silently fail if we can't write


def parse_connection_target(arg: str) -> tuple[str | None, str | None, int | None]:
    """
    Parse SSH-style connection target.

    Formats:
        user@host      -> (user, host, None)
        user@host:port -> (user, host, port)
        host           -> (None, host, None)
        host:port      -> (None, host, port)

    Returns:
        (user, host, port) - any can be None

    Raises:
        ValueError: If port is not a valid integer or out of range
    """
    user = None
    port = None

    # Extract user if present
    if "@" in arg:
        user, host_part = arg.split("@", 1)
    else:
        host_part = arg

    # Extract port if present
    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        if not port_str.isdigit():
            raise ValueError(f"Invalid port: '{port_str}'")
        port = int(port_str)
        if port < 1 or port > 65535:
            raise ValueError(f"Port out of range: {port}")
    else:
        host = host_part

    return (user if user else None, host if host else None, port)


def main(args: list[str] | None = None) -> int:
    """Main entry point for the CLI."""
    saved = load_saved_config()

    parser = argparse.ArgumentParser(
        description="Run remote commands in a tmux session via SSH (batch mode).",
        epilog=(
            "Command quoting: Use quotes for commands with shell operators "
            "(&&, ||, |, >, etc.), variables ($VAR), wildcards (*), or flags (-la). "
            'Example: tmux-ssh user@host "cmd1 && cmd2"'
        ),
    )
    parser.add_argument("-H", "--host", default=None, help="Remote hostname")
    parser.add_argument("-U", "--user", default=None, help="Remote username")
    parser.add_argument(
        "-p", "--port", type=int, default=None, help="SSH port (default: 22)"
    )
    parser.add_argument(
        "-C", "--clear", action="store_true", help="Clear stored credentials"
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=None,
        help="Max seconds to stream output (default: unlimited)",
    )
    parser.add_argument(
        "-i",
        "--idle-timeout",
        type=int,
        default=3600,
        help="Exit if no output for N seconds (default: 3600)",
    )
    parser.add_argument(
        "-n",
        "--new",
        action="store_true",
        help="Create a new unique tmux session (for concurrent commands)",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force execution, kill any running command in session",
    )
    parser.add_argument(
        "-a",
        "--attach",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION",
        help="Attach to session and resume streaming (auto-detect if no session specified)",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List all running commands/sessions",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Clean up idle task_* sessions (keeps remote_task)",
    )
    parser.add_argument(
        "-k",
        "--kill",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION",
        help="Kill running command in session (auto-detect if no session specified)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (for --kill)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        dest="auto",
        default=None,
        help="Auto-create new session if command already running (default: true)",
    )
    parser.add_argument(
        "--no-auto",
        action="store_false",
        dest="auto",
        help="Disable auto-create, block if command already running",
    )
    parser.add_argument(
        "positional",
        nargs="*",
        help='[user@host[:port]] ["command"] - quote commands with special chars',
    )

    parsed_args = parser.parse_args(args)

    # Parse positional arguments to extract connection target
    positionals = parsed_args.positional
    target_user = None
    target_host = None
    target_port = None
    command_args: list[str] = []

    if positionals:
        first_arg = positionals[0]
        # Check if first arg looks like a connection target
        # (contains @ or is a hostname-like string without spaces/slashes)
        if "@" in first_arg or (
            not first_arg.startswith("-")
            and "/" not in first_arg
            and " " not in first_arg
            and "." in first_arg  # Likely a hostname
        ):
            try:
                target_user, target_host, target_port = parse_connection_target(
                    first_arg
                )
                command_args = positionals[1:]
            except ValueError as e:
                print(error(str(e)))
                return 1
        else:
            # No connection target, all args are command
            command_args = positionals

    # Conflict detection with warnings
    if target_host and parsed_args.host:
        print(
            warning(
                f"Both '{positionals[0]}' and -H '{parsed_args.host}' "
                f"provided. Using '{target_host}'."
            )
        )
    if target_user and parsed_args.user:
        print(
            warning(
                f"Both '{positionals[0]}' and -U '{parsed_args.user}' "
                f"provided. Using '{target_user}'."
            )
        )
    if target_port and parsed_args.port:
        print(
            warning(
                f"Both '{positionals[0]}' and -p '{parsed_args.port}' "
                f"provided. Using port {target_port}."
            )
        )

    # Resolve host: positional > CLI flag > saved config > prompt
    saved_host = saved.get("host")
    host = (
        target_host
        or parsed_args.host
        or (saved_host if isinstance(saved_host, str) else None)
    )
    if not host:
        host = input("[?] Enter remote hostname: ").strip()
        if not host:
            print(error("Hostname is required."))
            return 1

    # Resolve user: positional > CLI flag > saved config > prompt
    saved_user = saved.get("user")
    user = (
        target_user
        or parsed_args.user
        or (saved_user if isinstance(saved_user, str) else None)
    )
    if not user:
        user = input("[?] Enter remote username: ").strip()
        if not user:
            print(error("Username is required."))
            return 1

    # Resolve port: positional > CLI flag > saved config > default (22)
    saved_port = saved.get("port", 22)
    port = (
        target_port
        or parsed_args.port
        or (saved_port if isinstance(saved_port, int) else 22)
    )

    # Resolve auto: CLI arg > saved config > default (True)
    if parsed_args.auto is not None:
        auto = parsed_args.auto
    else:
        saved_auto = saved.get("auto_new_session", True)
        auto = saved_auto if isinstance(saved_auto, bool) else True

    # Get last_server from saved config
    saved_last_server = saved.get("last_server")
    last_server: str | None = (
        saved_last_server if isinstance(saved_last_server, str) else None
    )

    # Save for future use (without last_server yet, will update after connection)
    save_config(host, user, last_server, auto, port)

    config = Config(hostname=host, username=user, port=port)
    client = TmuxSSHClient(config, last_server=last_server)

    if parsed_args.clear:
        client.clear_credentials()
        return EXIT_COMPLETED

    if parsed_args.list:
        result = client.list_running()
        # Save current server for next time
        if client.current_server:
            save_config(host, user, client.current_server, auto, port)
        return result

    if parsed_args.cleanup:
        result = client.cleanup()
        if client.current_server:
            save_config(host, user, client.current_server, auto, port)
        return result

    if parsed_args.attach is not None:
        # parsed_args.attach is "" if --attach with no value, or the session name
        session = parsed_args.attach if parsed_args.attach else None
        result = client.attach(session)
        if client.current_server:
            save_config(host, user, client.current_server, auto, port)
        return result

    if parsed_args.kill is not None:
        # parsed_args.kill is "" if --kill with no value, or the session name
        session = parsed_args.kill if parsed_args.kill else None
        result = client.kill(session, force=parsed_args.yes)
        if client.current_server:
            save_config(host, user, client.current_server, auto, port)
        return result

    user_cmd = " ".join(command_args)
    if not user_cmd:
        user_cmd = input("[?] Enter the command to run on server: ").strip()

    result = client.execute(
        user_cmd,
        timeout=parsed_args.timeout,
        idle_timeout=parsed_args.idle_timeout,
        new_session=parsed_args.new,
        force=parsed_args.force,
        auto=auto,
    )
    if client.current_server:
        save_config(host, user, client.current_server, auto, port)
    return result


if __name__ == "__main__":
    sys.exit(main())
