"""Adapters: HostOps port + LocalHostOps + HermesCliAdapter + ProfileFsAdapter +
status_decider + LogTailer + parsers."""

from app.adapters.hermes_cli import HermesCliAdapter, HermesCliError
from app.adapters.hostops import CliResult, HostOps, ProcessInfo
from app.adapters.local_hostops import LocalHostOps
from app.adapters.log_tail import LogTailer
from app.adapters.parsers import (
    ApprovedUser,
    GatewayPidFile,
    PairingListOutput,
    PendingPairing,
    ProfileShow,
    ProfileSummary,
    classify_create_error,
    parse_gateway_pid_file,
    parse_pairing_list,
    parse_profile_list,
    parse_profile_show,
)
from app.adapters.profile_fs import ProfileFsAdapter, validate_bot_name
from app.adapters.status_decider import BotStatus, decide_bot_status

__all__ = [
    "ApprovedUser",
    "BotStatus",
    "CliResult",
    "GatewayPidFile",
    "HermesCliAdapter",
    "HermesCliError",
    "HostOps",
    "LocalHostOps",
    "LogTailer",
    "PairingListOutput",
    "PendingPairing",
    "ProcessInfo",
    "ProfileFsAdapter",
    "ProfileShow",
    "ProfileSummary",
    "classify_create_error",
    "decide_bot_status",
    "parse_gateway_pid_file",
    "parse_pairing_list",
    "parse_profile_list",
    "parse_profile_show",
    "validate_bot_name",
]
