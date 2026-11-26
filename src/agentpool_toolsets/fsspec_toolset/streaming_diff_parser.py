"""Streaming diff parser for incremental diff processing.

Parses unified diff format incrementally as chunks arrive,
emitting events that can be used with StreamingFuzzyMatcher.
"""

from dataclasses import dataclass
from enum import Enum, auto


class ParserState(Enum):
    """Parser state machine states."""

    PENDING = auto()  # Waiting for diff start
    IN_DIFF = auto()  # Inside <diff> block, collecting lines
    DONE = auto()  # Finished parsing


@dataclass
class OldTextChunk:
    """Event: chunk of old/context text for location matching."""

    chunk: str
    done: bool
    line_hint: int | None = None


@dataclass
class NewTextChunk:
    """Event: chunk of new text to insert."""

    chunk: str
    done: bool


# Union type for parser events
DiffParserEvent = OldTextChunk | NewTextChunk


class StreamingDiffParser:
    """Streaming parser for locationless unified diff format.

    Parses diff format incrementally and emits events:
    - OldTextChunk: Context and removed lines (for location matching)
    - NewTextChunk: Added lines (for insertion)

    Format expected:
    ```
    <diff>
     context line
    -removed line
    +added line
     more context
    </diff>
    ```

    Usage:
        parser = StreamingDiffParser()
        for chunk in streaming_response:
            for event in parser.push(chunk):
                if isinstance(event, OldTextChunk):
                    matcher.push(event.chunk)
                elif isinstance(event, NewTextChunk):
                    # apply edit
    """

    def __init__(self) -> None:
        self.state = ParserState.PENDING
        self.buffer = ""
        self.current_hunk_old: list[str] = []
        self.current_hunk_new: list[str] = []
        self._in_hunk = False
        self._old_text_done = False

    def push(self, chunk: str) -> list[DiffParserEvent]:  # noqa: PLR0915
        """Push a chunk of text and get any events produced.

        Args:
            chunk: Text chunk from streaming response

        Returns:
            List of parser events (OldTextChunk or NewTextChunk)
        """
        self.buffer += chunk
        events: list[DiffParserEvent] = []

        while True:
            match self.state:
                case ParserState.PENDING if "<diff>" in self.buffer:
                    # Look for <diff> start tag
                    start_idx = self.buffer.find("<diff>")
                    self.buffer = self.buffer[start_idx + 6 :]  # Skip <diff>
                    # Strip leading newline if present
                    if self.buffer.startswith("\n"):
                        self.buffer = self.buffer[1:]
                    self.state = ParserState.IN_DIFF
                    self._in_hunk = True
                    # Also check for ```diff format
                case ParserState.PENDING if "```diff" in self.buffer:
                    start_idx = self.buffer.find("```diff")
                    self.buffer = self.buffer[start_idx + 7 :]  # Skip ```diff
                    if self.buffer.startswith("\n"):
                        self.buffer = self.buffer[1:]
                    self.state = ParserState.IN_DIFF
                    self._in_hunk = True
                case ParserState.PENDING:
                    break

                case ParserState.IN_DIFF:
                    # Check for end tag
                    end_tag = None
                    end_idx = -1
                    if "</diff>" in self.buffer:
                        end_tag = "</diff>"
                        end_idx = self.buffer.find("</diff>")
                    elif "```" in self.buffer and not self.buffer.strip().startswith("```diff"):
                        # End of code block (but not start of new one)
                        end_tag = "```"
                        end_idx = self.buffer.find("```")

                    # Process complete lines
                    while "\n" in self.buffer:
                        newline_idx = self.buffer.find("\n")

                        # Check if end tag is before this newline
                        if end_idx != -1 and end_idx < newline_idx:
                            # Process remaining content before end tag
                            remaining = self.buffer[:end_idx]
                            if remaining.strip():
                                line_events = self._process_line(remaining)
                                events.extend(line_events)
                            # Emit final events for current hunk
                            events.extend(self._finalize_hunk())
                            tag_len = len(end_tag) if end_tag else 0
                            self.buffer = self.buffer[end_idx + tag_len :]
                            self.state = ParserState.DONE
                            break

                        line = self.buffer[:newline_idx]
                        self.buffer = self.buffer[newline_idx + 1 :]

                        # Update end_idx after consuming buffer
                        if end_tag and end_tag in self.buffer:
                            end_idx = self.buffer.find(end_tag)
                        else:
                            end_idx = -1

                        line_events = self._process_line(line)
                        events.extend(line_events)

                    if self.state == ParserState.DONE:
                        break

                    # If we have partial line and potential end tag prefix, wait for more
                    if self._could_be_end_tag_prefix():
                        break

                    break

                case ParserState.DONE:
                    break

        return events

    def _process_line(self, line: str) -> list[DiffParserEvent]:
        """Process a single diff line."""
        events: list[DiffParserEvent] = []

        # Empty line = hunk separator
        if not line or (not line.startswith(("+", "-", " ")) and not line.strip()):
            # Finalize current hunk if we have content
            if self.current_hunk_old or self.current_hunk_new:
                events.extend(self._finalize_hunk())
            return events

        # Skip non-diff content
        if not line.startswith(("+", "-", " ")):
            return events

        if line.startswith("-"):
            # Removed line - part of old text
            content = line[1:]
            self.current_hunk_old.append(content)
            # Emit incremental old text chunk
            events.append(OldTextChunk(chunk=content + "\n", done=False))

        elif line.startswith("+"):
            # Added line - part of new text
            content = line[1:]
            self.current_hunk_new.append(content)
            # Mark old text as done when we see first + line
            if not self._old_text_done and self.current_hunk_old:
                self._old_text_done = True
            # Emit incremental new text chunk
            events.append(NewTextChunk(chunk=content + "\n", done=False))

        elif line.startswith(" "):
            # Context line - in both old and new
            content = line[1:] if len(line) > 1 else ""
            self.current_hunk_old.append(content)
            self.current_hunk_new.append(content)
            # Emit as old text chunk for matching AND new text chunk for replacement
            events.append(OldTextChunk(chunk=content + "\n", done=False))
            events.append(NewTextChunk(chunk=content + "\n", done=False))

        return events

    def _finalize_hunk(self) -> list[DiffParserEvent]:
        """Finalize current hunk and emit done events."""
        events: list[DiffParserEvent] = []
        if self.current_hunk_old:
            # Emit final old text marker
            events.append(OldTextChunk(chunk="", done=True))
        if self.current_hunk_new:
            # Emit final new text marker
            events.append(NewTextChunk(chunk="", done=True))
        # Reset for next hunk
        self.current_hunk_old = []
        self.current_hunk_new = []
        self._old_text_done = False
        self._in_hunk = True
        return events

    def _could_be_end_tag_prefix(self) -> bool:
        """Check if buffer ends with potential end tag prefix."""
        prefixes = ["<", "</", "</d", "</di", "</dif", "</diff", "</diff>"]
        prefixes += ["`", "``", "```"]
        return any(self.buffer.endswith(p) for p in prefixes)

    def finish(self) -> list[DiffParserEvent]:
        """Finish parsing and emit any remaining events."""
        events: list[DiffParserEvent] = []

        # Process any remaining buffer content
        if self.buffer.strip() and self.state == ParserState.IN_DIFF:
            for line in self.buffer.split("\n"):
                if line:
                    events.extend(self._process_line(line))

        # Finalize any open hunk
        events.extend(self._finalize_hunk())
        return events
