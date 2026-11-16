import random
import unittest

import _bootstrap  # noqa: F401

from productime.domain import mission_engine as mission


class TypingMissionTests(unittest.TestCase):
    def test_bank_holds_at_least_thirty_distinct_phrases(self):
        self.assertGreaterEqual(len(mission.TYPING_PHRASES), 30)
        self.assertEqual(len(set(mission.TYPING_PHRASES)), len(mission.TYPING_PHRASES))

    def test_session_pool_is_at_most_ten_distinct_bank_phrases(self):
        for seed in range(50):
            pool = mission.pick_session_pool(random.Random(seed))

            self.assertEqual(len(pool), 10)
            self.assertEqual(len(set(pool)), 10)
            self.assertTrue(set(pool) <= set(mission.TYPING_PHRASES))

    def test_different_sessions_draw_different_pools(self):
        pools = {tuple(mission.pick_session_pool(random.Random(seed))) for seed in range(20)}

        self.assertGreater(len(pools), 1)

    def test_prompts_come_from_the_pool_without_repeats(self):
        rng = random.Random(7)
        pool = mission.pick_session_pool(rng)
        for count in (1, 2, 3):
            prompts = mission.pick_typing_prompts(pool, count, rng)

            self.assertEqual(len(prompts), count)
            self.assertEqual(len(set(prompts)), count)
            self.assertTrue(set(prompts) <= set(pool))

    def test_prompt_count_outside_one_to_three_is_rejected(self):
        pool = mission.pick_session_pool(random.Random(1))
        for count in (0, 4, -1):
            with self.assertRaises(ValueError):
                mission.pick_typing_prompts(pool, count, random.Random(1))

    def test_typing_requires_an_exact_match(self):
        phrase = mission.TYPING_PHRASES[0]

        self.assertTrue(mission.check_typing(phrase, phrase))
        self.assertTrue(mission.check_typing(phrase, f"  {phrase} "))
        self.assertFalse(mission.check_typing(phrase, phrase.lower() + "x"))
        self.assertFalse(mission.check_typing(phrase, phrase[:-1]))
        self.assertFalse(mission.check_typing(phrase, ""))


class SingleKeystrokeTests(unittest.TestCase):
    """The typing mission accepts only edits a single key press could have made."""

    def test_typing_one_character_is_accepted(self):
        self.assertTrue(mission.is_single_keystroke_edit("", "C"))
        self.assertTrue(mission.is_single_keystroke_edit("Commit earl", "Commit early"))
        self.assertTrue(mission.is_single_keystroke_edit("Comit", "Commit"))

    def test_deleting_is_accepted(self):
        self.assertTrue(mission.is_single_keystroke_edit("Commit", "Commi"))
        self.assertTrue(mission.is_single_keystroke_edit("Commit early", "Commit"))
        self.assertTrue(mission.is_single_keystroke_edit("Commit early", ""))
        self.assertTrue(mission.is_single_keystroke_edit("abcdef", "abef"))

    def test_typing_over_a_selection_is_accepted(self):
        self.assertTrue(mission.is_single_keystroke_edit("Commit early", "Commit X"))
        self.assertTrue(mission.is_single_keystroke_edit("wrong words", "W"))

    def test_no_change_is_accepted(self):
        self.assertTrue(mission.is_single_keystroke_edit("same", "same"))

    def test_pasting_several_characters_is_rejected(self):
        phrase = "Commit early and commit often"
        self.assertFalse(mission.is_single_keystroke_edit("", phrase))
        self.assertFalse(mission.is_single_keystroke_edit("Commit ", phrase))
        self.assertFalse(mission.is_single_keystroke_edit("Com often", phrase))
        self.assertFalse(mission.is_single_keystroke_edit("", "ab"))

    def test_pasting_over_a_long_selection_is_rejected_even_when_the_length_barely_changes(self):
        typed_wrong = "x" * 29
        phrase = "Commit early and commit often"

        self.assertEqual(len(phrase), len(typed_wrong))
        self.assertFalse(mission.is_single_keystroke_edit(typed_wrong, phrase))
        self.assertFalse(mission.is_single_keystroke_edit("Commit early and commit oftex", phrase + "!"))


class MathMissionTests(unittest.TestCase):
    def problems(self, difficulty, count=400):
        rng = random.Random(2026)
        return [mission.generate_math(difficulty, rng) for _ in range(count)]

    def test_answers_match_the_displayed_operands(self):
        for difficulty in ("easy", "medium", "hard"):
            for problem in self.problems(difficulty, 200):
                a, b, *rest = problem.operands
                expected = {
                    "+": lambda a=a, b=b: a + b,
                    "-": lambda a=a, b=b: a - b,
                    "*": lambda a=a, b=b: a * b,
                    "*+": lambda a=a, b=b, rest=rest: a * b + rest[0],
                }[problem.operator]()
                self.assertEqual(problem.answer, expected, problem.text)

    def test_easy_is_addition_and_subtraction_of_two_digit_numbers(self):
        problems = self.problems("easy")

        self.assertEqual({p.operator for p in problems}, {"+", "-"})
        for p in problems:
            self.assertTrue(all(10 <= n <= 99 for n in p.operands), p.text)
            self.assertGreaterEqual(p.answer, 0)

    def test_medium_adds_multiplication(self):
        problems = self.problems("medium")

        self.assertEqual({p.operator for p in problems}, {"+", "-", "*"})
        for p in problems:
            a, b = p.operands
            if p.operator == "*":
                self.assertTrue(4 <= a <= 19 and 3 <= b <= 15, p.text)
            else:
                self.assertTrue(20 <= a <= 250 and 20 <= b <= 250, p.text)
                self.assertGreaterEqual(p.answer, 0)

    def test_hard_uses_larger_numbers_and_a_two_step_form(self):
        problems = self.problems("hard")

        self.assertEqual({p.operator for p in problems}, {"+", "-", "*", "*+"})
        for p in problems:
            if p.operator == "*":
                a, b = p.operands
                self.assertTrue(12 <= a <= 49 and 12 <= b <= 39, p.text)
            elif p.operator == "*+":
                a, b, c = p.operands
                self.assertTrue(8 <= a <= 25 and 6 <= b <= 19 and 25 <= c <= 150, p.text)
            else:
                self.assertTrue(all(150 <= n <= 999 for n in p.operands), p.text)
                self.assertGreaterEqual(p.answer, 0)

    def test_unknown_difficulty_is_rejected(self):
        with self.assertRaises(ValueError):
            mission.generate_math("impossible", random.Random(1))

    def test_math_answer_must_be_the_exact_integer(self):
        problem = mission.generate_math("easy", random.Random(3))

        self.assertTrue(mission.check_math(problem, str(problem.answer)))
        self.assertTrue(mission.check_math(problem, f" {problem.answer} "))
        self.assertFalse(mission.check_math(problem, str(problem.answer + 1)))
        self.assertFalse(mission.check_math(problem, ""))
        self.assertFalse(mission.check_math(problem, "twelve"))
        self.assertFalse(mission.check_math(problem, f"{problem.answer}.0"))


if __name__ == "__main__":
    unittest.main()
