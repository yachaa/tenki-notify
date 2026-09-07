# 天気・防災 通知 ＋ ダッシュボード

自宅・訪問先の天気予報、**雨がいつ降り出すか**、気象庁の警報・注意報を**メール／LINE**に送る。
Webダッシュボード（`dashboard.html`）もセット。

## 送信先について

送信先は**環境変数（GitHub Secrets）に値が入っているものだけ**が自動的に有効になる。
コードの変更は不要。

| 送信先 | 必要な設定 | 状態 |
|---|---|---|
| メール | `SMTP_USER` `SMTP_PASSWORD` `MAIL_TO` | いまはこちらを使う |
| LINE | `LINE_CHANNEL_ACCESS_TOKEN` `LINE_USER_ID` | あとで追加する。Secretsを登録した時点で有効になる |

両方登録すれば両方に届く。

## 中身

| ファイル | 役割 |
|---|---|
| `tenki_line.py` | 本体。天気を取得してLINEに送る（Python標準ライブラリのみ、追加インストール不要） |
| `config.json` | 監視地点と通知条件の設定 |
| `dashboard.html` | ブラウザで見るダッシュボード（サーバー不要の1ファイル） |
| `.github/workflows/tenki.yml` | GitHub Actionsで定時実行する設定 |
| `state.json` | 通知済みの記録（自動生成。同じ雨・同じ警報を二重に通知しないため） |
| `.env.example` | 送信先設定の記入テンプレート。`.env` にコピーして使う |
| `run_local.sh` | ローカル実行用。`.env` を読み込んで動かす |

## 送られる6種類の通知

災害系（警報・地震・気象情報）は**5分ごと**、天気系は**30分ごと**にチェックし、
送るものがある時だけ届く。

| 種類 | 間隔 | 内容 |
|---|---|---|
| 朝のサマリー | 30分 | 下記のとおり。毎朝7時に1通を保証 |
| 雨アラート | 30分 | 降り出し時刻・強さ・ピーク・やむ時刻・合計雨量 |
| 予報の急変 | 30分 | 気温・降水確率・雷雨/強風の追加・雨量の増加 |
| **気象警報・注意報** | **5分** | 新規発表・解除・現在発表中＋雨の実況＋キキクルへの導線 |
| **地震** | **5分** | 自分の市区町村の震度・震源・規模・震度に応じた行動 |
| **府県気象情報** | **5分** | 記録的短時間大雨情報・熱中症警戒アラート・竜巻注意情報など |

### 通知の速さと費用

GitHub Actions のプライベートリポジトリ無料枠は**月2,000分**で、課金は**ジョブ単位で1分未満切り上げ**
（実行が10秒でも1分として計上される）。

| 間隔 | 月あたり | 可否 |
|---|---|---|
| 60分 | 約730分 | ○ |
| **30分（現在）** | **約1,459分** | **○ 無料枠の上限** |
| 20分 | 約2,189分 | ✕ 超過 |
| 10分 | 約4,378分 | ✕ 超過 |

**これより速くしたい場合はリポジトリをPublicにする**（Publicは実行時間が無制限）。
ただし `config.json` の緯度経度が誰でも見えるようになる点に注意。
自宅の座標を出したくない場合は、最寄り駅などに少しずらすとよい。

### 朝のサマリーの中身

```
🌧 9/7(月) の天気
📍自宅

━━ 今日 ━━            天気・最高最低気温（前日比）・体感・湿度
                        降水確率・雨量・雨の継続時間
                        風向と風速（最大瞬間）・日の出/日の入り
━━ 時間ごとの天気 ━━   6時〜23時を1時間刻み（気温・降水確率・雨量）
━━ 雨の見込み ━━       降る時間帯と強さ（弱い雨〜非常に激しい雨）
━━ この先1週間 ━━      7日分の天気・気温・降水確率
━━ ○○地方気象台 より ━━ 気象台が書いた解説文（台風の見通しなど）
━━ ひとこと ━━         傘・服装・熱中症・不快指数・洗濯・風・UV・気圧・PM2.5
```

`━━ 気象台 より ━━` は数値予報には出てこない人間の解説で、
「台風第24号から変わった熱帯低気圧の進路によっては大雨のおそれ」のような
先の見通しがここに入る。

## データの出どころ（すべて無料・APIキー不要）

- 予報: [Open-Meteo](https://open-meteo.com/) — 日本国内は気象庁MSM/GSMが自動選択される
- 大気質(PM2.5): [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api) — 取れなくても通知は続行する
- 警報・注意報: [気象庁 防災情報](https://www.jma.go.jp/bosai/warning/)
- 天気概況（気象台の解説文）: `bosai/forecast/data/overview_forecast/{府県コード}.json`
- 地震情報: `bosai/quake/data/list.json`（市区町村コードは警報と同じ体系）
- 府県気象情報: `bosai/information/data/information.json`（`areaCodes` で府県を絞る）
- 暑さ指数(WBGT): [環境省 熱中症予防情報サイト](https://www.wbgt.env.go.jp/)
- 送信（メール）: Gmail の SMTP（`smtplib`、標準ライブラリ）
- 送信（LINE）: [LINE Messaging API](https://developers.line.biz/ja/docs/messaging-api/)

> **LINEについて:** LINE Notifyは2025年3月31日で終了済み。現在LINEに通知を送る方法はMessaging APIのみで、
> LINE公式アカウントの作成（SMSによる本人確認が必要）が前提になる。
> 無料枠は**コミュニケーションプラン（月額¥0）で月200通**。
> この設定なら通常は月60〜100通程度で収まるが、注意報が頻発する季節は
> `config.json` の `warning_alert.min_level` を `"warning"` にすると通数を抑えられる。
> メールにはこうした通数制限はない。

---

# セットアップ

## 手順1: メールの準備（5分ほど）

Gmailから送る。**Googleアカウントの「アプリパスワード」**が必要（通常のログインパスワードでは送れない）。

### 1-1. 2段階認証を有効にする
アプリパスワードは2段階認証が有効でないと発行できない。
[Googleアカウント → セキュリティ](https://myaccount.google.com/security) で「2段階認証プロセス」をオンにする。

### 1-2. アプリパスワードを発行する
1. https://myaccount.google.com/apppasswords を開く
2. アプリ名に `天気通知` などと入れて作成
3. **表示される16桁の文字列**を控える（この画面を閉じると二度と見られない）

> このパスワードはメール送信専用で、Googleアカウント全体のパスワードとは別物。
> 不要になったら同じ画面から個別に削除できる。**他人には見せないこと。**

### 1-3. 控える値

| 変数名 | 入れる値 |
|---|---|
| `SMTP_USER` | 送信元のGmailアドレス |
| `SMTP_PASSWORD` | 1-2で発行した16桁のアプリパスワード |
| `MAIL_TO` | 受信したいメールアドレス（`SMTP_USER` と同じでよい） |

Gmail以外を使う場合のみ `SMTP_HOST` / `SMTP_PORT` も設定する（既定は `smtp.gmail.com` / `587`）。

## 手順2: 地点を設定する

> **このリポジトリはPublic（誰でも閲覧可）です。**
> `config.json` に自宅の緯度経度を書くと公開されてしまうため、
> **実際の地点は GitHub Secrets の `LOCATIONS` に入れる**運用にしている。
> `LOCATIONS` があればそちらが優先され、`config.json` の値は使われない。
>
> `LOCATIONS` に入れるJSONの例（1行で貼り付ける）:
> ```json
> [{"name":"自宅","latitude":00.0000,"longitude":000.0000,"jma_office":"230000","jma_city":"2310000","wbgt_point":"51106","morning_summary":true,"rain_alert":true,"warning_alert":true}]
> ```
>
> ダッシュボードも同様に、URLパラメータで座標を渡す:
> `dashboard.html?name=自宅&lat=00.0000&lon=000.0000&office=230000&city=2310000`
> このURLをブックマークすれば、座標は自分のブラウザの中だけに残る。


`config.json` の `locations` を書き換える。緯度経度と気象庁コードは次のコマンドで調べられる。

```bash
python3 tenki_line.py geocode 名古屋市
```

出力された `latitude` / `longitude` / `jma_city` / `jma_office` を `config.json` に入れる。
地点を増やすときは `{ }` を並べる。

```json
"locations": [
  { "name": "自宅", "latitude": 35.1815, "longitude": 136.9066,
    "jma_office": "230000", "jma_city": "2310000",
    "morning_summary": true, "rain_alert": true, "warning_alert": true },
  { "name": "出張先（福岡市）", "latitude": 33.6, "longitude": 130.4,
    "jma_office": "400000", "jma_city": "4013000",
    "morning_summary": false, "rain_alert": true, "warning_alert": true }
]
```

`morning_summary` を `false` にすると、その地点は朝のサマリーを送らない（雨と警報だけ見る）。

---

## 手順3: 手元で動作確認

```bash
python3 tenki_line.py preview
```

LINEに送らず、送られる内容が画面に表示される。中身を見て問題なければ次へ。

### 実際に送ってみる

トークンをコマンドに直接書くとシェルの履歴に残るので、`.env` ファイルに書く方式にしてある。

```bash
cp .env.example .env
```

`.env` を開き、手順1-3の3つの値（`SMTP_USER` / `SMTP_PASSWORD` / `MAIL_TO`）を入れて保存する。
（`.env` は `.gitignore` 済みなのでGitHubには絶対に上がらない）

```bash
./run_local.sh test
```

「✅ 天気通知のテストです」というメールが届けば成功。
実行時に `送信先: メール(...)` と表示されるので、どこに送られたか確認できる。

以降、ローカルで動かすときは `./run_local.sh preview` / `./run_local.sh auto` が使える。

## 手順4: GitHub Actionsで自動化する

Macの電源が入っていなくても動くようにする。

### 4-1. リポジトリを作る
1. [github.com/new](https://github.com/new) で新規リポジトリを作成
   - 名前: 例 `tenki-line`
   - **Private** を選ぶ（設定ファイルを人に見せないため）
2. この `weather_line` フォルダの中身をそのリポジトリにアップロードする
   （GitHubの画面で「uploading an existing file」からドラッグ&ドロップでよい）
   - **`.env` はアップロードしないこと**（トークンが漏れる）。`.env.example` は上げてよい

> `.github/workflows/tenki.yml` はリポジトリの**ルート直下**に `.github/workflows/` として置く必要がある。
> フォルダごとアップロードすればそのままでよい。

### 4-2. Secretsを登録する
リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で登録する。

**いま登録するもの（メール）**

| Name | Secret |
|---|---|
| `SMTP_USER` | 送信元のGmailアドレス |
| `SMTP_PASSWORD` | 16桁のアプリパスワード |
| `MAIL_TO` | 受信するメールアドレス |

**あとで追加するもの（LINE）** — 登録した時点で自動的にLINEにも届くようになる。コード変更は不要。

| Name | Secret |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developersで発行するチャネルアクセストークン |
| `LINE_USER_ID` | 自分のLINEユーザーID |

> ここに入れた値はGitHubが暗号化して保持し、ログにも表示されない。**コードに直接書かないこと。**

### 4-3. 動かす
**Actions** タブ → 左の「天気LINE通知」 → **Run workflow** → mode に `test` を選んで実行。
LINEに届けば成功。以降は自動で動く。

- 毎朝 7:00（JST）… 朝のサマリー
- 毎時 0分 … 雨アラートと警報チェック（送るものがある時だけ通知）

時刻を変えたい場合は `.github/workflows/tenki.yml` の `cron` を編集する（**UTC表記**なので JST − 9時間）。

---

## 手順5: ダッシュボードを見られるようにする（任意）

`dashboard.html` はそのままダブルクリックで開ける（サーバー不要）。
スマホからも見たい場合は GitHub Pages で公開できる。

1. リポジトリの **Settings → Pages** → Source を `Deploy from a branch` → `main` / `/ (root)` → Save
2. 数分後 `https://<ユーザー名>.github.io/tenki-line/dashboard.html` で見られる
3. スマホのSafariで開き、共有 → 「ホーム画面に追加」でアプリのように使える

> **Publicリポジトリにすると誰でもURLを開けてしまう。** Privateリポジトリのままにするか、
> 公開する場合は緯度経度が自宅を指すことに注意（少しずらすか、最寄り駅にしておくとよい）。

ダッシュボードの地点は、HTML内の `const LOCATIONS = [...]`（`<script>`の先頭）を
`config.json` と同じ内容に書き換える。

---

## 設定の意味

```jsonc
"morning_summary": { "hour_jst": 7 },        // 朝のサマリーを送る時刻（JST・0〜23）

"rain_alert": {
  "lookahead_minutes": 120,                  // 何分先までの降り出しを通知するか
  "threshold_mm_per_15min": 0.2,             // これ以上を「雨」とみなす（15分あたりmm）
  "threshold_mm_per_hour": 0.3,              // 朝サマリーで雨の時間帯を出す閾値
  "quiet_hours_jst": [22, 6]                 // 22時〜6時は雨アラートを送らない
},

"warning_alert": {
  "min_level": "advisory"                    // advisory=注意報も / warning=警報以上 / emergency=特別警報のみ
}
```

## うまくいかないとき

| 症状 | 確認すること |
|---|---|
| `送信先が設定されていません` | `.env` またはGitHub Secretsに値が入っていない |
| メールの `認証エラー` | `SMTP_PASSWORD` に通常のパスワードを入れている。**16桁のアプリパスワード**が必要 |
| `UnicodeEncodeError: 'ascii' codec can't encode character '\xa0'` | アプリパスワードをGoogleの画面から空白ごとコピーした場合に出る。その空白はノーブレークスペース(U+00A0)。**現在はコード側で自動除去するので発生しない**（`clean_password()`） |
| アプリパスワードが発行できない | Googleアカウントの2段階認証が未設定。先に有効にする |
| メールが届かない | 迷惑メールフォルダを確認。`MAIL_TO` の綴りも確認 |
| LINEに届かない | 公式アカウントを自分で友だち追加したか。トークンとユーザーIDが正しいか |
| LINEが `HTTP 401` | チャネルアクセストークンが誤り、または再発行で古いものが無効になった |
| LINEが `HTTP 429` | 月200通の無料枠を使い切った。`min_level` を `"warning"` に上げる |
| 警報が出ない | `jma_city` が誤り。`python3 tenki_line.py geocode 地名` で正しいコードを確認 |
| 朝のサマリーが来ない | GitHub Actionsのcronは混雑時に数分〜十数分遅れることがある（仕様）。設定時刻を過ぎて未送信なら送る方式なので、遅れても1日1通は届く |

## あとでLINEを追加するには

1. LINE公式アカウントを作る（SMSによる本人確認が必要）
2. [LINE Developers Console](https://developers.line.biz/console/) でMessaging APIを有効化し、
   チャネルアクセストークンと自分のユーザーIDを取得
3. 公式アカウントを**自分で友だち追加**する（しないと届かない）
4. GitHub Secretsに `LINE_CHANNEL_ACCESS_TOKEN` と `LINE_USER_ID` を登録

**4を終えた時点で自動的にLINEにも届くようになる。** コードもワークフローも変更不要。
メールを止めたい場合は、メール側のSecretsを削除すればよい。

## 免責

この通知は補助的なもの。**避難など命に関わる判断は、必ず気象庁・自治体の公式発表で確認すること。**
- 気象庁 警報・注意報: https://www.jma.go.jp/bosai/warning/
- 気象庁 キキクル（危険度分布）: https://www.jma.go.jp/bosai/risk/
