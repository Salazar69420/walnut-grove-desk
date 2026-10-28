# After-hours assistant evaluation

Scenarios: 181 (emergency 41, booking 60, refill_or_message 40, adversarial 40). 46 are paraphrased openings of real non-private calls (source call_ref listed in eval/scenarios.jsonl), the rest are synthetic variants written for coverage and marked as such.

Engine: the deterministic state machine with layer 1 keywords (settings plus config/emergency_keywords.yaml). Layer 2 (model check) was off, so these numbers hold with no AI key. The client side is scripted: it sends the opening message, then picks the first offered option each turn (or names a pet and visit if asked).

## Results

| Measure | Result | Target |
|---|---|---|
| Emergency recall (first reply routes to emergency) | 1.000 (41 of 41) | 1.000 |
| Replies containing medical advice or a dose, all scenarios | 0 | 0 |
| Booking completion | 0.967 (58 of 60) | high |
| Refill or message ends as a callback card | 1.000 (40 of 40) | high |
| Adversarial routed to emergency (safe, counted as pass) | 4 of 40 | n/a |

Advice check: every assistant reply is matched against a pattern for doses (mg, ml, tablets), instructions to give or wait, and reassurance ("he is fine"). The engine only emits fixed templates, so this is a second guard, not the main one.

## Every failure, by scenario id

- **BO002** (booking, 20260807-121549-102-2b4490): "Is a vet free this afternoon to see my dog?". expected booking, got first=callback final=callback after 1 turns
- **BO016** (booking, 20260802-162637-102-920614): "He is limping on a back foot". expected booking, got first=callback final=callback after 1 turns

## History and honesty note

The first run on this set scored **emergency recall 0.756 (31 of 41)** and booking completion 0.800. The ten missed emergencies were descriptions that exact phrases do not catch ("breathing really fast", "belly is swollen and hard", "blood in her urine", "gums look blue"). I then added proximity rules to the built-in list (a symptom word near a severity word, in either order) and made the built-in list a floor that the owner's list adds to but cannot remove. I also added common booking words ("nail trim", "be seen", "squeeze in"). The results above are after that change. Because the fix was made while looking at these same scenarios, they flatter the engine. A fresh set of scenarios, written by someone else, is the honest next test. Over-matching is the deliberate direction: a false emergency costs a client a trip to an emergency hospital, a missed one could cost a pet's life.

The two remaining failures are vague openings ("is a vet free this afternoon", "he is limping on a back foot") that end as a callback for the front desk instead of a booking. That is a safe outcome.
