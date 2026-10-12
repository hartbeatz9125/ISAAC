import unittest

from isaac_core.brain.adapters.llama_cpp import normalize_chat_messages, shorten_chat_context


class ChatRoleTests(unittest.TestCase):
    def test_context_retry_removes_oldest_pair_preserving_current_request(self):
        history = [{"role": r, "content": str(i)} for i, r in enumerate(
            ["system", "user", "assistant", "user", "assistant", "user"])]
        result = shorten_chat_context(history)
        self.assertEqual([m["content"] for m in result], ["0", "3", "4", "5"])
        self.assertEqual(len(history), 6)

    def test_oversized_source_block_keeps_identity_and_latest_request(self):
        history = [{"role": "system", "content": "IDENTITY " + "x" * 10000},
                   {"role": "user", "content": "Hello"}]
        result = shorten_chat_context(history)
        self.assertTrue(result[0]["content"].startswith("IDENTITY"))
        self.assertEqual(result[-1], history[-1])
        self.assertLess(len(result[0]["content"]), len(history[0]["content"]))

    def test_failed_or_cancelled_reply_preserves_both_user_messages(self):
        history = [
            {"role": "system", "content": "Identity"},
            {"role": "user", "content": "Unanswered"},
            {"role": "user", "content": "Try again"},
        ]
        prompt = normalize_chat_messages(history)
        self.assertEqual([m["role"] for m in prompt], ["system", "user"])
        self.assertEqual(prompt[-1]["content"], "Unanswered\n\nTry again")
        self.assertEqual(len(history), 3)
        self.assertEqual(history[1]["content"], "Unanswered")

    def test_normal_history_unchanged(self):
        history = [{"role": r, "content": r} for r in ["system", "user", "assistant", "user"]]
        self.assertEqual(normalize_chat_messages(history), history)

    def test_repeated_roles_inside_restored_history(self):
        history = [{"role": r, "content": r} for r in ["system", "user", "user", "assistant", "assistant", "user"]]
        self.assertEqual([m["role"] for m in normalize_chat_messages(history)],
                         ["system", "user", "assistant", "user"])
