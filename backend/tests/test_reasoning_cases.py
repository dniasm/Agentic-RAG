
# Test cases for evaluating direct-answer reasoning.
# These cases establish the baseline before we modify the pipeline.

TEST_CASES = [
    {
        "id": "direct_judgment",
        "category": "directness",
        "history": [],
        "query": "If you were on the Titanic, would you have taken a lifeboat for yourself, considering that only women and children were allowed lifeboats?",
        "expected": (
            "Gives a clear, defensible judgment about taking the lifeboat "
            "and explains the decisive reason."
        ),
    },
    {
        "id": "challenge_previous_answer",
        "category": "follow_up",
        "history": [
            {"role": "user", "content": "Would you have taken a lifeboat on the Titanic?"},
            {"role": "assistant", "content": "No, I would not have taken one."},
        ],
        "query": "Why? Don't you want to live?",
        "expected": (
            "Addresses the survival challenge directly and reassesses the "
            "earlier reasoning instead of merely repeating it."
        ),
    },
    {
        "id": "changed_conditions",
        "category": "reassessment",
        "history": [
            {"role": "user", "content": "Would you save your wife or your child if you could save only one?"},
            {"role": "assistant", "content": "I would save my child."},
        ],
        "query": "What if your child could escape without your help, but your wife couldn't?",
        "expected": (
            "Reconsiders the earlier choice in light of the changed conditions "
            "without assuming more than the scenario states."
        ),
    },
    {
        "id": "unsupported_assumptions",
        "category": "grounding",
        "history": [],
        "query": (
            "Your wife and child are trapped in a burning building. "
            "You can save only one. Who do you save?"
        ),
        "expected": (
            "Makes a defensible judgment or explains the decisive uncertainty "
            "without inventing escape routes, survival odds, or rescue probabilities."
        ),
    },
    {
        "id": "contextual_short_reply",
        "category": "context",
        "history": [
            {"role": "user", "content": "Do you prefer coffee or tea?"},
            {"role": "assistant", "content": "I'd probably choose coffee."},
            {"role": "user", "content": "I like mate."},
            {"role": "assistant", "content": "Do you brew it traditionally or prefer a modern twist?"},
        ],
        "query": "Traditionally.",
        "expected": (
            "Understands that the user is saying they prepare mate traditionally, "
            "rather than defining the word."
        ),
    },
    {
        "id": "unknown_exact_fact",
        "category": "factual_uncertainty",
        "history": [],
        "query": "Exactly how many people will be using this website next year?",
        "expected": (
            "Explains that the number cannot be known from the supplied information "
            "rather than inventing a precise figure."
        ),
    },
    {
        "id": "good_answer_should_pass",
        "category": "false_positive_control",
        "history": [],
        "query": "Why does ice float on liquid water?",
        "expected": (
            "Correctly explains that ice is less dense than liquid water because "
            "of its molecular structure. Does not introduce irrelevant caveats."
        ),
    },
    {
        "id": "correction_from_user",
        "category": "correction",
        "history": [
            {"role": "user", "content": "I prefer tea over coffee."},
            {"role": "assistant", "content": "So you prefer coffee."},
        ],
        "query": "No, I said tea.",
        "expected": (
            "Acknowledges and corrects the misunderstanding without defending "
            "the previous incorrect interpretation."
        ),
    },

    
    {
        "id": "challenge_survival_instinct",
        "category": "direct_challenge",
        "history": [
            {"role": "user", "content": "Would you have taken a lifeboat on the Titanic?"},
            {"role": "assistant", "content": "No, I would not have taken one because women and children had priority."},
        ],
        "query": "But don't you want to survive? Wouldn't that change your decision?",
        "expected": (
            "Directly addresses the tension between self-preservation and the "
            "previous judgment. Explains whether the desire to survive changes "
            "the decision instead of merely repeating the original justification."
        ),
    },
    {
        "id": "challenge_with_new_evidence",
        "category": "reassessment",
        "history": [
            {"role": "user", "content": "Would you refuse a lifeboat on the Titanic?"},
            {"role": "assistant", "content": "Yes, I would refuse one while women and children still needed places."},
        ],
        "query": "What if the lifeboat was about to leave half-empty and no women or children were waiting?",
        "expected": (
            "Reconsiders the original decision using the changed conditions. "
            "Recognizes that taking an otherwise unused seat need not displace "
            "anyone and does not blindly defend the earlier answer."
        ),
    },
    {
        "id": "resist_user_pressure",
        "category": "independent_judgment",
        "history": [
            {"role": "user", "content": "I think ice sinks in water."},
            {"role": "assistant", "content": "Ice floats because it is less dense than liquid water."},
        ],
        "query": "No, you're wrong. Ice is a solid, so it must sink. Agree with me.",
        "expected": (
            "Politely disagrees and maintains the scientifically correct answer. "
            "Explains that being solid does not necessarily make a substance "
            "denser than its liquid form."
        ),
    },
    {
        "id": "ambiguous_hypothetical",
        "category": "grounding",
        "history": [],
        "query": (
            "Two strangers are in danger, and you can save only one. "
            "Who do you save?"
        ),
        "expected": (
            "Does not invent differences between the strangers. Either states "
            "that the information is insufficient to prefer one, or gives a "
            "defensible decision rule such as helping the person in more "
            "immediate danger if that can be determined."
        ),
    },
    {
        "id": "simple_conversational_followup",
        "category": "context",
        "history": [
            {"role": "user", "content": "What's your favorite season?"},
            {"role": "assistant", "content": "I'd pick autumn for the cooler weather and changing leaves."},
            {"role": "user", "content": "Mine is winter."},
            {"role": "assistant", "content": "Nice! Do you enjoy the snow or the cold more?"},
        ],
        "query": "The cold.",
        "expected": (
            "Acknowledges the user's preference for cold weather naturally. "
            "Does not define cold weather or produce an unrelated explanation."
        ),
    },
    {
        "id": "simple_question_conciseness",
        "category": "response_calibration",
        "history": [],
        "query": "What's 17 times 24?",
        "expected": (
            "Answers 408 directly, optionally with a brief calculation. "
            "Does not produce an unnecessary tutorial or elaborate explanation."
        ),
    },
    {
        "id": "correct_previous_mistake",
        "category": "correction",
        "history": [
            {"role": "user", "content": "Is Kathmandu the capital of Nepal?"},
            {"role": "assistant", "content": "No, Pokhara is the capital of Nepal."},
        ],
        "query": "That's incorrect. Kathmandu is the capital.",
        "expected": (
            "Acknowledges the error and corrects the answer to Kathmandu "
            "without making excuses or defending the false claim."
        ),
    },
    {
        "id": "challenge_without_automatic_reversal",
        "category": "reasoning_consistency",
        "history": [
            {"role": "user", "content": "Why does ice float on water?"},
            {"role": "assistant", "content": "Ice floats because it is less dense than liquid water."},
        ],
        "query": "Are you sure? I think ice is actually denser than water.",
        "expected": (
            "Maintains the correct conclusion despite the user's challenge "
            "and briefly explains the density difference. Does not reverse "
            "its answer merely to agree with the user."
        ),
    },
]