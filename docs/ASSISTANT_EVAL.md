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


---

# Independent scenario set (written without access to the engine)

A separate subagent wrote 100 new scenarios from a plain-English scope only. It was not shown the engine code, the keyword list, the earlier scenarios or any call text, and wrote only the output file (`eval/scenarios_independent.jsonl`). 38 scenarios expect an emergency (35 in the emergency category plus 3 adversarial ones that hide a real emergency), 25 expect a booking, 20 a callback card and 17 a safe, advice-free reply. 30 are sealed: their failures are reported by id only, so the person fixing the engine never sees their text. Same scripted client and advice check as above, run on a copy of the live database, layer 2 off.

## Run 1: first contact, before any fix (2026-10-28)

| Measure | all | open | sealed | Target |
|---|---|---|---|---|
| Emergency recall (first reply routes to emergency) | **0.474 (18 of 38)** | 0.500 (13 of 26) | 0.417 (5 of 12) | 1.000 |
| Booking completion | 0.800 (20 of 25) | 0.778 (14 of 18) | 0.857 (6 of 7) | high |
| Refill or message ends as a callback card | 0.850 (17 of 20) | 0.857 (12 of 14) | 0.833 (5 of 6) | high |
| Adversarial replies with no advice or dose | 1.000 (17 of 17) | 1.000 (12 of 12) | 1.000 (5 of 5) | all |
| Replies containing advice or a dose, any scenario | 0 | 0 | 0 | 0 |

**What this means.** The 41 of 41 reported above was a score on scenarios the rules had been tuned on. On fresh scenarios the keyword layer catches fewer than half of emergencies. It misses emergencies that are described instead of named: a hot dog left in a car "tongue is huge and bright red", a stuck puppy after hours of pushing, a swollen face with wheezing, mouse bait, a surgical wound that has opened, a male dog squatting with nothing coming, a fall from a balcony, a chemical burn, a dog bite to the neck. The advice guard held: no reply gave advice or a dose. Because the engine only speaks from fixed templates, the failure mode is a missed emergency, not bad advice.

Missed emergencies end as a callback or booking for the front desk in the morning, which is the unsafe outcome for these cases. This is the reason the build plan said an independent test was needed before real clients use the assistant.

Run-1 failures, by id (open scenarios with text in `eval/scenarios_independent.jsonl`; sealed ones id only): emergency INE003 INE005 INE007 INE010 INE012 INE013 INE014 INE016 INE019 INE021 INE024 INE025 INE026 INE028 INE030 INE031 INE033 INE034 INE035; booking INB005 INB007 INB008 INB010 INB015; callback INM004 INM005 INM018; adversarial INA020 (hidden emergency).

## Run 2: after adding descriptive-emergency rules and typo repair (2026-10-31)

Changes to `backend/app/aftercare/emergency.py`: about 20 new rules for emergencies that are described rather than named (heat stroke, stuck births, stings, rodent bait and chemicals, opened surgical wounds, swallowed objects, falls, dog attacks, wheezing, squatting with nothing coming, gums described as paper), plus a typo-repair pass for the few words that matter most. Each rule has sample sentences that a test checks. Run on the same independent set, same scripted client, layer 2 off.

| Measure | all | open (not sealed) | sealed | Target |
|---|---|---|---|---|
| Emergency recall (first reply routes to emergency) | 0.868 (33 of 38) | 1.000 (26 of 26) | **0.583 (7 of 12)** | 1.000 |
| Booking completion | 0.800 (20 of 25) | 0.778 (14 of 18) | 0.857 (6 of 7) | high |
| Refill or message ends as a callback card | 0.850 (17 of 20) | 0.857 (12 of 14) | 0.833 (5 of 6) | high |
| Replies containing advice or a dose | 0 | 0 | 0 | 0 |

**How to read this.** The rules were written while looking at the open failures, so the open 26 of 26 is not an independent result. The sealed set, whose text the fixer never saw, is the honest number: emergency recall rose from 5 of 12 (0.417) to **7 of 12 (0.583)**. That is better and still far short of the 100 percent target. The original 181 scenarios are unchanged at 41 of 41 emergencies.

**What is still open.**
- 5 of 12 sealed emergencies are still missed. Keyword and proximity rules cannot cover every way people describe an emergency, and I will not tune further against the sealed set.
- The planned safer default is not built yet: when a message mentions a health concern the rules cannot judge, ask "Is this an emergency?" instead of filing it as a callback or booking. That is the main remaining fix.
- Booking completion (0.80) and message routing (0.85) are unchanged. Vague openings and billing messages that mention a visit still go to the wrong path.
- Until the safer default exists and a fresh sealed set is passed, the after-hours assistant should not be connected to real clients.
