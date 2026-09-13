#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天気・防災 LINE通知

データ元(いずれもAPIキー不要・無料):
  - Open-Meteo  https://open-meteo.com/         気象モデル: best_match(日本では気象庁MSM/GSMが自動選択される)
  - 気象庁 防災情報XML(JSON版) https://www.jma.go.jp/bosai/
送信先:
  - LINE Messaging API  https://api.line.me/v2/bot/message/push

使い方:
  python3 tenki_line.py morning     朝の天気サマリーを送る
  python3 tenki_line.py rain        雨の降り出しを検知して送る(降りそうな時だけ)
  python3 tenki_line.py warning     気象警報・注意報の変化を送る(変化した時だけ)
  python3 tenki_line.py auto        上記3つをまとめて判定(GitHub Actions用)
  python3 tenki_line.py test        LINEに疎通テストを1通送る
  python3 tenki_line.py preview     LINEに送らず内容を画面に出す(動作確認用)
  python3 tenki_line.py geocode 名古屋   地名から緯度経度と気象庁エリアコードを検索

送信先は環境変数の有無で自動的に決まる(両方あれば両方に送る):

  [メール] 4つとも設定すると有効になる
    SMTP_USER       送信元のGmailアドレス
    SMTP_PASSWORD   Googleアカウントの「アプリパスワード」(通常のパスワードではない)
    MAIL_TO         受信するメールアドレス
    SMTP_HOST       省略可。既定 smtp.gmail.com
    SMTP_PORT       省略可。既定 587

  [LINE] 2つとも設定すると有効になる
    LINE_CHANNEL_ACCESS_TOKEN   LINE Developersで発行するチャネルアクセストークン
    LINE_USER_ID                自分のLINEユーザーID
"""

import json
import os
import smtplib
import ssl
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

JST = timezone(timedelta(hours=9), "JST")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
def state_path():
    """通知済みの記録の保存先。環境変数 STATE_FILE で切り替えられる。
    天気用と防災用でファイルを分け、2つのワークフローが同じファイルを
    書いてgitで衝突するのを防ぐ。"""
    return os.path.join(BASE_DIR, os.environ.get("STATE_FILE", "state.json"))
USER_AGENT = "tenki-line/1.0 (personal weather notifier)"

# ----------------------------------------------------------------------------
# 気象庁 警報・注意報コード
# 出典: 気象庁 防災情報XMLフォーマット 警報・注意報種別コード表
# ----------------------------------------------------------------------------
WARNING_NAMES = {
    "02": "暴風雪警報", "03": "大雨警報", "04": "洪水警報", "05": "暴風警報",
    "06": "大雪警報", "07": "波浪警報", "08": "高潮警報",
    "10": "大雨注意報", "12": "大雪注意報", "13": "風雪注意報", "14": "雷注意報",
    "15": "強風注意報", "16": "波浪注意報", "17": "融雪注意報", "18": "洪水注意報",
    "19": "高潮注意報", "20": "濃霧注意報", "21": "乾燥注意報", "22": "なだれ注意報",
    "23": "低温注意報", "24": "霜注意報", "25": "着氷注意報", "26": "着雪注意報",
    "27": "その他の注意報",
    "32": "暴風雪特別警報", "33": "大雨特別警報", "35": "暴風特別警報",
    "36": "大雪特別警報", "37": "波浪特別警報", "38": "高潮特別警報",
}
EMERGENCY_CODES = {"32", "33", "35", "36", "37", "38"}
WARNING_CODES = {"02", "03", "04", "05", "06", "07", "08"}

# ----------------------------------------------------------------------------
# WMO天気コード -> 日本語  出典: https://open-meteo.com/en/docs (WMO Weather interpretation codes)
# ----------------------------------------------------------------------------
WMO = {
    0: ("快晴", "☀️"), 1: ("晴れ", "🌤"), 2: ("晴れ時々くもり", "⛅️"), 3: ("くもり", "☁️"),
    45: ("霧", "🌫"), 48: ("霧(着氷)", "🌫"),
    51: ("弱い霧雨", "🌦"), 53: ("霧雨", "🌦"), 55: ("強い霧雨", "🌧"),
    56: ("着氷性の霧雨", "🌧"), 57: ("強い着氷性の霧雨", "🌧"),
    61: ("弱い雨", "🌦"), 63: ("雨", "🌧"), 65: ("強い雨", "🌧"),
    66: ("着氷性の雨", "🌧"), 67: ("強い着氷性の雨", "🌧"),
    71: ("弱い雪", "🌨"), 73: ("雪", "🌨"), 75: ("大雪", "❄️"), 77: ("霧雪", "🌨"),
    80: ("にわか雨", "🌦"), 81: ("強いにわか雨", "🌧"), 82: ("激しいにわか雨", "⛈"),
    85: ("にわか雪", "🌨"), 86: ("強いにわか雪", "❄️"),
    95: ("雷雨", "⛈"), 96: ("雷雨(ひょう)", "⛈"), 99: ("激しい雷雨(ひょう)", "⛈"),
}
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


WIND_DIRS = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
             "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]


def wind_dir_ja(deg):
    if deg is None:
        return ""
    return WIND_DIRS[int((deg % 360) / 22.5 + 0.5) % 16]


def wind_note(speed_max, gust_max):
    """風の強さの一言。単位は km/h で返ってくるので m/s に直す。"""
    if speed_max is None:
        return None
    g = (gust_max or 0) / 3.6
    if g >= 20:
        return "🌪 非常に強い風。外出時は注意"
    if g >= 14:
        return "💨 風が強めです。傘が壊れるかも"
    return None


def uv_note(uv):
    if uv is None:
        return None
    if uv >= 8:
        return "🕶 UV指数 %.0f（非常に強い）日焼け対策を" % uv
    if uv >= 6:
        return "🕶 UV指数 %.0f（強い）日中は日陰を" % uv
    if uv >= 3:
        return "🕶 UV指数 %.0f（中程度）" % uv
    return None


def pm25_note(v):
    if v is None:
        return None
    if v >= 70:
        return "😷 PM2.5 %.0f（非常に多い）外出を控えめに" % v
    if v >= 35:
        return "😷 PM2.5 %.0f（多い）敏感な方は注意" % v
    return None


INTENSITY_ORDER = ["1", "2", "3", "4", "5-", "5+", "6-", "6+", "7"]


def intensity_rank(v):
    """震度文字列を順位に。不明なら -1。"""
    try:
        return INTENSITY_ORDER.index(str(v))
    except ValueError:
        return -1


def intensity_advice(v):
    r = intensity_rank(v)
    if r >= 6:      # 6- 以上
        return "🚨 立っていられない揺れです。身の安全を最優先に。余震に警戒してください"
    if r >= 4:      # 5- 以上
        return "⚠️ 家具の転倒に注意。落下物とガラスに気をつけてください"
    if r >= 3:      # 4
        return "地震です。落下物に注意してください"
    return None


def discomfort_index(temp, hum):
    """不快指数 = 0.81T + 0.01H(0.99T - 14.3) + 46.3
    出典: 一般的に用いられる Thom の不快指数(日本での慣用式)。"""
    if temp is None or hum is None:
        return None
    return 0.81 * temp + 0.01 * hum * (0.99 * temp - 14.3) + 46.3


def discomfort_note(di):
    if di is None:
        return None
    if di >= 85:
        return "🥵 不快指数 %.0f（暑くてたまらない）冷房を使ってください" % di
    if di >= 80:
        return "😓 不快指数 %.0f（汗が出る蒸し暑さ）" % di
    if di >= 75:
        return "💧 不快指数 %.0f（やや蒸し暑い）" % di
    if di < 55:
        return "🧊 不快指数 %.0f（肌寒い）" % di
    return None


def wbgt_note(w):
    """環境省の指針。31以上=危険 / 28-31=厳重警戒 / 25-28=警戒 / 21-25=注意。
    出典: 環境省 熱中症予防情報サイト https://www.wbgt.env.go.jp/wbgt.php"""
    if w is None:
        return None
    if w >= 31:
        return "🚨 暑さ指数 %.0f【危険】外出は避け、涼しい室内へ" % w
    if w >= 28:
        return "🔥 暑さ指数 %.0f【厳重警戒】外出時は炎天下を避けて" % w
    if w >= 25:
        return "🥵 暑さ指数 %.0f【警戒】運動時はこまめに休憩を" % w
    if w >= 21:
        return "💧 暑さ指数 %.0f【注意】水分補給を忘れずに" % w
    return None


def laundry_note(psum, pop, hum_avg, tmax, wind):
    """洗濯物の乾きやすさ。降水・湿度・気温・風から判定する自作指標。"""
    if pop is None:
        return None
    if (psum or 0) >= 1 or pop >= 50:
        return "🧺 洗濯は室内干しがおすすめ"
    score = 0
    if hum_avg is not None:
        score += 2 if hum_avg < 55 else (1 if hum_avg < 70 else 0)
    if tmax is not None:
        score += 2 if tmax >= 25 else (1 if tmax >= 18 else 0)
    if wind is not None:
        score += 1 if wind / 3.6 >= 2 else 0
    if score >= 4:
        return "🧺 洗濯日和。厚手もよく乾きます"
    if score >= 2:
        return "🧺 洗濯物はまずまず乾きます"
    return "🧺 洗濯物は乾きにくい日です"


def pressure_note(hourly, now):
    """気圧の急降下は荒天の兆候であり、頭痛(気象病)の引き金にもなる。
    これから12時間で6hPa以上下がる見込みなら知らせる。"""
    try:
        times = [parse_local(t) for t in hourly["time"]]
        vals = hourly.get("pressure_msl") or []
        win = [(t, v) for t, v in zip(times, vals)
               if v is not None and now <= t <= now + timedelta(hours=12)]
        if len(win) < 4:
            return None
        drop = win[0][1] - min(v for _, v in win)
        if drop >= 10:
            return "🤕 気圧が大きく下がります（-%.0fhPa）体調の変化に注意" % drop
        if drop >= 6:
            return "🤕 気圧が下がり気味です（-%.0fhPa）頭痛などに注意" % drop
    except Exception:
        pass
    return None


def dryness_note(hum_avg, tmax):
    if hum_avg is None:
        return None
    if hum_avg <= 35:
        return "🔥 空気がかなり乾燥。火の元と加湿に注意"
    if hum_avg <= 45 and (tmax or 99) < 20:
        return "🧴 乾燥しています。肌と喉のケアを"
    return None


def avg_humidity_today(hourly, now):
    vals = []
    for t_str, h in zip(hourly["time"], hourly.get("relative_humidity_2m") or []):
        t = parse_local(t_str)
        if t.date() == now.date() and h is not None:
            vals.append(h)
    return sum(vals) / len(vals) if vals else None


def clothing_note(tmax, tmin, app_max):
    """最高気温と体感から服装の目安。"""
    t = app_max if app_max is not None else tmax
    if t is None:
        return None
    if t >= 33:
        return "👕 半袖で。熱中症に注意、こまめに水分を"
    if t >= 28:
        return "👕 半袖が快適です"
    if t >= 23:
        return "👔 長袖1枚でちょうどよい陽気"
    if t >= 17:
        return "🧥 羽織るものがあると安心"
    if t >= 11:
        return "🧥 上着が必要です"
    if t >= 5:
        return "🧣 コートとマフラーを"
    return "🧤 厳しい寒さ。防寒をしっかり"


def temp_diff_note(today_max, yday_max):
    if today_max is None or yday_max is None:
        return ""
    d = today_max - yday_max
    if abs(d) < 2:
        return "（昨日とほぼ同じ）"
    return "（昨日より %+.0f℃）" % d


def rain_intensity_note(mm_per_hour):
    if mm_per_hour >= 50:
        return "非常に激しい雨"
    if mm_per_hour >= 30:
        return "激しい雨"
    if mm_per_hour >= 20:
        return "強い雨"
    if mm_per_hour >= 10:
        return "やや強い雨"
    if mm_per_hour >= 3:
        return "本降り"
    return "弱い雨"


def wmo_text(code):
    return WMO.get(code, ("不明(code %s)" % code, "•"))


# ----------------------------------------------------------------------------
# 汎用
# ----------------------------------------------------------------------------
def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def load_config():
    """config.json を読む。ただし環境変数 LOCATIONS があればそちらの地点を優先する。

    リポジトリをPublicにしても自宅の緯度経度が公開されないよう、地点だけを
    GitHub Secrets に逃がせるようにしてある。LOCATIONS には config.json の
    locations と同じ形のJSON配列を入れる。"""
    if not os.path.exists(CONFIG_PATH):
        die("config.json が見つかりません: %s" % CONFIG_PATH)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    raw = os.environ.get("LOCATIONS", "").strip()
    if raw:
        try:
            locs = json.loads(raw)
            if isinstance(locs, list) and locs:
                cfg["locations"] = locs
            else:
                sys.stderr.write("警告: LOCATIONS が配列でないため config.json を使います\n")
        except ValueError as e:
            sys.stderr.write("警告: LOCATIONS のJSONが不正なため config.json を使います: %s\n" % e)
    return cfg


def load_state():
    sp = state_path()
    if not os.path.exists(sp):
        return {}
    try:
        with open(sp, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def save_state(state):
    with open(state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def die(msg):
    sys.stderr.write("エラー: %s\n" % msg)
    sys.exit(1)


def parse_local(s):
    """Open-Meteoが返す 'YYYY-MM-DDTHH:MM' (現地時刻) を JST aware datetime にする。"""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=JST)


def now_jst():
    return datetime.now(JST)


# ----------------------------------------------------------------------------
# 送信（メール / LINE。設定されているものすべてに送る）
# ----------------------------------------------------------------------------
def env(name, default=""):
    """環境変数を読む。コピペで混入しがちなノーブレークスペース(U+00A0)を
    通常の空白に直してから前後の空白を除去する。"""
    return os.environ.get(name, default).replace("\u00a0", " ").strip()


def clean_password(raw):
    """Gmailのアプリパスワードは画面上「abcd efgh ijkl mnop」と4文字ずつ区切って
    表示され、その空白はノーブレークスペース(U+00A0)であることがある。
    SMTPのAUTHはASCIIしか通せないため、そのまま渡すとUnicodeEncodeErrorになる。

    英数字と空白だけで構成されている場合は空白を全て除去する。
    (空白を含む本物のパスワードを壊さないよう、記号を含む場合はそのまま返す)"""
    if raw and all(c.isalnum() or c.isspace() for c in raw):
        return "".join(raw.split())
    return raw


def email_enabled():
    return bool(env("SMTP_USER") and env("SMTP_PASSWORD") and env("MAIL_TO"))


def line_enabled():
    return bool(env("LINE_CHANNEL_ACCESS_TOKEN") and env("LINE_USER_ID"))


def notify(text, dry_run=False):
    """有効な送信先すべてに送る。1つでも成功すればTrue。"""
    if dry_run:
        print("-" * 48)
        print(text)
        print("-" * 48)
        return True

    if not email_enabled() and not line_enabled():
        die("送信先が設定されていません。\n"
            "  メールを使う場合: SMTP_USER / SMTP_PASSWORD / MAIL_TO\n"
            "  LINEを使う場合:  LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID\n"
            "  詳しくは README.md を参照してください。")

    results = []
    if email_enabled():
        results.append(email_push(text))
    if line_enabled():
        results.append(line_push(text))
    return any(results)


def email_push(text):
    """1行目を件名、全文を本文としてメールを送る。"""
    lines = text.split("\n")
    subject = lines[0].strip() or "天気のお知らせ"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = env("SMTP_USER")
    msg["To"] = env("MAIL_TO")
    msg.set_content(text)

    host = env("SMTP_HOST") or "smtp.gmail.com"
    port = int(env("SMTP_PORT") or "587")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30,
                                  context=ssl.create_default_context()) as sv:
                sv.login(env("SMTP_USER"), clean_password(env("SMTP_PASSWORD")))
                sv.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as sv:
                sv.starttls(context=ssl.create_default_context())
                sv.login(env("SMTP_USER"), clean_password(env("SMTP_PASSWORD")))
                sv.send_message(msg)
        print("メール送信OK: %s" % subject)
        return True
    except smtplib.SMTPAuthenticationError:
        sys.stderr.write(
            "メール送信失敗: 認証エラー。SMTP_PASSWORD には Googleアカウントの\n"
            "「アプリパスワード」(16桁)を設定してください。通常のログインパスワードでは通りません。\n"
            "発行手順: https://myaccount.google.com/apppasswords （2段階認証の有効化が必要）\n")
        return False
    except UnicodeEncodeError:
        sys.stderr.write(
            "メール送信失敗: 認証情報にASCII以外の文字が含まれています。\n"
            "SMTP_USER / SMTP_PASSWORD に全角文字や特殊な空白が混ざっていないか確認してください。\n")
        return False
    except Exception as e:
        sys.stderr.write("メール送信失敗: %s: %s\n" % (type(e).__name__, e))
        return False


def line_push(text):
    token = env("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = env("LINE_USER_ID")
    body = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": text[:4900]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % token,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            res.read()
        print("LINE送信OK (%d文字)" % len(text))
        return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.stderr.write("LINE送信失敗 HTTP %s: %s\n" % (e.code, detail))
        return False
    except urllib.error.URLError as e:
        sys.stderr.write("LINE送信失敗 (通信エラー): %s\n" % e.reason)
        return False


# ----------------------------------------------------------------------------
# 天気取得
# ----------------------------------------------------------------------------
def fetch_forecast(lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "Asia/Tokyo",
        # models は指定しない。Open-Meteoの best_match が日本では気象庁MSM/GSMを自動選択する。
        # models=jma_seamless を明示すると precipitation_probability が返らない(検証済み 2026-09-04)。
        "minutely_15": "precipitation",
        "forecast_minutely_15": 48,          # 15分 x 48 = 12時間先まで
        "hourly": "precipitation,precipitation_probability,temperature_2m,"
                  "apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,pressure_msl,wind_gusts_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "apparent_temperature_max,apparent_temperature_min,"
                 "precipitation_sum,precipitation_hours,precipitation_probability_max,"
                 "wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,"
                 "uv_index_max,sunrise,sunset",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "weather_code,precipitation,wind_speed_10m,pressure_msl",
        "forecast_days": 7,
        "past_days": 1,                      # 前日比を出すため
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    return http_json(url)


def fetch_wbgt(point):
    """環境省 熱中症予防情報サイトの暑さ指数(WBGT)予測値。
    出典: https://www.wbgt.env.go.jp/  地点番号はアメダス番号(例 名古屋=51106)。
    CSVは「1行目=YYYYMMDDHH の並び / 2行目=地点番号,発表時刻,値(10倍)…」。
    暖候期(おおむね4〜10月)のみ提供されるため、取れなければ None を返す。"""
    if not point:
        return None
    url = "https://www.wbgt.env.go.jp/prev15WG/dl/yohou_%s.csv" % point
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as res:
            text = res.read().decode("utf-8", "replace")
        rows = [r for r in text.splitlines() if r.strip()]
        if len(rows) < 2:
            return None
        head = [c.strip() for c in rows[0].split(",")]
        data = [c.strip() for c in rows[1].split(",")]
        today = now_jst().strftime("%Y%m%d")
        vals = []
        for h, v in zip(head[2:], data[2:]):
            if h.startswith(today) and v.isdigit():
                vals.append(int(v) / 10.0)
        return max(vals) if vals else None
    except Exception:
        return None


def fetch_overview(office):
    """気象台が書いた天気概況の本文。台風の見通しなど、数値予報には出ない解説が入る。
    出典: https://www.jma.go.jp/bosai/forecast/data/overview_forecast/{府県コード}.json"""
    if not office:
        return None
    try:
        return http_json(
            "https://www.jma.go.jp/bosai/forecast/data/overview_forecast/%s.json" % office,
            timeout=10)
    except Exception:
        return None


def fetch_quakes():
    """気象庁の地震情報リスト（直近の震源・震度情報）。
    出典: https://www.jma.go.jp/bosai/quake/data/list.json"""
    try:
        return http_json("https://www.jma.go.jp/bosai/quake/data/list.json", timeout=10)
    except Exception:
        return None


def fetch_jma_info():
    """気象庁の各種気象情報（記録的短時間大雨情報・熱中症警戒アラート・竜巻注意情報など）。
    出典: https://www.jma.go.jp/bosai/information/data/information.json"""
    try:
        return http_json("https://www.jma.go.jp/bosai/information/data/information.json",
                         timeout=10)
    except Exception:
        return None


def fetch_air_quality(lat, lon):
    """PM2.5。取れなくても通知は続行する(戻り値 None)。"""
    params = {"latitude": lat, "longitude": lon, "timezone": "Asia/Tokyo",
              "hourly": "pm2_5", "forecast_days": 1}
    url = "https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode(params)
    try:
        return http_json(url, timeout=10)
    except Exception:
        return None


def fetch_warning(office_code):
    url = "https://www.jma.go.jp/bosai/warning/data/warning/%s.json" % office_code
    return http_json(url)


# ----------------------------------------------------------------------------
# 朝の天気サマリー
# ----------------------------------------------------------------------------
def rain_windows(times, precip, threshold, start_from, end_at=None):
    """降水量が閾値以上で連続する区間を [(開始, 終了, 最大mm), ...] で返す。"""
    windows = []
    cur = None
    for t_str, mm in zip(times, precip):
        t = parse_local(t_str)
        if t < start_from:
            continue
        if end_at and t > end_at:
            break
        wet = mm is not None and mm >= threshold
        if wet:
            if cur is None:
                cur = [t, t, mm]
            else:
                cur[1] = t
                cur[2] = max(cur[2], mm)
        elif cur is not None:
            windows.append(tuple(cur))
            cur = None
    if cur is not None:
        windows.append(tuple(cur))
    return windows


def today_index(daily, now):
    """past_days=1 を付けているので daily[0] は昨日。今日の位置を日付で特定する。"""
    key = now.strftime("%Y-%m-%d")
    for i, d in enumerate(daily["time"]):
        if d == key:
            return i
    return 1 if len(daily["time"]) > 1 else 0


def hourly_table(hourly, now, from_hour=6):
    """今日の時間ごとの天気。朝の通知なので from_hour 以降〜23時までを返す。
    すでに過ぎた時刻も含めて1日の流れが見えるようにする。"""
    rows = []
    for i, t_str in enumerate(hourly["time"]):
        t = parse_local(t_str)
        if t.date() != now.date() or t.hour < from_hour:
            continue
        temp = hourly["temperature_2m"][i]
        pop = hourly["precipitation_probability"][i]
        mm = hourly["precipitation"][i]
        code = hourly["weather_code"][i]
        if temp is None:
            continue
        _, icon = wmo_text(code)
        rows.append((t, icon, temp, pop, mm, t < now))
    return rows


def build_morning(loc, fc, cfg, aq=None, wbgt=None, overview=None):
    now = now_jst()
    daily, hourly = fc["daily"], fc["hourly"]
    i = today_index(daily, now)
    threshold = cfg["rain_alert"]["threshold_mm_per_hour"]

    def d(key, idx=None):
        v = daily.get(key)
        return v[i if idx is None else idx] if v else None

    code = d("weather_code")
    desc, emoji = wmo_text(code)
    tmax, tmin = d("temperature_2m_max"), d("temperature_2m_min")
    amax = d("apparent_temperature_max")
    pop, psum = d("precipitation_probability_max"), d("precipitation_sum")
    phours = d("precipitation_hours")
    wspd, wgust = d("wind_speed_10m_max"), d("wind_gusts_10m_max")
    wdir, uv = d("wind_direction_10m_dominant"), d("uv_index_max")
    yday_max = d("temperature_2m_max", i - 1) if i > 0 else None

    L = []
    L.append("%s %d/%d(%s) の天気" % (emoji, now.month, now.day, WEEKDAY_JA[now.weekday()]))
    L.append("📍%s" % loc["name"])

    # ── 今日 ──────────────────────────────────
    L.append("")
    L.append("━━ 今日 ━━")
    L.append(desc)
    if tmax is not None and tmin is not None:
        L.append("🌡 %.0f℃ / %.0f℃%s" % (tmax, tmin, temp_diff_note(tmax, yday_max)))
    if amax is not None:
        cur_hum = fc.get("current", {}).get("relative_humidity_2m")
        hum = "・湿度 %d%%" % cur_hum if cur_hum is not None else ""
        L.append("　体感 %.0f℃%s" % (amax, hum))
    if pop is not None:
        rain_detail = ""
        if psum:
            rain_detail = "／雨量 %.0fmm" % psum
            if phours:
                rain_detail += "・%d時間" % phours
        L.append("☔️ 降水確率 %d%%%s" % (pop, rain_detail))
    if wspd is not None:
        gust = "（最大 %.0fm/s）" % (wgust / 3.6) if wgust else ""
        L.append("💨 %sの風 %.0fm/s%s" % (wind_dir_ja(wdir), wspd / 3.6, gust))
    sr, ss = d("sunrise"), d("sunset")
    if sr and ss:
        L.append("🕐 日の出 %s ／ 日の入り %s" % (sr[11:16], ss[11:16]))

    # ── 時間ごと ──────────────────────────────
    rows = hourly_table(hourly, now)
    if rows:
        L.append("")
        L.append("━━ 時間ごとの天気 ━━")
        for t, icon, temp, pop, mm, past in rows:
            mark = "･" if past else " "
            pop_s = "%3d%%" % pop if pop is not None else "  --"
            mm_s = " %.1fmm" % mm if mm and mm >= 0.1 else ""
            L.append("%s%02d時 %s %2.0f℃ %s%s" % (mark, t.hour, icon, temp, pop_s, mm_s))

    # ── 雨の時間帯 ────────────────────────────
    end_of_day = now.replace(hour=23, minute=59, second=0, microsecond=0)
    wins = rain_windows(hourly["time"], hourly["precipitation"], threshold, now, end_of_day)
    L.append("")
    if wins:
        L.append("━━ 雨の見込み ━━")
        for st, en, mx in wins[:4]:
            L.append("　%s〜%s頃（%s・最大%.1fmm/h）"
                     % (st.strftime("%H:%M"), (en + timedelta(hours=1)).strftime("%H:%M"),
                        rain_intensity_note(mx), mx))
    else:
        L.append("━━ 雨の見込み ━━")
        L.append("　今日これからの雨の予報はありません")

    # ── 週間予報 ──────────────────────────────
    L.append("")
    L.append("━━ この先1週間 ━━")
    for k in range(i, min(i + 7, len(daily["time"]))):
        dt = datetime.strptime(daily["time"][k], "%Y-%m-%d").replace(tzinfo=JST)
        _, ic = wmo_text(daily["weather_code"][k])
        hi, lo = daily["temperature_2m_max"][k], daily["temperature_2m_min"][k]
        pp = daily["precipitation_probability_max"][k]
        tag = "今日" if k == i else ("明日" if k == i + 1 else "%d/%02d" % (dt.month, dt.day))
        L.append("%s(%s) %s %2.0f/%2.0f℃ ☔️%s"
                 % (tag, WEEKDAY_JA[dt.weekday()], ic,
                    hi if hi is not None else 0, lo if lo is not None else 0,
                    ("%d%%" % pp) if pp is not None else "--"))

    # ── 気象台の解説 ──────────────────────────
    # 数値予報には出てこない台風の見通しなどが書かれているので、そのまま載せる。
    if overview and (overview.get("text") or "").strip():
        body = " ".join(overview["text"].split())
        if len(body) > 300:
            body = body[:300] + "…"
        L.append("")
        L.append("━━ %s より ━━" % overview.get("publishingOffice", "気象台"))
        L.append(body)

    # ── 今日のアドバイス ──────────────────────
    hum_avg = avg_humidity_today(hourly, now)
    cur_t = fc.get("current", {}).get("temperature_2m")
    cur_h = fc.get("current", {}).get("relative_humidity_2m")

    tips = []
    # 傘
    if wins or (pop is not None and pop >= 50):
        if psum and psum >= 20:
            tips.append("🌂 傘は必須。折りたたみでは心もとない雨量です")
        else:
            tips.append("🌂 傘を持っていってください")
    elif psum and psum > 0:
        tips.append("🌂 折りたたみ傘があると安心です")
    else:
        tips.append("👍 傘はいりません")

    for f in (clothing_note(tmax, tmin, amax),
              wbgt_note(wbgt),
              discomfort_note(discomfort_index(tmax if tmax is not None else cur_t,
                                               hum_avg if hum_avg is not None else cur_h)),
              wind_note(wspd, wgust),
              uv_note(uv),
              pressure_note(hourly, now),
              laundry_note(psum, pop, hum_avg, tmax, wspd),
              dryness_note(hum_avg, tmax)):
        if f:
            tips.append(f)

    if aq:
        vals = [v for v in aq.get("hourly", {}).get("pm2_5", []) if v is not None]
        note = pm25_note(max(vals)) if vals else None
        if note:
            tips.append(note)
    if tmin is not None and tmin <= 3:
        tips.append("🧊 路面凍結に注意。朝は足元に気をつけて")
    if tmin is not None and tmin >= 25:
        tips.append("😴 熱帯夜です。寝るときも冷房を")

    L.append("")
    L.append("━━ ひとこと ━━")
    L.extend(tips)
    return "\n".join(L)


# ----------------------------------------------------------------------------
# 雨アラート(15分刻み)
# ----------------------------------------------------------------------------
def rain_strength(mm_per_15min):
    mm_h = mm_per_15min * 4
    if mm_h >= 30:
        return "激しい雨"
    if mm_h >= 10:
        return "強い雨"
    if mm_h >= 3:
        return "やや強い雨"
    return "弱い雨"


def build_rain_alert(loc, fc, cfg, state, idx=0):
    """降り出しが近ければ本文を返す。送る必要がなければ None。"""
    now = now_jst()
    ra = cfg["rain_alert"]
    threshold = ra["threshold_mm_per_15min"]
    lookahead = timedelta(minutes=ra["lookahead_minutes"])

    m = fc.get("minutely_15")
    if not m:
        return None
    times = [parse_local(t) for t in m["time"]]
    precip = m["precipitation"]

    # 直近(現在を含む)のスロットで既に降っているか
    raining_now = False
    for t, mm in zip(times, precip):
        if t <= now < t + timedelta(minutes=15):
            raining_now = mm is not None and mm >= threshold
            break
    if raining_now:
        return None  # 降り出しの予告が目的なので、既に降っていれば送らない

    # これから最初に閾値を超えるスロット
    start = None
    for t, mm in zip(times, precip):
        if t <= now:
            continue
        if mm is not None and mm >= threshold:
            start = (t, mm)
            break
    if start is None:
        return None

    start_t, start_mm = start
    if start_t - now > lookahead:
        return None  # まだ先すぎる

    # 静かな時間帯は送らない
    qs, qe = ra["quiet_hours_jst"]
    h = now.hour
    quiet = (qs <= h or h < qe) if qs > qe else (qs <= h < qe)
    if quiet:
        return None

    # 同じ降り出しイベントを繰り返し通知しない
    key = "rain:%d" % idx
    event_id = start_t.strftime("%Y-%m-%dT%H:%M")
    if state.get(key) == event_id:
        return None
    state[key] = event_id

    # 雨がやみそうな時刻(閾値未満が3スロット=45分続いたら終了とみなす)
    stop_t = None
    dry = 0
    for t, mm in zip(times, precip):
        if t < start_t:
            continue
        if mm is None or mm < threshold:
            dry += 1
            if dry >= 3:
                stop_t = t - timedelta(minutes=30)
                break
        else:
            dry = 0

    minutes = int((start_t - now).total_seconds() // 60)
    peak = max((mm for t, mm in zip(times, precip)
                if start_t <= t <= start_t + timedelta(hours=3) and mm is not None),
               default=start_mm)

    # ピーク時刻と雨量の合計
    peak_t, peak_mm, total = start_t, start_mm, 0.0
    for t, mm in zip(times, precip):
        if t < start_t or (stop_t and t > stop_t):
            continue
        if mm is None:
            continue
        total += mm
        if mm > peak_mm:
            peak_mm, peak_t = mm, t

    minutes = int((start_t - now).total_seconds() // 60)
    peak_h = peak_mm * 4
    start_h = start_mm * 4

    lines = []
    lines.append("🌧 まもなく雨です")
    lines.append("📍%s" % loc["name"])
    lines.append("")
    lines.append("あと約%d分（%s頃）から" % (minutes, start_t.strftime("%H:%M")))
    lines.append("降り始め: %s（%.1fmm/h）" % (rain_intensity_note(start_h), start_h))
    if peak_t != start_t and peak_h > start_h * 1.5:
        lines.append("ピーク: %s頃 %s（%.1fmm/h）"
                     % (peak_t.strftime("%H:%M"), rain_intensity_note(peak_h), peak_h))
    if stop_t and stop_t > start_t:
        dur = int((stop_t - start_t).total_seconds() // 60)
        h, m = divmod(dur, 60)
        dur_s = ("%d時間%d分" % (h, m)) if h else ("%d分" % m)
        lines.append("やみそう: %s頃（約%s）" % (stop_t.strftime("%H:%M"), dur_s))
    else:
        lines.append("やみそう: 12時間先まで降り続く見込み")
    if total >= 1:
        lines.append("合計雨量: 約%.0fmm" % total)

    lines.append("")
    if peak_h >= 20:
        lines.append("⚠️ 強い雨です。低い土地の浸水に注意")
    elif peak_h >= 10:
        lines.append("🌂 傘は必須。折りたたみでは心もとない強さです")
    else:
        lines.append("🌂 出かけるなら傘を")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 地震（自分の市区町村で一定の震度を観測したら知らせる）
# ----------------------------------------------------------------------------
def build_quake_alert(loc, cfg, state, idx=0):
    city = loc.get("jma_city")
    if not city:
        return None
    qa = cfg.get("quake_alert", {})
    if not qa.get("enabled", True):
        return None
    min_rank = intensity_rank(qa.get("min_intensity", "3"))
    if min_rank < 0:
        min_rank = 2

    data = fetch_quakes()
    if not data:
        return None

    key = "quake:%d" % idx
    seen = state.get(key) or []
    first_run = key not in state

    hits = []
    for q in data[:40]:                      # 直近40件だけ見る
        eid = q.get("eid")
        if not eid or eid in seen:
            continue
        mine = None
        for pref in (q.get("int") or []):
            for c in (pref.get("city") or []):
                if str(c.get("code")) == str(city):
                    mine = c.get("maxi")
        if mine is None or intensity_rank(mine) < min_rank:
            continue
        hits.append((q, mine))

    # 見た地震は震度に関わらず記録して、次回以降の再判定を避ける
    state[key] = ([q.get("eid") for q in data[:40] if q.get("eid")])[:60]
    if first_run or not hits:
        return None

    q, mine = hits[0]                        # 最新の1件を通知する
    at = q.get("at", "")
    try:
        t = datetime.strptime(at[:16], "%Y-%m-%dT%H:%M").strftime("%m/%d %H:%M")
    except (ValueError, TypeError):
        t = at

    L = ["🌏 地震がありました", "📍%s" % loc["name"], ""]
    L.append("このあたりの震度: %s" % mine)
    L.append("")
    L.append("発生: %s頃" % t)
    L.append("震源: %s" % q.get("anm", "不明"))
    if q.get("mag") not in (None, "", "/"):
        L.append("規模: M%s" % q.get("mag"))
    if q.get("maxi"):
        L.append("最大震度: %s（震源周辺）" % q.get("maxi"))
    adv = intensity_advice(mine)
    if adv:
        L.append("")
        L.append(adv)
    L.append("")
    L.append("━━ 公式情報 ━━")
    L.append("https://www.jma.go.jp/bosai/map.html#contents=earthquake_map")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# 府県気象情報（記録的短時間大雨情報・熱中症警戒アラート・竜巻注意情報など）
# ----------------------------------------------------------------------------
def build_info_alert(loc, state, idx=0):
    office = loc.get("jma_office")
    if not office:
        return None
    data = fetch_jma_info()
    if not data:
        return None

    key = "jmainfo:%d" % idx
    seen = state.get(key) or []
    first_run = key not in state

    mine = []
    for x in data:
        codes = set(x.get("areaCodes") or [])
        if x.get("areaCode"):
            codes.add(x["areaCode"])
        if office not in codes:
            continue
        eid = x.get("eventId") or x.get("jsonName")
        if not eid:
            continue
        mine.append((eid, x))

    state[key] = [e for e, _ in mine][:60]
    if first_run:
        return None

    fresh = [x for e, x in mine if e not in seen]
    if not fresh:
        return None

    x = fresh[0]
    title = x.get("headTitle") or x.get("controlTitle") or "気象情報"
    L = ["📢 %s" % title, "📍%s" % loc["name"], ""]
    rdt = x.get("reportDatetime", "")
    try:
        L.append("発表: %s %s" % (datetime.strptime(rdt[:16], "%Y-%m-%dT%H:%M")
                                 .strftime("%m/%d %H:%M"),
                                 x.get("publishingOffice", "気象庁")))
    except (ValueError, TypeError):
        pass
    L.append("")
    L.append("━━ 詳しくは ━━")
    L.append("https://www.jma.go.jp/bosai/map.html")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# 気候の急変（予報が大きく変わったら知らせる）
# ----------------------------------------------------------------------------
SEVERE_CODES = {
    95: "雷雨", 96: "雷雨(ひょう)", 99: "激しい雷雨(ひょう)",
    82: "激しいにわか雨", 75: "大雪", 86: "強いにわか雪", 65: "強い雨", 67: "強い着氷性の雨",
}


def build_change_alert(loc, fc, cfg, state, idx=0):
    """今日の予報が前回チェック時から大きく変わっていたら知らせる。
    変わっていなければ None。"""
    ca = cfg.get("change_alert", {})
    if not ca.get("enabled", True):
        return None

    now = now_jst()
    qs, qe = ca.get("quiet_hours_jst", [23, 6])
    h = now.hour
    if (qs <= h or h < qe) if qs > qe else (qs <= h < qe):
        return None

    daily = fc["daily"]
    i = today_index(daily, now)

    def g(key):
        v = daily.get(key)
        return v[i] if v and i < len(v) else None

    cur = {
        "date": now.strftime("%Y-%m-%d"),
        "tmax": g("temperature_2m_max"),
        "tmin": g("temperature_2m_min"),
        "pop": g("precipitation_probability_max"),
        "psum": g("precipitation_sum"),
        "code": g("weather_code"),
        "gust": g("wind_gusts_10m_max"),
    }

    key = "forecast:%d" % idx
    prev = state.get(key)
    state[key] = cur
    # 初回、または日付が変わった直後は基準を記録するだけ
    if not prev or prev.get("date") != cur["date"]:
        return None

    changes = []

    d_t = ca.get("temp_diff", 3)
    if cur["tmax"] is not None and prev.get("tmax") is not None:
        diff = cur["tmax"] - prev["tmax"]
        if abs(diff) >= d_t:
            changes.append("・最高気温 %.0f℃ → %.0f℃（%+.0f℃）"
                           % (prev["tmax"], cur["tmax"], diff))

    d_p = ca.get("pop_diff", 30)
    if cur["pop"] is not None and prev.get("pop") is not None:
        diff = cur["pop"] - prev["pop"]
        if diff >= d_p:
            changes.append("・降水確率 %d%% → %d%%（雨の可能性が高まりました）"
                           % (prev["pop"], cur["pop"]))
        elif -diff >= d_p:
            changes.append("・降水確率 %d%% → %d%%（雨の可能性が下がりました）"
                           % (prev["pop"], cur["pop"]))

    if cur["code"] in SEVERE_CODES and prev.get("code") != cur["code"]:
        changes.append("・%sの予報が追加されました" % SEVERE_CODES[cur["code"]])

    g_ms = ca.get("gust_ms", 15)
    if cur["gust"] is not None:
        cur_ms, prev_ms = cur["gust"] / 3.6, (prev.get("gust") or 0) / 3.6
        if cur_ms >= g_ms and prev_ms < g_ms:
            changes.append("・最大瞬間風速 %.0fm/s の強風予報が出ました" % cur_ms)

    if cur["psum"] is not None and prev.get("psum") is not None:
        if cur["psum"] - prev["psum"] >= 20:
            changes.append("・予想雨量 %.0fmm → %.0fmm（大幅に増えました）"
                           % (prev["psum"], cur["psum"]))

    if not changes:
        return None

    desc, emoji = wmo_text(cur["code"])
    L = ["⚡️ 今日の予報が変わりました", "📍%s" % loc["name"], ""]
    L.extend(changes)
    L.append("")
    L.append("━━ 最新の見通し ━━")
    L.append("%s %s" % (emoji, desc))
    if cur["tmax"] is not None and cur["tmin"] is not None:
        L.append("🌡 %.0f℃ / %.0f℃" % (cur["tmax"], cur["tmin"]))
    if cur["pop"] is not None:
        extra = "／雨量 %.0fmm" % cur["psum"] if cur["psum"] else ""
        L.append("☔️ 降水確率 %d%%%s" % (cur["pop"], extra))
    return "\n".join(L)


# ----------------------------------------------------------------------------
# 気象警報・注意報
# ----------------------------------------------------------------------------
def extract_active_warnings(data, city_code):
    """指定した市区町村コードの、発表中の警報・注意報コード集合を返す。"""
    for group in data.get("areaTypes", []):
        for area in group.get("areas", []):
            if area.get("code") != city_code:
                continue
            active = set()
            for w in area.get("warnings", []):
                code = w.get("code")
                status = w.get("status", "")
                if not code:
                    continue
                if status in ("解除", "発表警報・注意報はなし"):
                    continue
                active.add(code)
            return active
    return None  # そのコードが見つからなかった


def sort_key(code):
    return (code_rank(code), code)


def label(code):
    return WARNING_NAMES.get(code, "警報・注意報(code %s)" % code)


LEVEL_RANK = {"emergency": 0, "warning": 1, "advisory": 2}


def code_rank(code):
    """0=特別警報 1=警報 2=注意報"""
    if code in EMERGENCY_CODES:
        return 0
    if code in WARNING_CODES:
        return 1
    return 2


def build_warning_alert(loc, state, min_level="advisory", fc=None, idx=0):
    office = loc.get("jma_office")
    city = loc.get("jma_city")
    if not office or not city:
        return None

    data = fetch_warning(office)
    active = extract_active_warnings(data, city)
    # 通知対象レベルで絞り込む(LINE無料枠 月200通を使い切らないため)
    if active is not None:
        limit = LEVEL_RANK.get(min_level, 2)
        active = {c for c in active if code_rank(c) <= limit}
    if active is None:
        sys.stderr.write(
            "警告: 市区町村コード %s が %s の警報データに見つかりません。"
            "config.json を確認してください（geocode コマンドで検索できます）\n" % (city, office))
        return None

    key = "warning:%d" % idx
    first_run = key not in state
    prev = set(state.get(key, []))
    state[key] = sorted(active)

    if first_run:
        # 初回実行時は、既に出ている警報・注意報をまとめて通知しない(基準として記録するだけ)
        return None

    added = active - prev
    lifted = prev - active
    if not added and not lifted:
        return None

    worst = min([sort_key(c)[0] for c in active], default=2)
    head = {0: "🚨 特別警報", 1: "⚠️ 気象警報", 2: "ℹ️ 気象注意報"}[worst] if active else "✅ 警報・注意報"

    lines = [head, "📍%s" % loc["name"], ""]
    if added:
        lines.append("【新たに発表】")
        for c in sorted(added, key=sort_key):
            lines.append("・%s" % label(c))
        lines.append("")
    if lifted:
        lines.append("【解除】")
        for c in sorted(lifted, key=sort_key):
            lines.append("・%s" % label(c))
        lines.append("")
    if active:
        lines.append("【現在発表中】")
        lines.append("　" + "、".join(label(c) for c in sorted(active, key=sort_key)))
    else:
        lines.append("現在、発表中の警報・注意報はありません")

    # 気象庁は警報が出ていない期間、古い発表内容をそのまま返し続けることがある。
    # 発表日時を先に確認し、1日以上前なら現況と無関係な見出し文は出さない。
    reported = None
    rdt = data.get("reportDatetime", "")
    if rdt:
        try:
            reported = datetime.strptime(rdt[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=JST)
        except ValueError:
            reported = None
    stale_days = (now_jst() - reported).days if reported else 0

    headline = (data.get("headlineText") or "").strip()
    if headline and stale_days < 1:
        lines.append("")
        lines.append(headline)

    # 大雨・洪水系が出ているときは、今後の雨量の見込みを添える
    rain_codes = {"03", "04", "10", "18", "33"}
    if fc and active & rain_codes:
        try:
            m = fc.get("minutely_15") or {}
            times = [parse_local(x) for x in m.get("time", [])]
            precip = m.get("precipitation", [])
            now = now_jst()
            nxt6 = sum(mm for t, mm in zip(times, precip)
                       if mm is not None and now <= t <= now + timedelta(hours=6))
            cur = fc.get("current", {}).get("precipitation")
            lines.append("")
            lines.append("━━ 雨の状況 ━━")
            if cur is not None:
                lines.append("いま: %.1fmm/h（%s）" % (cur, rain_intensity_note(cur)))
            lines.append("今後6時間の雨量: 約%.0fmm" % nxt6)
        except Exception:
            pass

    if reported:
        lines.append("")
        lines.append("発表: %s %s" % (reported.strftime("%m/%d %H:%M"),
                                    data.get("publishingOffice", "気象庁")))
        if stale_days >= 1:
            lines.append("⚠️ この情報は%d日前の発表です。最新は気象庁サイトで確認してください" % stale_days)

    # 命に関わる判断は必ず公式で確認できるようにする
    lines.append("")
    lines.append("━━ 公式情報 ━━")
    lines.append("危険度分布(キキクル)")
    lines.append("https://www.jma.go.jp/bosai/risk/")
    lines.append("警報・注意報")
    lines.append("https://www.jma.go.jp/bosai/warning/")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# コマンド
# ----------------------------------------------------------------------------
def _geo_search(name):
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": name, "count": 5, "language": "ja", "format": "json"})
    return http_json(url).get("results", [])


def cmd_geocode(name):
    # Open-Meteoの地名検索は「豊田市」ではヒットせず「豊田」でヒットする。
    # 末尾の行政区分を1文字ずつ落としてリトライする。
    res = _geo_search(name)
    query = name
    while not res and len(query) > 1 and query[-1] in "市区町村都道府県":
        query = query[:-1]
        res = _geo_search(query)

    print("■ 緯度経度（config.json の latitude / longitude に入れる）")
    if not res:
        print("  「%s」に一致する地点が見つかりませんでした" % name)
    else:
        if query != name:
            print("  （「%s」で検索しました）" % query)
        for r in res:
            print("  %s（%s） -> latitude: %.4f, longitude: %.4f"
                  % (r["name"], r.get("admin1", ""), r["latitude"], r["longitude"]))

    area = http_json("https://www.jma.go.jp/bosai/common/const/area.json")
    print()
    print("■ 気象庁エリアコード（config.json の jma_office / jma_city に入れる）")
    hits = 0
    for code, v in area.get("class20s", {}).items():
        if name in v.get("name", ""):
            office = code[:2] + "0000"
            print("  jma_city: %s（%s）  jma_office: %s" % (code, v["name"], office))
            hits += 1
            if hits >= 10:
                break
    if hits == 0:
        print("  市区町村名での一致なし。市区町村名（例: 名古屋市、豊田市）で再検索してください")


def cmd_watch():
    """1回の起動で一定時間だけ常駐し、一定間隔で警報チェックを繰り返す。

    GitHub Actions の cron は指定どおりには起動しない（"*/5" と書いても
    実測で2〜4時間に1回しか動かなかった）。そこで「起動回数」ではなく
    「1回の起動の中でループする」ことで、実質的な監視間隔を確保する。

      WATCH_MINUTES  : 何分間ループするか（既定 50）
      WATCH_INTERVAL : 何秒ごとにチェックするか（既定 300 = 5分）
    """
    minutes = int(env("WATCH_MINUTES") or "50")
    interval = int(env("WATCH_INTERVAL") or "300")

    # 送信先が未設定でも監視自体は続ける。ここで落とすと、設定が終わるまで
    # 防災監視が丸ごと止まってしまう(2026-09-13にその退行を起こした)。
    # 実際に通知すべき事象が起きた時点で notify() が落とすので、検知は止めない。
    if not email_enabled() and not line_enabled():
        sys.stderr.write(
            "警告: 送信先が未設定です。監視は続けますが、通知が必要になった時点で失敗します。\n"
            "  メール: SMTP_USER / SMTP_PASSWORD / MAIL_TO\n"
            "  LINE  : LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID\n")

    deadline = time.time() + minutes * 60
    n = 0
    print("watch開始: %d分間、%d秒ごとに警報をチェックします" % (minutes, interval), flush=True)
    while True:
        n += 1
        print("--- %d回目 %s JST" % (n, now_jst().strftime("%H:%M:%S")), flush=True)
        try:
            run("warning")
        except SystemExit:
            raise
        except Exception as e:
            # 一時的な通信エラーでループ全体を止めない
            sys.stderr.write("  チェック失敗(継続します): %s: %s\n" % (type(e).__name__, e))
        if time.time() + interval >= deadline:
            break
        time.sleep(interval)
    print("watch終了: %d回チェックしました" % n, flush=True)


def run(mode, dry_run=False):
    cfg = load_config()
    state = load_state()
    now = now_jst()
    sent = 0
    changed = False

    for idx, loc in enumerate(cfg["locations"]):
        # 朝サマリーの判定。GitHub Actions の cron は混雑時に十数分以上遅れることがあるため
        # 「設定時刻を過ぎていて、まだ今日送っていなければ送る」方式にする(1日1通を保証)。
        morning_key = "morning_sent:%d" % idx
        auto_morning = False
        if mode == "auto":
            target = cfg["morning_summary"]["hour_jst"]
            today = now.strftime("%Y-%m-%d")
            if now.hour >= target and state.get(morning_key) != today:
                auto_morning = True

        do_morning = (mode in ("morning", "preview") or auto_morning) \
            and loc.get("morning_summary", True)
        do_rain = loc.get("rain_alert", True) and mode in ("rain", "auto", "preview")
        do_warning = loc.get("warning_alert", True) and mode in ("warning", "auto", "preview")
        # warning 単独モード(災害監視の高頻度実行)では気象APIを叩かず、
        # 気象庁の警報JSONだけを見て軽く済ませる。
        need_weather = do_morning or do_rain or (do_warning and mode != "warning")

        fc = None
        if need_weather:
            try:
                fc = fetch_forecast(loc["latitude"], loc["longitude"])
            except Exception as e:
                sys.stderr.write("天気取得失敗 (%s): %s\n" % (loc["name"], e))

        if do_morning and fc:
            aq = fetch_air_quality(loc["latitude"], loc["longitude"])
            wb = fetch_wbgt(loc.get("wbgt_point"))
            ov = fetch_overview(loc.get("jma_office"))
            if notify(build_morning(loc, fc, cfg, aq, wb, ov), dry_run):
                sent += 1
                if auto_morning:
                    state[morning_key] = now.strftime("%Y-%m-%d")
                    changed = True

        if do_rain and fc:
            chg = build_change_alert(loc, fc, cfg, state, idx)
            changed = True
            if chg and notify(chg, dry_run):
                sent += 1
            elif chg is None and mode == "preview":
                print("[preview] %s: 予報の急変なし" % loc["name"])

            msg = build_rain_alert(loc, fc, cfg, state, idx)
            changed = True
            if msg and notify(msg, dry_run):
                sent += 1
            elif msg is None and mode == "preview":
                print("[preview] %s: 雨アラートなし（降り出しは予報範囲外 or 既に降雨中）" % loc["name"])

        if do_warning:
            # 災害系（地震・府県気象情報）は警報と同じ高頻度チェックで拾う
            for builder in (lambda: build_quake_alert(loc, cfg, state, idx),
                            lambda: build_info_alert(loc, state, idx)):
                try:
                    m = builder()
                    changed = True
                    if m and notify(m, dry_run):
                        sent += 1
                except Exception as e:
                    sys.stderr.write("災害情報の取得失敗 (%s): %s\n" % (loc["name"], e))

            try:
                msg = build_warning_alert(
                    loc, state, cfg.get("warning_alert", {}).get("min_level", "advisory"), fc, idx)
                changed = True
                if msg and notify(msg, dry_run):
                    sent += 1
                elif msg is None and mode == "preview":
                    print("[preview] %s: 警報・注意報の変化なし" % loc["name"])
            except Exception as e:
                sys.stderr.write("警報取得失敗 (%s): %s\n" % (loc["name"], e))

    if changed and not dry_run:
        save_state(state)
    print("完了: %d通送信 (mode=%s, %s JST)" % (sent, mode, now.strftime("%Y-%m-%d %H:%M")))


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    mode = args[0]

    if mode == "geocode":
        if len(args) < 2:
            die("地名を指定してください  例: python3 tenki_line.py geocode 名古屋市")
        cmd_geocode(args[1])
    elif mode == "test":
        dests = []
        if email_enabled():
            dests.append("メール(%s)" % env("MAIL_TO"))
        if line_enabled():
            dests.append("LINE")
        print("送信先: %s" % ("、".join(dests) if dests else "なし"), flush=True)
        notify("✅ 天気通知のテストです。\nこのメッセージが届いていれば設定は成功しています。\n(%s JST)"
               % now_jst().strftime("%Y-%m-%d %H:%M"))
    elif mode == "preview":
        run("preview", dry_run=True)
    elif mode == "watch":
        cmd_watch()
    elif mode in ("morning", "rain", "warning", "auto"):
        run(mode)
    else:
        die("不明なコマンド: %s\n%s" % (mode, __doc__))


if __name__ == "__main__":
    main()
