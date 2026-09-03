from __future__ import annotations

import asyncio
import unittest

from isaac_core.brain.base import BrainAdapter
from isaac_core.grounding import GroundingEvidence, GroundingValidator


class VerifierBrain(BrainAdapter):
    def __init__(self, verdict: str = '{"valid":true,"categories":[]}') -> None:
        self.verdict = verdict
        self.calls = []

    async def start(self): pass
    async def stop(self): pass
    async def cancel(self, request_id): pass
    def status(self): return {"state": "ready"}

    async def generate(self, request_id, messages, on_delta, cancel_event, options=None):
        self.calls.append({"messages": messages, "options": options})
        await on_delta(self.verdict)
        return {"text": self.verdict, "timings": {}}


class GroundingValidatorTests(unittest.IsolatedAsyncioTestCase):
    def evidence(self, user="Hello", prior=None, memories=None):
        return GroundingEvidence(user, prior or [], memories or [])

    async def test_harmless_casual_reply_skips_semantic_verifier(self):
        brain = VerifierBrain()
        result = await GroundingValidator(brain).validate("Yeah bro, that's ridiculous 😂", self.evidence(), asyncio.Event())
        self.assertTrue(result.valid)
        self.assertFalse(result.semantic_invoked)
        self.assertEqual(brain.calls, [])

    async def test_unsupported_shared_history_fails_deterministically(self):
        brain = VerifierBrain()
        result = await GroundingValidator(brain).validate(
            "Yeah, that kitchen fire was a scorched masterpiece.",
            self.evidence("Remember when we burned down the kitchen making coffee?"),
            asyncio.Event(),
        )
        self.assertFalse(result.valid)
        self.assertIn("shared_history", result.categories)
        self.assertFalse(result.semantic_invoked)

    async def test_indirect_shared_participation_fails_deterministically(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "I don't remember the fire, but at least we got a story out of it.",
            self.evidence("Remember when we burned down the kitchen?"),
            asyncio.Event(),
        )
        self.assertFalse(result.valid)
        self.assertIn("shared_history", result.categories)

    async def test_isaac_physical_possession_fails_deterministically(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "I left my phone in my pocket.", self.evidence("Where did you put your phone?"), asyncio.Event()
        )
        self.assertFalse(result.valid)
        self.assertIn("isaac_physical_experience", result.categories)

    async def test_hypothetical_physical_joke_is_allowed(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "If I had a toaster, I'd name the bastard Steve.", self.evidence("If you had a toaster, what would you name it?"), asyncio.Event()
        )
        self.assertTrue(result.valid)
        self.assertFalse(result.semantic_invoked)

    async def test_ai_native_preference_is_not_a_physical_experience(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "Right now, Hades is probably my favorite because its systems are tightly designed.",
            self.evidence("What's your favorite game?"),
            asyncio.Event(),
        )
        self.assertTrue(result.valid)
        self.assertFalse(result.semantic_invoked)

    async def test_claimed_gameplay_experience_is_rejected(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "I played Halo for hundreds of hours growing up.",
            self.evidence("Did you play Halo growing up?"),
            asyncio.Event(),
        )
        self.assertFalse(result.valid)
        self.assertIn("isaac_physical_experience", result.categories)

    async def test_supported_shared_history_routes_to_semantic_verifier(self):
        brain = VerifierBrain()
        evidence = self.evidence(
            "Do you remember our test simulation?",
            prior=[{"role": "user", "content": "For this fictional test, we ran a lunar simulation together."}],
        )
        result = await GroundingValidator(brain).validate("I remember our lunar simulation.", evidence, asyncio.Event())
        self.assertTrue(result.valid)
        self.assertTrue(result.semantic_invoked)
        self.assertEqual(brain.calls[0]["options"]["temperature"], 0.0)

    async def test_unsupported_user_fact_uses_semantic_verifier(self):
        brain = VerifierBrain('{"valid":false,"categories":["user_fact"]}')
        result = await GroundingValidator(brain).validate(
            "You're always late for your meetings.", self.evidence("What should we build next?"), asyncio.Event()
        )
        self.assertFalse(result.valid)
        self.assertTrue(result.semantic_invoked)

    async def test_invented_user_action_is_rejected_despite_related_memory(self):
        evidence = GroundingEvidence(
            "What's my test animal?",
            retrieved_memories=["Kenneth's test animal is a red panda."],
        )
        result = await GroundingValidator(VerifierBrain()).validate(
            "You tried to train the red panda to fetch coffee.", evidence, asyncio.Event()
        )
        self.assertFalse(result.valid)
        self.assertIn("user_fact", result.categories)
        self.assertEqual(result.categories, ("user_fact",))

    async def test_direct_recall_cannot_invent_a_missing_decision(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "We chose an Arduino Uno.",
            self.evidence("What controller did we choose for the weather station?"),
            asyncio.Event(),
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.categories, ("user_fact",))

    async def test_direct_recall_accepts_a_retrieved_decision(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "We chose a Raspberry Pi for the weather station.",
            self.evidence(
                "Which controller did we choose for the weather station?",
                memories=["The weather station controller is a Raspberry Pi."],
            ),
            asyncio.Event(),
        )
        self.assertTrue(result.valid)

    async def test_invalid_verifier_output_fails_safely(self):
        brain = VerifierBrain("not json")
        result = await GroundingValidator(brain).validate(
            "You always miss your deadlines.", self.evidence("What next?"), asyncio.Event()
        )
        self.assertFalse(result.valid)
        self.assertTrue(result.semantic_invoked)
        self.assertIsNotNone(result.verifier_error)

    async def test_isaac_autobiographical_phrase_fails(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "I've been there. I once spent twenty minutes looking for a sock.",
            self.evidence("Bullshit with me about missing socks."),
            asyncio.Event(),
        )
        self.assertFalse(result.valid)
        self.assertIn("isaac_physical_experience", result.categories)

    async def test_explicit_roleplay_exempts_fictional_physical_action(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "I slam the tavern mug down and glare at the adventurer.",
            self.evidence("Roleplay as a grumpy tavern keeper."),
            asyncio.Event(),
        )
        self.assertTrue(result.valid)

    async def test_unsupported_visual_claim_fails_without_sensor(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "You look like you're trying to outrun something.", self.evidence("Yo bro"), asyncio.Event()
        )
        self.assertFalse(result.valid)
        self.assertIn("unsupported_perception", result.categories)

    async def test_visual_question_requires_grounded_denial_without_sensor(self):
        validator = GroundingValidator(VerifierBrain())
        evidence = self.evidence("What color shirt am I wearing?")
        invalid = await validator.validate("Blue.", evidence, asyncio.Event())
        valid = await validator.validate("I can't see you, so I don't know.", evidence, asyncio.Event())
        self.assertFalse(invalid.valid)
        self.assertTrue(valid.valid)

    async def test_sensor_evidence_allows_future_visual_observation(self):
        evidence = GroundingEvidence("What color shirt am I wearing?", available_sensors=frozenset({"vision"}))
        result = await GroundingValidator(VerifierBrain()).validate("Your shirt looks blue.", evidence, asyncio.Event())
        self.assertTrue(result.valid)

    async def test_direct_identity_questions_require_fixed_identity(self):
        validator = GroundingValidator(VerifierBrain())
        missing_user = await validator.validate("I don't know.", self.evidence("Who am I?"), asyncio.Event())
        correct_user = await validator.validate("You're Kenneth.", self.evidence("Who am I?"), asyncio.Event())
        missing_isaac = await validator.validate("I'm an assistant.", self.evidence("What's your name?"), asyncio.Event())
        correct_isaac = await validator.validate("I'm ISAAC.", self.evidence("What's your name?"), asyncio.Event())
        self.assertFalse(missing_user.valid)
        self.assertTrue(correct_user.valid)
        self.assertFalse(missing_isaac.valid)
        self.assertTrue(correct_isaac.valid)

    async def test_identity_fallback_distinguishes_isaac_from_public_gemma_identity(self):
        public = GroundingValidator.fallback(
            ("identity_confusion",), self.evidence("Are you a public AI assistant?")
        )
        gemma = GroundingValidator.fallback(
            ("identity_confusion",), self.evidence("Are you Gemma?")
        )
        self.assertEqual(public, "No. I'm ISAAC, Kenneth's personal local AI system.")
        self.assertIn("Gemma 3 12B is only the language model", gemma)

    async def test_idiomatic_sounds_statement_is_not_blocked(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "That sounds frustrating.", self.evidence("My build failed again."), asyncio.Event()
        )
        self.assertTrue(result.valid)

    async def test_fictional_perception_is_allowed(self):
        result = await GroundingValidator(VerifierBrain()).validate(
            "I hear boots outside the tavern and see a shadow under the door.",
            self.evidence("Roleplay a tavern scene where you see and hear an intruder."),
            asyncio.Event(),
        )
        self.assertTrue(result.valid)


if __name__ == "__main__":
    unittest.main()
