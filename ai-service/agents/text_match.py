"""Whole-word keyword matching, so "admin" is not "min" and "description" is
not "script"."""
import re

_ENDINGS = r"(?:s|es|d|ed|ing|ly|ure|ures)?"


def has_word(text: str, keywords) -> bool:
    return any(
        re.search(rf"(?<![a-z]){re.escape(k.strip())}{_ENDINGS}(?![a-z])", text.lower())
        for k in keywords
    )
