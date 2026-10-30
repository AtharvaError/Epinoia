# Test Credentials & Setup (required deliverable)

> Fill in once auth + seed exist. Synthetic data only.

## Setup
```bash
npm install
npx prisma migrate dev
npm run seed      # loads /data/raw CSVs + seeds users
npm run dev
```

## Test logins (one per role)
| Role | Email | Password |
|---|---|---|
| director | director@festos.test | <seeded> |
| lead | lead@festos.test | <seeded> |
| volunteer | volunteer@festos.test | <seeded> |
| readonly | readonly@festos.test | <seeded> |
