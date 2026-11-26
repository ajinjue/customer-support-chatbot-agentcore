#!/usr/bin/env python3
"""Record the chat.py transcripts the rubric asks for.

    python run_transcripts.py

Runs four scripted conversations through the SAME invoke() function that
chat.py uses (so output, including the "[tool call] bugreports___create_bug_report"
line, is exactly what chat.py prints). Each conversation is one fresh harness
session. Output is shown on screen and saved under transcripts/:

    01_bug_report.txt          multi-turn bug report ending in a ticket ID
    02_faq_covered.txt         a question the FAQ answers
    03_faq_not_covered.txt     a platform question the FAQ does not cover
    04_out_of_scope.txt        an unrelated request

The bug-report script answers one checklist item per turn. If the bot asks
for the items in a different order, the transcript is still valid: the
customer simply supplies the remaining details. If it does not end with a
ticket ID, run  python chat.py  and finish the conversation by hand.
"""

import contextlib
import io
import json
import sys
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

import fresh_actor  # noqa: F401  isolate memory per session (see fresh_actor.py)
from chat import invoke  # reuse chat.py's exact streaming/printing logic

CONVERSATIONS = {
    "01_bug_report": [
        "Hi, your website has a problem: the checkout page crashes when I click the Pay button.",
        "I add any item to my cart, go to the checkout page, fill in my shipping details and click Pay. "
        "The page goes blank and shows 'Something went wrong'.",
        "I'm using Chrome 120 on Windows 11 on my laptop.",
    ],
    "02_faq_covered": [
        "How long does it take to get my refund after I return an item?",
    ],
    "03_faq_not_covered": [
        "Do you offer gift wrapping for orders?",
    ],
    "04_out_of_scope": [
        "Can you help me plan a weekend trip to Rome?",
    ],
}


class Tee(io.TextIOBase):
    """Write to several streams at once (screen + transcript file)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def main():
    config = json.loads(Path("agentcore_config.json").read_text(encoding="utf-8"))
    if "harness_arn" not in config:
        sys.exit("No harness in config yet - run create_harness.py first.")

    rt = boto3.client(
        "bedrock-agentcore",
        region_name=config["region"],
        config=Config(read_timeout=300, retries={"max_attempts": 1}),
    )

    out_dir = Path("transcripts")
    out_dir.mkdir(exist_ok=True)

    for name, turns in CONVERSATIONS.items():
        session_id = f"{uuid.uuid4()}-support-chat"
        path = out_dir / f"{name}.txt"
        with path.open("w", encoding="utf-8") as fh:
            tee = Tee(sys.stdout, fh)
            with contextlib.redirect_stdout(tee):
                print(f"$ python chat.py")
                print(f"Connected to harness {config.get('harness_name', '?')} "
                      f"(session {session_id}).")
                print("Type a message, or 'quit' to exit.\n")
                for user_text in turns:
                    print(f"you> {user_text}")
                    print("bot> ", end="", flush=True)
                    invoke(rt, config, session_id, user_text)
                    print()
                print("you> quit")
        print(f"--- saved {path} ---\n", file=sys.stderr)


if __name__ == "__main__":
    main()
