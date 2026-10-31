# FestOS Command — External Stay Suggestions

An internal module for the **Aarohan 2026** core organizing team at **IIM Indore** to recommend verified and nearby accommodation options to participants seeking external stay outside the campus hostels.

---

## 1. Purpose & Separation of Concerns

- **"Add Hostel"**: Managed partner properties booked by the festival committee and counted directly towards the campus bed budget and bed inventory.
- **"Stay Suggestions"**: Informal, helpful recommendations of external commercial hotels/hostels around Indore.
  - **No booking is made.**
  - **No beds are counted or allocated.**
  - **Zero impact on festival budget, occupancy rates, or capacity gauges.**
  - Stored strictly in isolated tables (`external_hotels` and `stay_suggestion_logs`).

---

## 2. Architecture & Data Model

### Database Tables

#### `external_hotels`
Stores hotels located within the festival campus vicinity.
- `id` (Integer PK)
- `festival_id` (FK to `festivals.id`)
- `name` (String, required)
- `address`, `area` (Text, String)
- `latitude`, `longitude` (Float, required for map rendering and Telegram `sendVenue`)
- `phone`, `alternate_phone` (String, tap-to-call enabled in UI)
- `price_min`, `price_max` (Integer INR/night)
- `rating` (Numeric 0.0 - 5.0)
- `notes` (Internal notes on curfew, amenities, etc.)
- `google_maps_link` (Direct navigation link)
- `is_verified` (Boolean: true if core team verified prices and phone)
- `verified_by`, `verified_at` (Audit trail of volunteer verification)
- `is_active` (Boolean soft deletion / visibility toggle)
- `created_at`, `updated_at` (Timestamps)

#### `stay_suggestion_logs`
Full audit trail of every suggestion dispatched.
- `id` (Integer PK)
- `festival_id` (FK to `festivals.id`)
- `sender_id` (FK to `users.id`)
- `participant_id` (FK to `participants.id`)
- `hotel_ids` (JSON array of hotel IDs sent)
- `message_text` (Snapshot of sent or copied message)
- `channel` (`"telegram"` or `"copied_fallback"`)
- `delivery_status` (`"sent"`, `"failed"`, `"no_telegram"`, `"copied"`)
- `error_message` (Detailed Telegram API error if failed)
- `created_at` (Dispatch timestamp)

---

## 3. Screen Layout & User Experience

Access: On the **Accommodation** page, click the **Stay suggestions** button in the header toolbar:
`Contingent sheet | Stay suggestions | Add room | Add hostel | Preview plan`

### Key UI Capabilities:
1. **Top Bar**:
   - Real-time search by hotel name or area.
   - Radius quick selector: **5 km**, **10 km** (default), **15 km**, **25 km**.
   - Filters: Verified only, Price range, Minimum rating, Has phone.
   - Actions: Edit Campus Pin coordinates, Add Custom Hotel, Refresh from Places/OSM.
   - Tab switcher: "Hotels & Map" vs "Sent History".
2. **Left Panel (Hotel Cards)**:
   - Sorted by driving distance from IIM Indore campus coordinates.
   - Shows distance in km, estimated car travel time (min), price range per night (`₹X - ₹Y`), rating stars, and verified badge.
   - Tap-to-call mobile buttons for one-touch phone dialing.
   - Checkbox selector (select up to 5 hotels to recommend).
3. **Right Panel (Interactive Map)**:
   - Powered by Leaflet with OpenStreetMap tiles.
   - Fixed anchor pin for IIM Indore campus.
   - Dynamic circle showing selected radius.
   - Interactive hotel pins with status color (Cyan = Selected, Green = Verified, Amber = Unverified).
   - Bi-directional linking: Clicking a card highlights its pin; clicking a pin highlights and scrolls to the card.
   - Rich popup with tap-to-call and Google Maps directions link.
4. **Mobile & Tablet Responsive**:
   - Switches automatically to a List / Map tab toggle on small screens.
   - Full touch friendly layout.
5. **Dark Mode & Light Mode**:
   - Fully styled using FestOS CSS design tokens and theme variables.

---

## 4. Telegram Integration & Participant Dispatch Flow

1. Volunteer selects up to 5 hotels and clicks **"Send to participant"**.
2. **Participant Search Modal**:
   - Search participants by name, college, phone number, or registration code (`P001`).
   - Displays arrival time and Telegram connection status.
   - **Privacy Rule**: Participant gender is **strictly excluded** and never displayed or used in this flow.
3. **Unverified Hotel Warning**:
   - If any chosen hotel is unverified, an explicit warning modal prompts the volunteer to confirm before proceeding: *"Phone and price not confirmed. Are you sure?"*
4. **Editable Preview**:
   - Volunteer can review and personalize the formatted message before dispatching.
   - Includes standard disclaimer: *"Hotel stay is at your own cost and booking. Please confirm availability by calling the hotel."*
5. **Telegram Bot Dispatch**:
   - Sends the formatted overview text message.
   - Dispatches a native Telegram **`sendVenue`** location card for each hotel so the participant gets an interactive map pin directly in their chat.
6. **Fallback for Unconnected Participants**:
   - If participant has not linked or started the Telegram bot, FestOS displays:
     > *"This participant has not started the Telegram bot."*
   - A single-click **"Copy message to clipboard"** button is provided so volunteers can send via WhatsApp or SMS.
   - Copying automatically logs the action in `stay_suggestion_logs` with status `"copied"`.
7. **Participant Profile Badge**:
   - Participants who have received suggestions display a **"Stay suggestions sent"** pill on their profile card and detailed count in their facts panel.

---

## 5. Security, Permissions & Rate Limiting

- **Permission Flag**: Guarded by `can_send_stay_suggestions`. By default, granted to `super_admin`, `festival_director`, and `accommodation_head`.
- **Rate Limit**: Volunteers are rate-limited to a maximum of **10 sends per minute** to prevent spam or accidental double-clicking.
- **Telegram Errors**: Classified and handled gracefully (bot blocked, chat not found, invalid token).

---

## 6. How to Refresh the Hotel List

### Method A: Via FestOS Command UI
1. Open the **Stay suggestions** drawer on the Accommodation page.
2. Click the **"Refresh"** button in the header bar.
3. FestOS queries Google Places API (if `GOOGLE_PLACES_API_KEY` is present in `.env`) or falls back to OpenStreetMap Overpass API, inserting newly discovered properties.

### Method B: Via CLI Script
Run the automated refresh script from the `backend/` directory:

```bash
# Seed or refresh using curated Indore properties + OpenStreetMap
python -m scripts.refresh_hotels --festival-id 1 --radius 10

# Force remote discovery via Google Places / OSM Overpass
python -m scripts.refresh_hotels --festival-id 1 --radius 15 --remote

# Dry run to preview newly found hotels without writing to DB
python -m scripts.refresh_hotels --festival-id 1 --radius 10 --dry-run
```

---

## 7. How to Mark Hotels as Verified

When volunteers call a hotel to verify room rates and phone numbers:

### Method A: Via FestOS Command UI
1. In the Stay Suggestions drawer, locate the hotel card.
2. Click **"Verify"** (or click the edit pencil icon to update phone and price, then check "Mark as Verified").
3. The hotel immediately receives the green **"Verified"** badge and logs the volunteer's identity.

### Method B: Via CLI Script
```bash
# Mark all currently active hotels in the database as verified
python -m scripts.refresh_hotels --festival-id 1 --verify-all
```

---

## 8. Environment Variables

Add the following to `backend/.env` (optional for local testing; fallbacks are enabled):

```env
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"

# Google Places API (Optional - OpenStreetMap is used as free fallback)
GOOGLE_PLACES_API_KEY="AIzaSy..."
```

