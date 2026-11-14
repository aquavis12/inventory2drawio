"""Decide in code whether a message asks for a diagram.

A small model given tools, a scope and a flow description will often draw for any message,
even "hi". So the app decides first: messages with no drawing intent are answered by a
chat agent that has no tools, no scope and no flow description to act on."""
import re

# Trailer the UI appends to every message; it is not the user's words.
UI_TRAILER = "\n\n[Scope from the UI]"

DRAW_WORDS = re.compile(
    r"\b(draw\w*|redraw\w*|diagram\w*|inventory|architecture|visuali[sz]e|render\w*|generate|"
    r"regenerate|redo|retry|map|topology|show|display|list|create|make|build|export|drawio|"
    r"hide|exclude|include|leave out|add|remove|again|update|change|"
    r"prefer\w*|remember\w*|memory|"            # preference questions need the memory-backed agent
    r"worth a look|risks?|review|explain|flow|connections?|connected|"
    r"resources?|buckets?|vpcs?|subnets?|network|serverless|tagged|tags?|application|app|"
    r"region|account|everything|lambda|dynamodb|tables?|sqs|sns|s3|ec2|rds|cloudfront|"
    r"route ?53|cognito|api|alb|eks|ecs)\b",
    re.IGNORECASE)


def user_words(prompt):
    """What the person typed, without the scope text the UI adds."""
    return (prompt or "").split(UI_TRAILER)[0].strip()


def wants_diagram(prompt):
    return bool(DRAW_WORDS.search(user_words(prompt)))


class SectionCutter:
    """Drops the closing "Worth a look" section from the streamed answer when the collector
    reported no findings. The prompt says to omit it, but a small model still writes
    "Worth a look: (none)"; the app enforces it. Works across chunk boundaries."""
    MARK = re.compile(r"\s*(?:\*\*|#+\s*)?Worth a look", re.IGNORECASE)
    HOLD = 24    # characters kept back in case the marker is split across chunks

    def __init__(self):
        self.buf, self.cut = "", False

    def feed(self, text, active):
        if self.cut or not text and not self.buf:
            return ""
        self.buf += text
        if not active:
            out, self.buf = self.buf, ""
            return out
        m = self.MARK.search(self.buf)
        if m:
            out, self.buf, self.cut = self.buf[:m.start()], "", True
            return out
        out = self.buf[:-self.HOLD] if len(self.buf) > self.HOLD else ""
        self.buf = self.buf[len(out):]
        return out

    def flush(self, active):
        out = "" if self.cut else self.buf
        self.buf = ""
        if active and out:
            m = self.MARK.search(out)
            out = out[:m.start()] if m else out
        return out
