"""Commands people can send the bridge in an LXMF message.

  /ping            -> pong (anyone)
  /help            -> the command list
  /sh <command>    -> runs it in bash, replies with the output; signed
                      messages from addresses on the allowed list only

Pure decisions here; the agent does the sending and running.
"""

from __future__ import annotations

from dataclasses import dataclass

HELP = "/ping, /help, /sh <command> (allowed senders only)"
NOT_ALLOWED = "Not allowed: ask the bridge's owner to run `eyes-of-aziz-mesh allow <your LXMF address>`."
MAX_REPLY = 1500  # LoRa is slow: keep replies short


@dataclass(frozen=True)
class Decision:
    reply: str | None = None   # send this back
    shell: str | None = None   # run this, then reply with its output


def decide(text: str, sender: str, signed: bool, allowed: set[str]) -> Decision:
    text = text.strip()
    if text == "/ping":
        return Decision(reply="pong")
    if text == "/help":
        return Decision(reply=HELP)
    if text == "/sh" or text.startswith("/sh "):
        command = text[3:].strip()
        if not (signed and sender in allowed):
            return Decision(reply=NOT_ALLOWED)
        if not command:
            return Decision(reply="Usage: /sh <command>")
        return Decision(shell=command)
    return Decision()  # an ordinary message: just keep it in the inbox


def format_shell_reply(returncode: int | None, output: str, timeout: int) -> str:
    if returncode is None:
        reply = f"timed out after {timeout}s"
    else:
        reply = f"exit {returncode}\n{output.strip() or '(no output)'}"
    if len(reply) > MAX_REPLY:
        reply = reply[:MAX_REPLY] + "\n…(truncated)"
    return reply
