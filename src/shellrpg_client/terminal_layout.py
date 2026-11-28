# Terminal dimensions use visible cells and exclude ANSI SGR bytes.
from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

ANSI = "\x1b["
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
SGR_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
ELLIPSIS = "..."


def strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


def _cell_width(character: str) -> int:
    if unicodedata.category(character) in {"Mn", "Me"}:
        return 0
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 2
    return 1


def _printable(text: str) -> str:
    return "".join(character for character in text if character.isprintable())


def display_width(text: str) -> int:
    return sum(_cell_width(character) for character in _printable(strip_ansi(text)))


def fit_text_width(text: str, max_visible: int) -> str:
    if max_visible <= 0:
        return ""
    inline = str(text).replace("\r", " ").replace("\n", " ")
    chunks: list[str] = []
    position = 0
    for match in ANSI_PATTERN.finditer(inline):
        chunks.append(_printable(inline[position:match.start()]))
        if SGR_PATTERN.fullmatch(match.group()):
            chunks.append(match.group())
        position = match.end()
    chunks.append(_printable(inline[position:]))
    clean = "".join(chunks)
    if display_width(clean) <= max_visible:
        if SGR_PATTERN.search(clean) and not clean.endswith(f"{ANSI}0m"):
            clean += f"{ANSI}0m"
        return clean

    marker = ELLIPSIS if max_visible >= len(ELLIPSIS) else "." * max_visible
    budget = max_visible - len(marker)
    parts: list[str] = []
    visible = 0
    colored = False
    for chunk in chunks:
        if SGR_PATTERN.fullmatch(chunk):
            parts.append(chunk)
            colored = True
            continue
        for character in chunk:
            width = _cell_width(character)
            if visible + width > budget:
                reset = f"{ANSI}0m" if colored else ""
                return "".join(parts) + reset + marker
            parts.append(character)
            visible += width
    return "".join(parts) + (f"{ANSI}0m" if colored else "") + marker


def pad_text_width(text: str, max_visible: int, align: str = "left") -> str:
    fitted = fit_text_width(text, max_visible)
    padding = max(0, max_visible - display_width(fitted))
    if align == "center":
        left = padding // 2
        return (" " * left) + fitted + (" " * (padding - left))
    if align == "right":
        return (" " * padding) + fitted
    return fitted + (" " * padding)


def fit_plain_terminal_line(text: str, columns: int) -> str:
    return fit_text_width(text, columns - 1)


def format_shell_prompt(cwd: Path, columns: int, *, colored: bool = False) -> str:
    prefix, suffix = "PS ", "> "
    if columns <= len(prefix) + len(suffix):
        return "> "[:max(0, columns - 1)]

    budget = columns - 1 - len(prefix) - len(suffix)
    path_text = _printable(strip_ansi(str(cwd)))
    if display_width(path_text) > budget:
        marker = ELLIPSIS if budget > len(ELLIPSIS) else ""
        remaining = budget - len(marker)
        tail: list[str] = []
        for character in reversed(path_text):
            width = _cell_width(character)
            if width > remaining:
                break
            tail.append(character)
            remaining -= width
        path_text = marker + "".join(reversed(tail))
    prompt = f"{prefix}{path_text}{suffix}"
    if colored:
        return f"{ANSI}1;96m{prefix}{ANSI}0m{path_text}{ANSI}1;96m{suffix}{ANSI}0m"
    return prompt


@dataclass
class ReservedTerminalRenderer:
    # Verwaltet einen festen Renderanker oberhalb der Eingabezeile, ohne die Prompt-Zeile selbst zu ueberschreiben.
    stream: TextIO = sys.stdout
    fallback_columns: int = 100
    fallback_lines: int = 30
    use_cursor: bool = True

    # Liest die aktuelle Terminalgroesse und faellt fuer Tests oder Pipes auf sichere Standardwerte zurueck.
    def terminal_size(self) -> os.terminal_size:  # type: ignore[name-defined]
        return shutil.get_terminal_size((self.fallback_columns, self.fallback_lines))

    # Reserviert Leerzeilen zwischen Verlauf und Prompt, damit die UI ausschliesslich dort gerendert wird.
    def reserve(self, rows: int) -> None:
        if rows <= 0:
            return
        self.stream.write("\n" * rows)
        self.stream.flush()

    # Rendert einen Block ueber dem zuletzt reservierten Anker und kehrt danach zur Eingabezeile zurueck.
    def draw_above_anchor(self, lines: list[str], rows: int) -> None:
        if rows <= 0:
            return
        self.stream.write("\x1b[s")
        self.stream.write(f"{ANSI}{rows}A")
        for idx in range(rows):
            self.stream.write(f"{ANSI}2K\r")
            self.stream.write(lines[idx] if idx < len(lines) else "")
            if idx < rows - 1:
                self.stream.write("\n")
        self.stream.write("\x1b[u")
        self.stream.flush()

    # Bereinigt die aktuelle Prompt-Zeile und schreibt den neuen Eingabeprompt kontrolliert an dieselbe Stelle.
    def write_prompt(self, prompt_text: str) -> None:
        self.stream.write(f"{ANSI}2K\r{prompt_text}" if self.use_cursor else prompt_text)
        self.stream.flush()

    # Stellt nach einem Wizard-Schritt den Prompt-Anker wieder her, ohne den Verlauf zu verschieben.
    def reanchor_prompt_line(self) -> None:
        self.stream.write(f"{ANSI}1A\r")
        self.stream.flush()


class ReservedRenderSession:
    # Kapselt mehrere Redraws gegen denselben Anker, etwa fuer Intro- oder Schriftrollen-Animationen.
    def __init__(self, renderer: ReservedTerminalRenderer, rows: int) -> None:
        self.renderer = renderer
        self.rows = rows
        self.active = False

    # Startet die Session und reserviert den benoetigten UI-Bereich genau einmal.
    def __enter__(self) -> "ReservedRenderSession":
        self.renderer.reserve(self.rows)
        self.active = True
        return self

    # Rendert den aktuellen Frame ueber dem Anker und aktualisiert optional die Eingabezeile.
    def render(self, lines: list[str], prompt_text: str = "") -> None:
        if not self.active:
            raise RuntimeError("Render-Session ist nicht aktiv.")
        self.renderer.draw_above_anchor(lines[: self.rows], self.rows)
        self.renderer.write_prompt(prompt_text)

    # Liest eine Eingabe ueber denselben Prompt-Anker ein und stellt danach den Zeichenanker wieder her.
    def read(self, lines: list[str], prompt_text: str) -> str:
        self.render(lines, prompt_text)
        value = input("")
        self.renderer.reanchor_prompt_line()
        return value

    # Loescht Panel und Prompt-Zeile kontrolliert, damit keine UI-Reste in den Live-Betrieb uebergehen.
    def clear(self) -> None:
        self.render([""] * self.rows, "")

    # Beendet die Session mit einem sauberen Reset des reservierten Bereichs.
    def __exit__(self, exc_type, exc, tb) -> None:
        if self.active:
            self.clear()
            self.active = False
