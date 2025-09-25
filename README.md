# Ball Knowledge

Play here ----> https://ball-knowledge-ae9r.onrender.com
Daily NFL player guessing game. Start with one season stat line; each wrong guess reveals another line. Fewer reveals = higher score.
Timer mode in which players have 2 minutes to guess as many players correct as possible using hints and skips which both can lower your score

## Run locally (Windows)
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
