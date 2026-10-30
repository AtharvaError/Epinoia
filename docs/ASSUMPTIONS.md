# Assumptions Log

Keep this current — the brief rewards stated assumptions and preserved uncertainty.

| # | Assumption | Why | Risk if wrong |
|---|---|---|---|
| 1 | 2,836 is the working headcount. The gap to ~2,400 is unexplained: phones are all distinct and declared strengths sum to 2,842 | narrative vs pack | over-allocating beds if many are no-shows |
| 2 | Blank gender/dietary = UNKNOWN, needs human resolution | brief rule 8 | wrong housing / safety gap |
| 3 | Bed supply (710 on-campus + 740 external) is the hard ceiling | hostels + external_stays | overbooking |
| 4 | Team assignments are unknown. Pack team IDs are kept as raw values, not treated as real teams | team IDs sit on solo events and are missing on team events | team counts and team clashes are unreliable |
| 5 | Each event's rounds run back to back in one slot, 15 minutes apart | pack gives rounds and duration, no times | real rounds may span days, which changes clashes |
| 6 | Venue facilities are inferred: open ground = outdoor; quiet room = Library Hall or Seminar Hall; stage and lights = Main Stage or Amphitheatre; audio or projector with seating = lecture halls or Seminar Hall; power and tables = Sports Complex or Seminar Hall | pack lists requirements but not venue facilities | events could be placed in unsuitable venues |
| 7 | Venues need 30 minutes between events for setup and teardown | brief lists setup and teardown; pack has no durations | tighter or looser schedule |
| 8 | Food, Merchandise and Registration core members get the Volunteer role in the portal | the app has no matching role | they may need wider access |
| 9 | Participants aged 17 are minors | age field | consent and housing rules may apply |
| 10 | Male and Female participants use `boys` and `girls` rooms. Non-binary and "Prefer not to say" participants never get gendered rooms; they go to external stays, where the property sets the room | pack has no housing preference field, brief rule 2 | some may prefer a gendered room, so a person can override |
| 11 | Only verified facilities count. A room is accessible if `accessibility=yes`; a ground-floor campus room also meets "no stairs". External stays have no accessibility data, so they do not count | brief rule 1 | 237 people are held for a personal arrangement who might fit somewhere unverified |
| 12 | Each person holds one bed for their whole stay. Beds are not reused between people with non-overlapping stays | simplest safe reading; cleaning data is missing | reuse could reduce the 694-bed shortage |
| 13 | External stays are used cheapest first, only if transport is not ruled out, and stop at the limit. The default limit is the simulated Hospitality budget of ₹9L | brief rule 7 | the real budget changes how many people are placed |
| 14 | Minors get campus beds first. Any minor sent off campus is marked for review | safety, brief rule 6 | supervision needs may differ |
| 15 | Passengers need 30 minutes from drop-off to reach an event | no travel data in the pack | affected event lists could be longer or shorter |
| 16 | One helper per 40 people moved between hostels | no staffing data for moves | helper estimate could be off |
| 17 | Events before 09:00 or ending after 21:00 are hard for people staying off campus | brief rule 6 | early and late risks could be over or under-reported |
| 18 | A gap under 30 minutes between events is tight. The pack has no distances between venues | no distance data | transfer risk is only indicative |
| 19 | A festival-wide budget cut is shared in proportion to each department's budget | simplest fair split | the real cut may be targeted |
| 20 | Simulating a hostel closure only rehouses people from that hostel, not those already waiting for a bed | keeps one decision about one change | waiting participants may have a stronger claim on free beds |

> Add a row whenever a decision depends on something the data does not state outright.
