# AI Disclosure

Required by the Epinoia brief: one slide on what AI did, what the team decided, and how
outputs were checked. Review this before presenting, and adjust it to match what your
team actually did.

## What AI did

Claude Code, Anthropic's coding assistant, wrote most of the code under the team's
direction. That covers the backend, database schema, frontend, Ripple Simulator, tests,
deployment files and documentation. It also profiled the data pack and proposed the
schedule planner and allocation rules.

The product has an optional assistant that uses Claude to answer questions. Without an
API key it runs offline, and it cannot change data either way.

## What the team decided

- The product: FestOS Command, with the Ripple Simulator as its core.
- The stack: FastAPI, PostgreSQL, React.
- Local sign-in now, with Firebase left for later.
- Using the real data pack, with clearly labelled simulated records only where the pack
  has no data.
- The visual direction, based on a reference site the team chose.
- The order of work and which gaps to accept.

Add any decisions your team made that are not listed here.

## How outputs were checked

- 169 automated backend tests, in their own database. Expected figures are recalculated
  from the raw CSV files, not copied from the app.
- A test walks the full 14-step workflow from an empty festival to the audit log.
- Security tests list every API route from the API itself, and confirm each one needs a
  session, enforces roles, and blocks access across festivals.
- Tests confirm a preview changes no records, an out-of-date preview cannot be applied,
  and a failed commit leaves nothing behind.
- The AI also ran the main flows in a real browser, which found problems tests missed,
  including a crash and layouts that broke on tablets. Have your team try it too.
- axe-core accessibility checks on every page at tablet and phone width, and again in
  both themes on desktop.
- A load test with 20 concurrent users, and a backup restored into a separate database
  and compared table by table.
- Dependencies checked against known vulnerability databases.

## What was not verified

- The live Claude API path has never run against the real service. Its logic was tested
  with a simulated client.
- Camera check-in was tested with a simulated video feed showing real pass QR codes, not
  on a physical phone. Try it on the phones you will use, in the light you will have.
- Simulated records are invented, so results built on them show how the system behaves,
  not real festival outcomes.
- The schedule is generated, and 2,024 registrations still fall outside participants' stays.
