# Deriv Trading Bot

High-frequency grid trading bot for Deriv with multi-user support and Supabase authentication.

## Features

- ✅ Grid trading strategy with configurable parameters
- ✅ Multi-user support with session management
- ✅ Supabase authentication and API token storage
- ✅ Real-time WebSocket connection to Deriv API
- ✅ Risk management (max runtime, drawdown limits)
- ✅ Responsive dark/light mode UI
- ✅ Mobile-friendly dashboard

## Quick Start

### Prerequisites

- Python 3.8+
- Deriv API token ([Get yours here](https://app.deriv.com/account/api-token))
- Supabase account (optional, for multi-user setup)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/anthonyy616/deriv-bot.git
cd deriv-bot
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment:
```bash
cp .env.example .env
# Edit .env with your credentials
```

4. (Optional) Set up Supabase:
- Create a Supabase project
- Run `supabase_schema.sql` in the SQL editor
- Update `.env` with your Supabase URL and key

### Running Locally

```bash
python main.py
```

Access the dashboard at `http://localhost:8000`

## Deployment

### Backend (Railway/Render/DigitalOcean)
1. Deploy the Python backend to your preferred platform
2. Set environment variables (SUPABASE_URL, SUPABASE_KEY)
3. Note the deployed backend URL

### Frontend (Vercel)
1. Update line 414 in `static/index.html` with your backend URL
2. Deploy to Vercel
3. Users can access at your Vercel URL

## Configuration

All bot parameters are configurable via the web UI:
- **Spread**: Distance between grid levels
- **Max Positions**: Maximum concurrent positions
- **Max Runtime**: Auto-stop after N minutes (0 = infinite)
- **Max Drawdown**: Auto-stop if loss exceeds $N (0 = disabled)

## Architecture

- **Backend**: FastAPI + Deriv WebSocket API
- **Frontend**: Vanilla JS + TailwindCSS
- **Database**: Supabase (PostgreSQL)
- **Auth**: Session-based with Supabase storage

## License

MIT

## Disclaimer

Trading involves risk. Use at your own discretion. This bot is for educational purposes.
