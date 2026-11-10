"""CLI sub-package for ``hotelrate-mcp setup`` and ``hotelrate-mcp doctor``.

Lazy-imported by the main server module only when a CLI subcommand is invoked,
so there is zero import overhead for the normal MCP server startup path.
"""

from __future__ import annotations

import argparse


def run_setup(args: argparse.Namespace) -> None:
    """Entry point for ``hotelrate-mcp setup``."""
    from hotelrate.apps.mcp.cli.setup import SetupWizard

    wizard = SetupWizard(args)
    wizard.run()


def run_doctor(args: argparse.Namespace) -> None:
    """Entry point for ``hotelrate-mcp doctor``."""
    from hotelrate.apps.mcp.cli.doctor import DoctorCommand

    cmd = DoctorCommand(args)
    raise SystemExit(cmd.run())
