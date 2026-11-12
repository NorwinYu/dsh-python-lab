from typing import List, Optional

from minidsh.cli import main as cli_main


def main(argv: Optional[List[str]] = None) -> int:
    return cli_main(argv, default_command="serve")
