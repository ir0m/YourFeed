# YourFeed

ジャンルごとにWebを検索し、見つけた記事をAIが要約して一つのタイムラインに並べます。検索には同梱の [SearXNG](https://docs.searxng.org/) を使います。

## 必要なもの

- Docker と Docker Compose
- AIの接続先のどれか一つ
  - OpenAI互換API（OpenAI、Gemini、OpenRouter、Ollama など）
  - Claude API
  - ログインした Claude Code・Codex・Gemini CLI（Dockerを使わずに動かします）

## 起動

```sh
git clone https://github.com/ir0m/YourFeed.git
cd YourFeed
cp .env.example .env
```

`.env` に次を書きます。

- `POSTGRES_PASSWORD`: 英数字のパスワード
- `SEARXNG_SECRET`: 長いランダムな文字列（`openssl rand -hex 32` など）
- 使うAIのURLとキー（「AIの接続先」）

```sh
docker compose up -d --build
```

http://localhost:8000 を開き、右上の「設定」で次の順に決めます。

1. 「AI」で接続先とモデルを選ぶ
2. 「ジャンルを追加」で名前を入れて保存する
3. ジャンルの比率を決めて「今すぐ更新」を押す

以後は更新間隔ごとに自動で更新されます。

## 設定

| 項目 | 内容 |
|---|---|
| 比率 | ジャンルの配分。0のジャンルは使わない |
| 更新間隔 | 15分〜24時間。0時から区切る |
| 1回の件数 | 1回に投稿する数（50件まで）。足りないジャンルの分は他のジャンルに回し、新しい記事が無ければ前に見つけた記事を使う |
| 表示期間 | これを過ぎた投稿は消える |
| 接続先・モデル | モデルが空なら、Claude API と Claude Code は `claude-opus-5-5`、Codex と Gemini CLI はそれぞれの既定を使う |

ジャンル:

| 項目 | 内容 |
|---|---|
| 名前 | 必須。検索語になる |
| サイト | 1行に1つ（`example.com` や `https://example.com/news`）。先にこの中を検索する |
| 注目する語 | 1行に1つ。ジャンル名と合わせて検索し、要約でも取り上げる |
| 注目語以外 | ジャンル名だけで検索した記事を混ぜる割合 |
| 除外する語 | 1行に1つ。この語を含む記事は使わない |
| 文字数 | 要約の最小と最大 |
| 事実と解釈 | 事実のみ / 事実中心 / 半々 / 解釈多め |

## 検索

更新のたびに新しいURLを合計50件、比率で分けて探します。ニュースを1日・1週間・1か月の順に探し、足りなければWeb全体と論文を探します。ページはHTMLとPDFを読み、robots.txt に従います。記事の一覧やジャンルと関係のない記事は使いません。

設定画面に「候補 32/50」と出たときは、50件に届かなかったことを表します。検索エンジンの回数制限で減ることがあります。SearXNG の設定は `deploy/searxng/settings.yml` にあります。

## AIの接続先

設定画面の「接続先」で選び、URLとキーは `.env` に書いて `docker compose up -d` で反映します。

| 使うもの | 接続先 | `.env` |
|---|---|---|
| OpenAI | OpenAI互換API | `OPENAI_BASE_URL=https://api.openai.com/v1`、`OPENAI_API_KEY` |
| Gemini | OpenAI互換API | `OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`、`OPENAI_API_KEY` |
| OpenRouter | OpenAI互換API | `OPENAI_BASE_URL=https://openrouter.ai/api/v1`、`OPENAI_API_KEY` |
| Ollama | OpenAI互換API | `OPENAI_BASE_URL=http://host.docker.internal:11434/v1`（Dockerなしでは `http://localhost:11434/v1`） |
| Claude | Claude API | `ANTHROPIC_API_KEY` |
| Claude のサブスクリプション | Claude Code | 不要 |
| ChatGPT のサブスクリプション | Codex | 不要 |
| Google アカウント | Gemini CLI | 不要 |

Claude Code・Codex・Gemini CLI は、ログインした環境で Docker を使わずに動かします（「Dockerを使わずに動かす」）。どれもツールを無効にして呼び出し、AIには記事の本文だけを渡します。

## 別の端末から開く

ログイン機能はないので、インターネットに直接公開しないでください。VPNやリバースプロキシを通して開くときは、そのホスト名を `.env` の `YOURFEED_ALLOWED_HOSTS` にカンマ区切りで書きます。

## 環境変数

| 変数 | 内容 |
|---|---|
| `YOURFEED_PORT` | ポート（既定 8000） |
| `YOURFEED_TIMEZONE` | 更新間隔の起点の時間帯（既定 `Asia/Tokyo`） |
| `YOURFEED_SEARCH_PORT` | SearXNG のポート（既定 8888） |
| `SEARXNG_VERSION` | SearXNG のイメージのタグ（既定 `latest`） |
| `SEARXNG_URL` | Dockerなしで使う SearXNG のURL（既定 `http://127.0.0.1:8888`） |
| `YOURFEED_ALLOW_PRIVATE_URLS` | `1` でLAN内やlocalhostのページも読む |

## 更新・停止

```sh
git pull && docker compose up -d --build   # 更新
docker compose down                         # 停止
docker compose down -v                      # 停止してデータも消す
```

## Dockerを使わずに動かす

Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 20以上、PostgreSQL 14以上を使います。SearXNG は `.env` を書いて `docker compose up -d searxng` で動かすか、`search.formats` に `json` を加えた SearXNG のURLを `SEARXNG_URL` に書きます。

```sh
createdb yourfeed
export DATABASE_URL=postgresql://<user>:<password>@127.0.0.1:5432/yourfeed
uv sync
(cd web && npm ci && npm run build)
uv run alembic upgrade head
uv run uvicorn yourfeed.main:app --host 127.0.0.1 --port 8000
```

更新は別に動かします。`--once` を付けると1回だけ更新して終わります。

```sh
uv run python -m yourfeed.worker
```

systemd で動かすときは、環境変数を `~/.config/yourfeed/env` に書き、`deploy/systemd/` のファイルを `~/.config/systemd/user/` に置きます。`~/YourFeed` 以外に置いた場合はファイル内のパスを直します。

```sh
systemctl --user daemon-reload
systemctl --user enable --now yourfeed-web yourfeed-worker
```

## テスト

```sh
uv run pytest
```

`YOURFEED_TEST_DATABASE_URL` にテスト用のDBを指定すると、DBを使うテストも動きます。そのDBの中身は消えます。

## ライセンス

MIT
