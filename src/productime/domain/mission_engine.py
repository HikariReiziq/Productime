"""Alarm dismissal missions: typing phrases and generated arithmetic."""

import random
from collections.abc import Sequence
from dataclasses import dataclass

SESSION_POOL_SIZE = 10
MAX_MISSION_COUNT = 3

TYPING_PHRASES: tuple[str, ...] = (
    "Small steps finish big projects",
    "Read the error message twice",
    "Commit early and commit often",
    "Tests first then the code",
    "One batch at a time",
    "Close the tab and open the editor",
    "Today I ship one working feature",
    "Write it down before you forget",
    "A bug found early is a cheap bug",
    "Focus now and rest later",
    "Simple code is easier to trust",
    "Ask why before asking how",
    "Save your work then stretch",
    "A clear plan beats a long night",
    "Practice a little every single day",
    "Finish the task you started",
    "Read the docs before guessing",
    "Name things so others understand",
    "Break the problem into pieces",
    "Wake up and write some code",
    "Slow is smooth and smooth is fast",
    "Review your notes every morning",
    "Make it work then make it clean",
    "Check the logs before the forum",
    "Drink water and check your posture",
    "Push the branch before lunch",
    "Measure first and optimize second",
    "Learning Linux one command at a time",
    "Backups matter before they are needed",
    "Done today beats perfect someday",
    "Keep the build green",
    "Start the timer and begin",
)


@dataclass(frozen=True)
class MathProblem:
    text: str
    answer: int
    operator: str
    operands: tuple[int, ...]


def pick_session_pool(rng: random.Random, size: int = SESSION_POOL_SIZE) -> list[str]:
    return rng.sample(TYPING_PHRASES, min(size, len(TYPING_PHRASES)))


def pick_typing_prompts(pool: Sequence[str], count: int, rng: random.Random) -> list[str]:
    if not 1 <= count <= MAX_MISSION_COUNT:
        raise ValueError("Mission count must be between 1 and 3.")
    return rng.sample(list(pool), count)


def check_typing(expected: str, typed: str) -> bool:
    return typed.strip() == expected


def is_single_keystroke_edit(old: str, new: str) -> bool:
    """True when `new` is `old` with one contiguous range replaced by at most one character.

    That covers typing, deleting, and typing over a selection, and rejects every paste,
    drop, or other bulk insertion of two or more characters wherever it lands.
    """
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return len(new) - prefix - suffix <= 1


def _ordered_pair(rng: random.Random, low: int, high: int) -> tuple[int, int]:
    a, b = rng.randint(low, high), rng.randint(low, high)
    return (a, b) if a >= b else (b, a)


def _additive(rng: random.Random, operator: str, low: int, high: int) -> MathProblem:
    if operator == "+":
        a, b = rng.randint(low, high), rng.randint(low, high)
        return MathProblem(f"{a} + {b}", a + b, "+", (a, b))
    a, b = _ordered_pair(rng, low, high)
    return MathProblem(f"{a} - {b}", a - b, "-", (a, b))


def _product(rng: random.Random, a_range: tuple[int, int], b_range: tuple[int, int]) -> MathProblem:
    a, b = rng.randint(*a_range), rng.randint(*b_range)
    return MathProblem(f"{a} × {b}", a * b, "*", (a, b))


def generate_math(difficulty: str, rng: random.Random) -> MathProblem:
    if difficulty == "easy":
        return _additive(rng, rng.choice("+-"), 10, 99)
    if difficulty == "medium":
        operator = rng.choice("+-*")
        if operator == "*":
            return _product(rng, (4, 19), (3, 15))
        return _additive(rng, operator, 20, 250)
    if difficulty == "hard":
        operator = rng.choice(("+", "-", "*", "*+"))
        if operator == "*":
            return _product(rng, (12, 49), (12, 39))
        if operator == "*+":
            a, b, c = rng.randint(8, 25), rng.randint(6, 19), rng.randint(25, 150)
            return MathProblem(f"{a} × {b} + {c}", a * b + c, "*+", (a, b, c))
        return _additive(rng, operator, 150, 999)
    raise ValueError(f"Unknown difficulty: {difficulty}")


def generate_math_set(difficulty: str, count: int, rng: random.Random) -> list[MathProblem]:
    if not 1 <= count <= MAX_MISSION_COUNT:
        raise ValueError("Mission count must be between 1 and 3.")
    return [generate_math(difficulty, rng) for _ in range(count)]


def check_math(problem: MathProblem, typed: str) -> bool:
    text = typed.strip()
    # int() would accept "1_0" and unicode digits; the answer field is plain ASCII digits.
    if not text.isascii() or not text.lstrip("-").isdigit():
        return False
    return int(text) == problem.answer
