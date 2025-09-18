# Ball Knowledge

Daily NFL player guessing game. Start with one season stat line; each wrong guess reveals another line. Fewer reveals = higher score.

## Run locally (Windows)
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
