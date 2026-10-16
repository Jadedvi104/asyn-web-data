"""Translate the example JSON configuration to the documented CLI flags."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("crawl-config.json")
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    command = [sys.executable, "-m", "webcollect", "--db", config["db"], "crawl"]
    for seed in config["seeds"]:
        command.extend(["--seed", seed])
    for domain in config["allow_domains"]:
        command.extend(["--allow-domain", domain])
    options = (
        "depth",
        "concurrency",
        "per_host_concurrency",
        "rps",
        "max_pages",
        "queue_size",
        "timeout",
        "max_response_bytes",
        "max_attempts",
        "user_agent",
    )
    for option in options:
        if option in config:
            command.extend(["--" + option.replace("_", "-"), str(config[option])])
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
