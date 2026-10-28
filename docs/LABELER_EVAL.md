# Evaluation of rules-v1 against gold

> **Read this first.** The gold set is 100 random calls (stratified by hour and weekday) plus 11 calls found by searching for emergency words and 1 family call found later by search, all hand-labeled by the orchestrating model (Opus). The rules were tuned on this same set, so these numbers are optimistic. There was no held-out set: the random sample held zero emergencies and only five private calls, too few to split.
>
> - **Emergency signs recall is 1.0 (6 of 6) only after tuning.** Before tuning it was 4 of 6.
> - **Private recall is 0.57 (4 of 7), well below the 0.95 target.** Rules cannot reliably tell a family chat from a short work call. To compensate, the production policy flags anything the rules cannot place as clinic work, so about 27 percent of real work calls are also flagged private and deleted. Every gold-labeled private call was deleted, whatever the rules said. In total 949 of 3,637 calls had their text deleted.
> - Intent accuracy is 0.39: keyword rules are weak at intent. A model labeler, run once the owner adds a DeepSeek key and allows it, should do much better and will be measured against this same gold set.
> - Without a model key, everything here comes from deterministic rules.


- gold labels: 112
- compared: 112
- gold without prediction: 0

## Accuracy per field

| field | accuracy | disagreements |
|---|---|---|
| real_direction | 0.714 | 32 |
| caller_type | 0.625 | 42 |
| intent | 0.393 | 68 |
| outcome | 0.652 | 39 |
| urgency | 0.670 | 37 |
| red_flags | 0.920 | 9 |
| promised_followup | 0.920 | 9 |
| followup_text | 0.875 | 14 |
| contains_private_content | 0.732 | 30 |
| language | 0.991 | 1 |

## emergency_signs

- tp=6 fp=8 fn=0 tn=98
- precision=0.429 recall=1.000

## contains_private_content

- tp=4 fp=27 fn=3 tn=78
- precision=0.129 recall=0.571

## Confusion counts (gold -> predicted)

### real_direction
- inbound -> inbound: 60
- inbound -> internal: 1
- inbound -> unknown: 5
- internal -> unknown: 5
- outbound -> inbound: 6
- outbound -> internal: 1
- outbound -> outbound: 19
- outbound -> unknown: 13
- unknown -> inbound: 1
- unknown -> unknown: 1

### caller_type
- automated_or_dead_air -> automated_or_dead_air: 2
- automated_or_dead_air -> client: 1
- automated_or_dead_air -> unknown: 4
- client -> automated_or_dead_air: 1
- client -> client: 58
- client -> other_clinic: 2
- client -> staff_internal: 2
- client -> unknown: 10
- lab_or_courier -> client: 1
- lab_or_courier -> lab_or_courier: 3
- lab_or_courier -> unknown: 4
- other_clinic -> client: 6
- other_clinic -> other_clinic: 4
- private_personal -> client: 4
- private_personal -> private_personal: 1
- staff_internal -> client: 3
- staff_internal -> unknown: 2
- unknown -> automated_or_dead_air: 1
- unknown -> unknown: 2
- vendor_or_sales -> unknown: 1

### intent
- billing_or_insurance -> recall_or_reminder: 1
- billing_or_insurance -> triage_symptom: 1
- booking -> booking: 8
- booking -> other: 1
- booking -> triage_symptom: 4
- check_in -> booking: 1
- check_in -> check_in: 2
- check_in -> other: 1
- check_in -> pickup_ready: 1
- check_in -> triage_symptom: 3
- courier_or_lab_logistics -> courier_or_lab_logistics: 3
- courier_or_lab_logistics -> other: 1
- courier_or_lab_logistics -> pickup_ready: 3
- courier_or_lab_logistics -> triage_symptom: 1
- food_or_supply_order -> booking: 2
- food_or_supply_order -> food_or_supply_order: 1
- food_or_supply_order -> refill: 1
- internal_logistics -> booking: 2
- internal_logistics -> other: 1
- internal_logistics -> triage_symptom: 3
- other -> check_in: 1
- other -> internal_logistics: 1
- other -> other: 11
- other -> records_request: 3
- other -> triage_symptom: 8
- pickup_ready -> other: 1
- pickup_ready -> pickup_ready: 1
- pickup_ready -> records_request: 1
- pricing_or_policy_question -> pricing_or_policy_question: 1
- pricing_or_policy_question -> triage_symptom: 1
- recall_or_reminder -> booking: 1
- recall_or_reminder -> recall_or_reminder: 3
- records_request -> recall_or_reminder: 1
- records_request -> records_request: 2
- records_request -> triage_symptom: 6
- refill -> food_or_supply_order: 1
- refill -> pickup_ready: 2
- refill -> pricing_or_policy_question: 1
- reschedule_or_cancel -> booking: 5
- reschedule_or_cancel -> pickup_ready: 1
- reschedule_or_cancel -> reschedule_or_cancel: 2
- reschedule_or_cancel -> triage_symptom: 1
- results_call_out -> check_in: 1
- results_call_out -> internal_logistics: 1
- results_call_out -> other: 1
- results_call_out -> results_question: 1
- results_question -> pickup_ready: 1
- results_question -> results_question: 2
- triage_symptom -> results_question: 1
- triage_symptom -> triage_symptom: 8

### outcome
- callback_promised -> callback_promised: 6
- hold_then_dropped -> unclear: 1
- no_answer -> no_answer: 2
- no_answer -> unclear: 3
- no_answer -> voicemail_left: 3
- resolved_on_call -> callback_promised: 5
- resolved_on_call -> resolved_on_call: 54
- resolved_on_call -> transferred: 1
- resolved_on_call -> unclear: 23
- unclear -> callback_promised: 1
- unclear -> unclear: 3
- unclear -> voicemail_left: 1
- voicemail_left -> unclear: 1
- voicemail_left -> voicemail_left: 8

### urgency
- emergency_signs -> emergency_signs: 6
- none -> emergency_signs: 5
- none -> none: 65
- none -> soon: 24
- soon -> emergency_signs: 3
- soon -> none: 5
- soon -> soon: 4

### red_flags
- ["emergency_stated"] -> ["emergency_stated"]: 3
- ["seizure"] -> ["seizure"]: 2
- ["toxin_chocolate"] -> ["emergency_stated", "toxin_chocolate"]: 1
- [] -> ["bleeding"]: 1
- [] -> ["cannot_pass_urine"]: 2
- [] -> ["emergency_stated"]: 2
- [] -> ["toxin_chocolate"]: 1
- [] -> ["trouble_breathing"]: 2
- [] -> []: 98

### promised_followup
- False -> False: 89
- False -> True: 8
- True -> False: 1
- True -> True: 14

### followup_text
- None -> None: 89
- None -> client to call clinic back: 1
- None -> clinic to call client back: 5
- None -> doctor to review and call back: 2
- check stock and call back -> clinic to call client back: 1
- client to call clinic back -> client to call clinic back: 6
- client to call clinic back -> clinic to call client back: 1
- client to call clinic back -> doctor to review and call back: 1
- clinic to call client back -> None: 1
- clinic to call client back -> clinic to call client back: 3
- doctor to review and call back -> clinic to call client back: 2

### contains_private_content
- False -> False: 78
- False -> True: 27
- True -> False: 3
- True -> True: 4

### language
- eng -> eng: 111
- eng -> pan: 1

## Disagreeing call_refs

### real_direction
20260805-140319-102-48378d, 20260805-160713-102-e3b915, 20260806-112243-102-b2b479, 20260808-132235-102-5960ba, 20260812-083014-102-bd3ac9, 20260815-114328-102-8d11be, 20260815-130730-101-011593, 20260815-163551-102-72558f, 20260821-135623-102-771d5f, 20260821-190136-102-5db495, 20260826-125337-103-9c7c32, 20260827-124644-103-627ca7, 20260828-090303-102-e0f244, 20260828-113551-102-7e6364, 20260828-143622-101-4aea3c, 20260901-122502-103-dee77a, 20260908-162240-102-3aca17, 20260909-164308-101-b46f60, 20260911-182711-102-cdb5ff, 20260912-152714-102-765d6c, 20260914-151248-102-726fc9, 20260916-125801-102-43e12c, 20260916-184855-102-0f0d21, 20260921-143847-101-97dc8a, 20260927-113903-103-9728a8, 20261007-163043-102-5505ef, 20261008-124641-101-88f2e7, 20261014-121257-102-a05566, 20261014-145506-103-ce6045, 20261014-164716-101-16265a, 20261017-090528-102-1dd2e4, 20261020-173302-103-c84a8b

### caller_type
20260804-100543-102-7e9284, 20260805-140319-102-48378d, 20260805-160713-102-e3b915, 20260806-091045-102-c3bdaf, 20260806-112243-102-b2b479, 20260808-132235-102-5960ba, 20260809-094059-102-780e58, 20260812-083014-102-bd3ac9, 20260812-111631-102-399dc6, 20260815-114328-102-8d11be, 20260815-130730-101-011593, 20260815-163551-102-72558f, 20260816-090749-102-535cbb, 20260820-092011-102-ed283b, 20260820-133507-102-37388c, 20260821-104413-102-f76d97, 20260827-124644-103-627ca7, 20260828-090303-102-e0f244, 20260828-113551-102-7e6364, 20260901-122502-103-dee77a, 20260903-144213-101-36cae5, 20260908-162240-102-3aca17, 20260910-150213-102-88cba2, 20260911-182711-102-cdb5ff, 20260912-152714-102-765d6c, 20260915-121948-102-64072c, 20260915-145238-102-9a2a7a, 20260916-125801-102-43e12c, 20260916-142945-102-78ffad, 20260916-184855-102-0f0d21, 20260919-094919-102-d3125f, 20260921-143847-101-97dc8a, 20260923-161018-102-f9498b, 20260923-170254-102-faae1a, 20260925-175450-102-2a05d2, 20260927-113903-103-9728a8, 20260928-151423-102-d50642, 20261008-124641-101-88f2e7, 20261014-121257-102-a05566, 20261014-145506-103-ce6045, 20261017-090528-102-1dd2e4, 20261021-093950-102-b8e799

### intent
20260729-174344-103-af145f, 20260730-173812-102-f09e0b, 20260802-090223-102-4bc16f, 20260804-100543-102-7e9284, 20260804-170814-102-1962f5, 20260805-094602-102-b610a0, 20260805-140319-102-48378d, 20260806-083609-102-3fe2a9, 20260806-091045-102-c3bdaf, 20260806-112243-102-b2b479, 20260807-091656-102-7ec3cb, 20260807-092022-103-a65de5, 20260808-132235-102-5960ba, 20260808-150525-102-936fe6, 20260809-094059-102-780e58, 20260812-083014-102-bd3ac9, 20260812-111631-102-399dc6, 20260813-152129-102-fc8cd5, 20260815-130730-101-011593, 20260815-163551-102-72558f, 20260820-092011-102-ed283b, 20260820-133507-102-37388c, 20260821-190136-102-5db495, 20260822-093351-102-f06594, 20260823-160247-102-ed3ed3, 20260824-093958-102-fb63f9, 20260824-162928-102-eafa0e, 20260827-124644-103-627ca7, 20260828-090303-102-e0f244, 20260828-113551-102-7e6364, 20260828-143622-101-4aea3c, 20260901-122502-103-dee77a, 20260902-132421-102-01d5bf, 20260902-142905-102-884122, 20260903-144213-101-36cae5, 20260905-105137-102-de7557, 20260909-084252-102-8449c5, 20260909-164308-101-b46f60, 20260911-182711-102-cdb5ff, 20260912-152714-102-765d6c, 20260914-103245-102-400ea3, 20260915-121948-102-64072c, 20260915-145238-102-9a2a7a, 20260916-115822-102-f60d87, 20260916-125801-102-43e12c, 20260916-184855-102-0f0d21, 20260917-135100-102-db8c44, 20260919-112153-102-36a7c6, 20260920-130038-102-204bff, 20260921-143847-101-97dc8a, 20260923-161018-102-f9498b, 20260923-170254-102-faae1a, 20260927-113903-103-9728a8, 20260928-151423-102-d50642, 20260929-102322-102-33612f, 20261005-095533-102-f94a74, 20261006-120226-102-6e9f3f, 20261008-124641-101-88f2e7, 20261010-114841-102-62092d, 20261014-145506-103-ce6045, 20261016-100217-102-a4ee6e, 20261016-164416-102-d768ff, 20261017-090528-102-1dd2e4, 20261018-142543-102-0d505d, 20261019-172307-102-62376e, 20261022-092133-102-30c830, 20261023-095410-102-a70475, 20261025-101415-102-24b251

### outcome
20260802-090223-102-4bc16f, 20260805-140319-102-48378d, 20260805-160713-102-e3b915, 20260807-091656-102-7ec3cb, 20260808-132235-102-5960ba, 20260815-114328-102-8d11be, 20260816-090749-102-535cbb, 20260820-092011-102-ed283b, 20260820-133507-102-37388c, 20260821-135623-102-771d5f, 20260827-124644-103-627ca7, 20260828-143622-101-4aea3c, 20260903-144213-101-36cae5, 20260908-162240-102-3aca17, 20260910-150213-102-88cba2, 20260911-105217-102-391d36, 20260911-182447-102-59bd49, 20260911-182711-102-cdb5ff, 20260914-151248-102-726fc9, 20260914-170829-102-9d01aa, 20260916-125801-102-43e12c, 20260916-184855-102-0f0d21, 20260919-094919-102-d3125f, 20260923-170254-102-faae1a, 20260923-182346-102-a54117, 20260925-175450-102-2a05d2, 20260927-113903-103-9728a8, 20261006-120226-102-6e9f3f, 20261008-120150-102-e608fd, 20261008-124641-101-88f2e7, 20261009-100458-102-543ce8, 20261010-114841-102-62092d, 20261011-090644-102-8a84f0, 20261014-121257-102-a05566, 20261020-122753-103-831128, 20261020-173302-103-c84a8b, 20261024-115951-102-a98ad1, 20261025-101415-102-24b251, 20261025-150035-102-13f278

### urgency
20260802-090223-102-4bc16f, 20260804-170814-102-1962f5, 20260805-140319-102-48378d, 20260807-092022-103-a65de5, 20260809-094059-102-780e58, 20260813-152129-102-fc8cd5, 20260815-163551-102-72558f, 20260816-090749-102-535cbb, 20260820-092011-102-ed283b, 20260820-173442-102-ced61b, 20260821-190136-102-5db495, 20260824-093958-102-fb63f9, 20260824-162928-102-eafa0e, 20260827-124644-103-627ca7, 20260828-113551-102-7e6364, 20260903-144213-101-36cae5, 20260909-093057-102-157b36, 20260909-164308-101-b46f60, 20260915-121948-102-64072c, 20260915-145238-102-9a2a7a, 20260916-125801-102-43e12c, 20260916-184855-102-0f0d21, 20260917-171038-102-c37394, 20260919-112153-102-36a7c6, 20260920-130038-102-204bff, 20260923-161018-102-f9498b, 20260923-170254-102-faae1a, 20260927-113903-103-9728a8, 20260928-151423-102-d50642, 20261005-095533-102-f94a74, 20261006-120226-102-6e9f3f, 20261011-090644-102-8a84f0, 20261016-100217-102-a4ee6e, 20261016-164416-102-d768ff, 20261022-092133-102-30c830, 20261025-101415-102-24b251, 20261025-150035-102-13f278

### red_flags
20260805-140319-102-48378d, 20260816-090749-102-535cbb, 20260821-190136-102-5db495, 20260915-145238-102-9a2a7a, 20260916-184855-102-0f0d21, 20260917-171038-102-c37394, 20260923-182346-102-a54117, 20261022-092133-102-30c830, 20261025-101415-102-24b251

### promised_followup
20260802-090223-102-4bc16f, 20260805-140319-102-48378d, 20260820-092011-102-ed283b, 20260821-135623-102-771d5f, 20260828-143622-101-4aea3c, 20260903-144213-101-36cae5, 20260921-143847-101-97dc8a, 20261010-114841-102-62092d, 20261016-100217-102-a4ee6e

### followup_text
20260802-090223-102-4bc16f, 20260805-140319-102-48378d, 20260820-092011-102-ed283b, 20260821-135623-102-771d5f, 20260823-160247-102-ed3ed3, 20260828-143622-101-4aea3c, 20260903-144213-101-36cae5, 20260914-103245-102-400ea3, 20260916-130823-102-bedccc, 20260916-142945-102-78ffad, 20260921-143847-101-97dc8a, 20261010-114841-102-62092d, 20261016-100217-102-a4ee6e, 20261018-142543-102-0d505d

### contains_private_content
20260804-100543-102-7e9284, 20260805-140319-102-48378d, 20260805-160713-102-e3b915, 20260806-112243-102-b2b479, 20260809-094059-102-780e58, 20260812-083014-102-bd3ac9, 20260815-130730-101-011593, 20260820-092011-102-ed283b, 20260820-133507-102-37388c, 20260821-135623-102-771d5f, 20260822-093351-102-f06594, 20260827-124644-103-627ca7, 20260828-113551-102-7e6364, 20260828-143622-101-4aea3c, 20260901-122502-103-dee77a, 20260903-144213-101-36cae5, 20260908-162240-102-3aca17, 20260910-150213-102-88cba2, 20260912-152714-102-765d6c, 20260916-125801-102-43e12c, 20260919-094919-102-d3125f, 20260923-161018-102-f9498b, 20260927-113903-103-9728a8, 20261007-163043-102-5505ef, 20261008-124641-101-88f2e7, 20261014-121257-102-a05566, 20261014-145506-103-ce6045, 20261014-164716-101-16265a, 20261017-090528-102-1dd2e4, 20261021-093950-102-b8e799

### language
20260925-175450-102-2a05d2

