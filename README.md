# Gensee.ai Auto Signup

Otomatis daftar akun Gensee.ai pake referral code

Syarat biar akun refferal kahitung itu harus buat instance dan tinggal nunggu selama kurang lebih 30 menit. Script ini udah pakai auto create instance juga di register.py

Direkomendasikan pakai residential proxy supaya per register bisa beda IP

## Cara Install
```bash
git clone https://github.com/zulemkeren/GeneeAutoRegister.git
cd GeneeAutoRegister
```
```bash
pip install -r requirements.txt
```
```bash
playwright install chromium
```
```bash
cp .env.example .env
```
```bash
nano .env
```
```bash
python -m venv venv
source venv/Scripts/activate
```

Edit `.env`, isi 3 hal:
- `TWOCAPTCHA_API_KEY` — daftar di https://2captcha.com, top up min $1, copy API key
- `REFERRAL_CODE` — kode referral yang mau dipake (default `W3EWKVAJ`)
- `PROXY_*` — opsional, kalo punya proxy. Kalo gak punya, **comment-out aja semua line `PROXY_`**

## Jalanin Auto Register

```bash
python register.py          # daftar akun + bikin instance (~4-5 menit)
python check_status.py      # aktivasi Premium+ trial + cek status (ini gaperlu ya, udah otomatis di register.py)
```

`register.py` udah include create instance otomatis di akhir flow — pake browser session yang sama (1 IP, hemat 1 captcha solve jika pakai proxy). Akun baru di-**append** ke `accounts.json` dengan nomor urut, gak overwrite akun sebelumnya.

Format `accounts.json` clean & simple:

```json
[
  {
    "no": 1,
    "Email": "abc123xyz@example.com",
    "Mail pass": "randompass1234ab",
    "Name": "John Doe",
    "Referral": "W3EWKVAJ",
    "Created": "2026-04-26 10:30:59",
    "Status": "SUCCESS"
  }
]
```

Token / sandbox / proxy session disimpan terpisah di `_meta.json` (script-script lain butuh ini, tapi gak perlu dilihat manual).

Liat semua akun di terminal:

```bash
python account_store.py
# === Account #1 ===
#   Email     : abc123xyz@example.com
#   Mail pass : randompass1234ab
#   Name      : John Doe
#   Referral  : W3EWKVAJ
#   Created   : 2026-04-26 10:30:59
#   Status    : SUCCESS
```

`check_status.py` / `create_instance.py` / `verify_login.py` otomatis pake akun **terakhir**.

> Kalau cuma mau bikin instance baru di akun yang udah ada (mis. instance lama di-pause), pake `python create_instance.py`.

## Nama akun

Nama first/last name digenerate pake library **Faker** — hasilnya nama Amerika realistis (mis. `Kristin Gillespie`, `David Fitzgerald`, `Michelle Garcia`) yang cocok buat US proxy. Gender-consistent (Michelle → female first name).

Mau ganti locale? Tambah ke `.env`:
```env
NAME_LOCALE=id_ID    # Indonesia
NAME_LOCALE=en_GB    # UK
NAME_LOCALE=ja_JP    # Jepang
# default: en_US
```

## Tanpa proxy

Comment-out `PROXY_HOST` / `PROXY_USER` / `PROXY_PASS` di `.env`. Script jalan langsung dari IP lo. Aman buat 1-2 akun, tapi jangan banyak-banyak (kena flag).

## Pake proxy

Isi `PROXY_HOST/USER/PASS` di `.env`. Tiap `python register.py` otomatis pake **IP residential beda** (sticky session random per run). Sesudah signup, semua script (`create_instance.py`, dst) otomatis pake IP yang sama kayak waktu daftar.

## File apa aja

| File | Fungsi |
|---|---|
| `register.py` | Daftar akun + bikin instance (1 flow, 1 IP) |
| `create_instance.py` | Bikin instance buat akun yang udah ada |
| `check_status.py` | Aktivasi trial + cek status referral |
| `verify_login.py` | Test login (optional) |
| `.env` | Config lo (API key + proxy) |
| `accounts.json` | List semua akun (clean, cuma 6 field penting) |
| `_meta.json` | Token / sandbox / proxy session per akun (untuk script) |
| `account_store.py` | Helper baca/tulis accounts.json (run buat liat list) |

## Cek saldo 2captcha

```bash
python -c "from twocaptcha import TwoCaptcha; import os; from dotenv import load_dotenv; load_dotenv(); print('Saldo: $', TwoCaptcha(os.environ['TWOCAPTCHA_API_KEY']).balance())"
```

1 captcha solve ~$0.003. Saldo $1 cukup buat ~300 signup.

## Kalo error

- **`Page.goto: Timeout`** → proxy lambat / mati. Test: `python proxy_helper.py`
- **`Invalid verification code`** → otp salah extract. Lapor ke gw, dump email-nya
- **`reCAPTCHA solve FAILED`** → saldo 2captcha abis atau worker mereka penuh
- **`max_active_instances: 1`** → akun cuma boleh 1 instance, pause dulu yg lama
