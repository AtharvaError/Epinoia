# UI2 design contract

## The job

Someone is holding a radio and something has just gone wrong. FestOS has to answer three
questions in order: what needs me, what will this break, and what do we do.

## Structure

The app is organised by what the person is doing, not by department.

| Workspace | Question it answers | Pages |
|---|---|---|
| Now | What needs me? | Decisions, Trends, Check-in, Safety, Messages |
| Respond | What will this break? | Report a disruption, History, Audit log |
| Plan | How is it set up? | Schedule, People, Stays, Transport, Crew, Venues and kit, Food |
| Review | How did it go? | Money, Reports, Settings |

**Report a disruption** is on every screen. All seven disruptions in the brief start there.

## Pages

| Page | Pattern | Rule |
|---|---|---|
| Decisions | Split pane | Work the queue without leaving the page. Figures stay small |
| Trends, Reports | Analytics | Charts live here, not on Decisions |
| Report a disruption | Form, then detail | One step at a time: Report, See the impact, Decide |
| Plan pages | List | Search, table, then a slide-over for one record |
| A participant | Detail | Their festival in time order, not a list of fields |
| Check-in | Form | One-handed on a phone |

## Look

A printed run sheet, not a dark dashboard.

- **Paper and ink.** Warm paper, near-black ink, hairline rules and tabular numbers.
- **One signal colour.** Marigold, the festival flower, marks the most important action on
  screen and nothing else.
- **Red means critical.** Severity colours are never used for decoration.
- **Square corners, no shadows on the page.** Depth is only for things that float.
- **Bricolage Grotesque for headlines, Instrument Sans for everything else.**

## Rejected

Sidebars of modules, KPI walls, hero bands, gradients, glass, purple accents, decorative
pills around static text, and charts on the page where decisions are made.

## Modes

Day is the default because it reads in sunlight. Night is for the control room.
