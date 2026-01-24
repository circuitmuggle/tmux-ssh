#!/usr/bin/env python3
"""
Script to output current datetime and timezone every 60 minutes.
Used to test if remote tmux_ssh sessions auto-stop.
"""

import sys
import time
from datetime import datetime


def main():
    print("Starting datetime monitoring loop...")
    print("Output interval: 60 minutes")
    print("-" * 50)
    sys.stdout.flush()

    iteration = 0
    while True:
        iteration += 1
        current_time = datetime.now().astimezone()
        timezone = current_time.tzname()

        print(
            f"[Iteration {iteration}] {current_time.strftime('%Y-%m-%d %H:%M:%S')} {timezone}"
        )
        sys.stdout.flush()

        # Sleep for 60 minutes (3600 seconds)
        time.sleep(3600)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nLoop terminated by user.")
        sys.exit(0)
