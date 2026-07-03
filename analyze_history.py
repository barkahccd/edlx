#!/usr/bin/env python3
"""
analyze_history.py — Hitung win-rate per ticker dari history.jsonl

Jalankan kapan saja untuk lihat statistik:
    python3 analyze_history.py
"""

import json
from pathlib import Path
from collections import defaultdict

HISTORY_FILE = Path(__file__).parent / "history.jsonl"


def load_records():
    if not HISTORY_FILE.exists():
        return []

    records = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def main():
    records = load_records()

    if not records:
        print("Belum ada data di history.jsonl. Jalankan bot dulu sampai minimal 1 round settled.")
        return

    settled = [r for r in records if r.get("settled")]
    pending = [r for r in records if not r.get("settled")]

    print(f"Total round tercatat : {len(records)}")
    print(f"Sudah settled         : {len(settled)}")
    print(f"Masih pending         : {len(pending)}")
    print()

    if not settled:
        print("Belum ada round yang settled. Tunggu sampai minimal 1 round selesai.")
        return

    # Hitung win/loss per ticker yang dipilih
    stats = defaultdict(lambda: {"win": 0, "loss": 0, "unknown": 0})
    unknown_total = 0
    known_total = 0

    for record in settled:
        for pick in record.get("picks", []):
            picked = pick.get("picked", "?")
            result = pick.get("result", "unknown")

            if result == "win":
                stats[picked]["win"] += 1
                known_total += 1
            elif result == "loss":
                stats[picked]["loss"] += 1
                known_total += 1
            else:
                stats[picked]["unknown"] += 1
                unknown_total += 1

    if known_total == 0:
        print("⚠️  Semua hasil settlement masih 'unknown' — field hasil dari API belum dikenali.")
        print("    Bot belum bisa otomatis tahu menang/kalah. Perlu cek struktur response API")
        print("    saat status round = SETTLED, untuk tahu nama field yang benar.")
        print()
        print(f"    Total picks tercatat (semua unknown): {unknown_total}")
        return

    print(f"Picks dengan hasil diketahui : {known_total}")
    if unknown_total:
        print(f"Picks dengan hasil unknown   : {unknown_total}  (field API belum lengkap dikenali)")
    print()

    # Sort by win rate descending, minimal 1 game
    rows = []
    for ticker, s in stats.items():
        total = s["win"] + s["loss"]
        if total == 0:
            continue
        win_rate = s["win"] / total * 100
        rows.append((ticker, s["win"], s["loss"], total, win_rate))

    rows.sort(key=lambda r: (-r[4], -r[3]))  # win rate desc, lalu jumlah game desc

    print(f"{'Ticker':<8} {'Win':>5} {'Loss':>5} {'Total':>6} {'Win Rate':>9}")
    print("─" * 40)
    for ticker, win, loss, total, rate in rows:
        print(f"{ticker:<8} {win:>5} {loss:>5} {total:>6} {rate:>8.1f}%")

    print()
    if rows:
        best = rows[0]
        worst = rows[-1]
        print(f"🏆 Win rate tertinggi : {best[0]} ({best[4]:.1f}%, {best[3]} games)")
        print(f"📉 Win rate terendah  : {worst[0]} ({worst[4]:.1f}%, {worst[3]} games)")

    # Saran: kalau sample masih kecil, ingatkan
    small_sample = [r for r in rows if r[3] < 5]
    if small_sample:
        print()
        print(f"⚠️  {len(small_sample)} ticker punya kurang dari 5 sampel — winrate-nya belum reliable secara statistik.")
        print("    Butuh lebih banyak round sebelum dipakai sebagai basis strategi.")


if __name__ == "__main__":
    main()
