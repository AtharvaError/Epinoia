# Data Findings — festos_dataset_pack

Profiled 30 Oct 2026. Fest window ~30 Oct–1 Nov 2026. Row counts all match the brief.

## Referential integrity (clean — surprisingly)
- `participant_id` unique (0 duplicate IDs).
- Every participant points to a real contingent (0 orphans).
- Every registration row (5,580) resolves to a real participant **and** event; 0 duplicate (participant,event) pairs.
- → IDs join cleanly. Join on `*_id`, never on names. Team data is a separate problem, see below.

## Corrections to the first pass

- **The 2,836 vs ~2,400 gap is unexplained.** All 2,836 phone numbers are distinct and declared contingent strengths sum to 2,842, so the pack is internally consistent at about 2,840. Duplicates do not account for the gap. The narrative figure may simply be out of date. We report it and do not reduce the count.
- **Team data is effectively inverted.** 1,967 solo-event registrations carry a team ID, all 1,810 team-event registrations have none, and 1,743 of 1,854 team IDs have one member. Team assignments for team events are unknown. We keep the raw value, flag it, and do not invent teams.
- **The pack has no event times or venues.** Schedule data has to be generated, and it is labelled that way.
- **Most events exceed every venue.** Events have 148 to 193 registrants. Only the Amphitheatre (250) holds them all; the next largest venues hold 150.
- **Stay dates limit attendance.** Arrivals run across 29 to 31 Oct and departures from 30 Oct to 3 Nov, so many registrants are not on campus for every event. The planner scores this, and the dashboard reports what remains. The only arrivals dated 2 Nov are the three reversed-date rows.
- **The 515 accessibility figure was too broad for beds.** 380 of those participants requested accommodation, so the bed comparison is 380 needs against 26 accessible beds.
- **Pack venue requirements do not always suit the event name.** Badminton asks for a quiet room and Table Tennis for audio and seating. We follow the data, so some placements look odd.
- **279 participants are 17.** Treat as minors for consent and housing until told otherwise.
- **One phone number uses a different format** (`99999 99999`).

## The real traps

### 1. "Duplicate-looking" ≠ duplicates
- 364 name+college groups repeat (e.g. "Meera Bose", C02 appears at P0007, P0017, P0027… every 10th ID, each with a **different arrival time**).
- Distinct IDs, distinct arrivals → synthetic **name-pool reuse**, not double registrations.
- **Do NOT merge on name.** Flag collisions for human review only; `participant_id` is the identity.

### 2. Capacity pressure (the headline conflict)
- Beds: 710 hostel + 740 external = **1,450**. Accommodation requested: **2,144**. → **~694-bed shortage.** Must be exposed, not optimized away.
- Accessible rooms: **only 10** of 200 hostel rooms (`accessibility=yes`) vs **~515** participants with a non-"None" accessibility need. Hard-constraint crunch.
- Hostels gender = `boys`/`girls` only — **no non-binary category** (415 participants Non-binary, 430 "Prefer not to say"). Brief rule: don't force them into a binary box.

### 3. Blanks everywhere — never default them
- `gender_as_given`: 417 blank (14%) + 430 "Prefer not to say" + 415 Non-binary.
- `dietary_needs`: 394 blank (13%).
- Blank ≠ consent / gender / clearance / "no need". → explicit `UNKNOWN`, human resolves.

### 4. Declared vs actual
- 7/38 contingents: `strength_declared` ≠ actual participant count (e.g. C05 declared 107/actual 99; C27 declared 45/actual 52). Trust the headcount, flag the gap.

### 5. Timing anomalies
- 3 participants: `departure_datetime` before `arrival_datetime`. All dates otherwise parse and sit in-window.

### 6. Vendors — compliance gaps
- **8 of 14** hygiene certs expire on/before the fest: VND05/08/11/13 already expired (2026-09-29); VND01/02/04/07 expire mid-fest (2026-10-31).
- Dietary coverage thin: vegan=1, jain=1, gluten-free=1 vendor only.

### 7. Logistics — half unusable
- Condition: good=35, under repair=22, needs check=15. → only ~49% of 72 lines are deployable. Availability must reflect condition.

### 8. Merch
- 10 SKUs, `reserved_quantity` ≤ `quantity` for all (no oversell yet). Enforce single-issue at claim time (live-test risk).

## Label inconsistencies to normalize on import
- `accommodation_requested`: yes/no (clean here, but validate).
- `dietary_options` (vendors) and `equipment_needs`/`venue_requirements` (events) are `;`- or `|`-delimited multi-values → split.
- Gender casing, dietary tokens → canonical set, keep original as `_as_given`.
