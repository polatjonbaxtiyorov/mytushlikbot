# Lunch Bot

A Telegram bot for managing daily lunch orders, attendance tracking, and balance synchronization with Google Sheets. It supports both regular users and administrators.

---

## Table of Contents

* [Features](#features)
* [Architecture](#architecture)
* [Setup & Installation](#setup--installation)

  * [Prerequisites](#prerequisites)
  * [Configuration](#configuration)
* [Usage](#usage)

  * [User Commands](#user-commands)
  * [Admin Commands](#admin-commands)
* [Data Models](#data-models)
* [Google Sheets Integration](#google-sheets-integration)
* [Scheduling Jobs](#scheduling-jobs)
* [Error Handling & Logging](#error-handling--logging)
* [Testing & Debugging](#testing--debugging)
* [Contributing](#contributing)
* [License](#license)

---

## Features

* **User Registration**: Collects name and phone number.
* **Balance Management**: Shows real-time balance synced from Google Sheets.
* **Attendance & Food Selection**: Daily morning survey, food choice inline menus, cancellation before cutoff.
* **Admin Panel**: User list, role management, set individual daily prices, delete users, manage menus, broadcast notifications, view/update kassa.
* **Google Sheets Sync**:

  * Incremental balance updates.
  * Push back updated balances after lunch deduction.
* **Scheduled Jobs**:

  * 7 AM attendance prompt (Mon–Fri).
  * Midday debt reminders (Mon/Wed/Fri).
  * Daily summary and balance deduction (9 AM weekdays).

---

## Architecture

* **Python 3.12**
* **`python-telegram-bot`** for Telegram API integration
* **Motor** (async MongoDB driver) for data persistence
* **Google `gspread`** with service-account for Sheets
* **AsyncIO** throughout for non-blocking I/O
* **Modular handlers** in `handlers/` for separation of concerns

---

## Setup & Installation

### Prerequisites

* Python 3.10+ installed
* MongoDB Atlas URI or local MongoDB
* Google Service Account JSON with Sheets API access
* Telegram Bot Token from BotFather

### Configuration

1. Clone repository:

   ```bash
   git clone https://github.com/yourorg/lunch-bot.git
   cd lunch-bot
   ```
2. Create a `.env` file in the project root:

   ```ini
   BOT_TOKEN=<your-telegram-bot-token>
   MONGODB_URI=<your-mongodb-uri>
   GOOGLE_CREDENTIALS_JSON=<base64-encoded-service-account-json>
   ```
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```
4. Initialize the database (create collections & defaults):

   ```bash
   python -c "from database import run_init; run_init()"
   ```

---

## Usage

Run the bot:

```bash
python main.py
```

### User Commands

| Command                    | Description                           |
| -------------------------- | ------------------------------------- |
| `/start`                   | Register or show main menu            |
| `/balance`                 | Check your current balance            |
| `/menu`                    | Show today’s food options             |
| `/attendance`              | Show your attendance history          |
| `/history`                 | Show your transaction history         |
| `✏️ Ism o'zgartirish`      | Change your display name              |
| `❌ Tushlikni bekor qilish` | Cancel today’s lunch (before 10 AM)   |
| `🔧 Admin panel`           | (Admins only) Open the admin keyboard |

### Admin Commands

| Button                          | Functionality                                          |
| ------------------------------- | ------------------------------------------------------ |
| **Foydalanuvchilar**            | List all users with balances (syncs incremental)       |
| **Admin Qo'shish/ Admin Olish** | Promote/demote users as admins                         |
| **Kunlik Narx**                 | Set per-user daily meal prices                         |
| **Foydalanuvchini O‘chirish**   | Delete a user and their records                        |
| **Tushlikni Bekor Qilish**      | Cancel lunch for a given date with refund reason       |
| **Karta Ma’lumotlari**          | Update or view payment card info                       |
| **Kassa**                       | Show and sync current cashbox total from Google Sheets |
| **🍽 Menyu**                    | Manage `menu1`/`menu2` items: view/add/delete          |
| **🔔 Notify All**               | Broadcast a message to all non-admin users             |

---

## Data Models

* **User** (`users` collection):

  * `telegram_id` (int, unique)
  * `name` (str)
  * `phone` (str)
  * `balance` (float)
  * `daily_price` (float)
  * `attendance` (list of dates)
  * `declined_days` (list of dates)
  * `transactions` (list of `{date, desc, amount}`)

* **DailyFoodChoices** (`daily_food_choices`):

  * Composite key: `{telegram_id, date}`
  * `food_choice` (str)
  * `user_name` (str)

* **Menu** (`menu`): two documents `menu1` and `menu2` with an `items` array

* **Kassa** (`kassa`): single-doc with today’s `date`, `balance`, and optional `transactions`

* **CardDetails** (`card_details`): single-doc with `card_number`, `card_owner`

---

## Google Sheets Integration

* Uses `get_worksheet()` (async wrapper) to load the sheet.
* **Incremental Sync** before listing users: only updates changed rows.
* **Find User** by scanning `Telegram ID` column for `/balance` checks.
* **Update Sheet** when deducting balances after summary.

---

## Scheduling Jobs

Configured via `Application.job_queue.run_daily`:

| Time (Tashkent) | Job                        | Days        |
| --------------- | -------------------------- | ----------- |
| **07:00**       | `morning_prompt` survey    | Mon–Fri     |
| **12:00**       | `check_debts` reminders    | Mon/Wed/Fri |
| **09:00**       | `send_summary` & deduction | Mon–Fri     |

---

## Error Handling & Logging

* All DB calls are wrapped in `try/except` with `logger.error(...)`.
* Inline menus always delete or replace messages to avoid stale buttons.
* Deprecated `utcnow()` replaced by timezone-aware `datetime.now(timezone.utc)` where needed.

---

## Testing & Debugging

* Use a separate test sheet and test DB for safe experimentation.
* Enable `logging.basicConfig(level=logging.DEBUG)` for verbose logs.
* Simulate callback queries via Telegram’s Bot API testers.

---

## Contributing

1. Fork and branch: `git checkout -b feature/YourFeature`
2. Code, then lint: `flake8 .`
3. PR with clear description.