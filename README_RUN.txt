Edel Runway Bot Multi Account + Integrated UI

Run default UI:
  python3 runway_bot.py

Run plain log mode:
  python3 runway_bot.py --plain

Setup:
  python3 -m pip install -r requirements.txt
  cp .env.example .env

accounts.json format simpel 3 baris per akun, skip cookie gapapa nanti masukin di bot tele :
  acc1
  akun1@gmail.com
  eyJ...

  acc2
  akun2@gmail.com
  eyJ...

Telegram cookie update:
  @acc1 eyJ...
  @acc2 edel_session=eyJ...

UI keys:
  S = start
  X = stop
  R = restart
  A = all logs
  C = clear active log
  Q = quit
