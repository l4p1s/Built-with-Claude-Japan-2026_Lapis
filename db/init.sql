-- ponytail: 初回起動時だけ実行される。スキーマ変更が出たら移行ツール（Alembic 等）を入れる
CREATE EXTENSION IF NOT EXISTS postgis;

-- アカウントの代わりの匿名セッション。トークン本体は持たず SHA-256 だけ保存する
CREATE TABLE sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash    bytea NOT NULL UNIQUE,
    consented_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE location_points (
    id           bigserial PRIMARY KEY,
    session_id   uuid NOT NULL REFERENCES sessions ON DELETE CASCADE,
    geom         geography(Point, 4326) NOT NULL,
    accuracy_m   real CHECK (accuracy_m >= 0),
    recorded_at  timestamptz NOT NULL
);
CREATE INDEX ON location_points (session_id, recorded_at);
CREATE INDEX ON location_points USING gist (geom);

-- 非公式の拠点の候補。人の集まりの検知（detected）か、利用者の報告（report）から生まれる
CREATE TABLE candidates (
    id                bigserial PRIMARY KEY,
    source            text NOT NULL CHECK (source IN ('detected', 'report')),
    center            geography(Point, 4326) NOT NULL,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'confirmed', 'ended')),
    last_activity_at  timestamptz NOT NULL DEFAULT now(),  -- 最後に確認された時刻
    created_at        timestamptz NOT NULL DEFAULT now(),
    -- ここから下は検知（detected）のときだけ入る
    cell_code         text,  -- 1/2 地域メッシュ（500m）の番号
    window_start      timestamptz,
    observed          integer,
    expected          double precision,
    p_value           double precision,
    UNIQUE (cell_code, window_start)
);
CREATE INDEX ON candidates USING gist (center);

-- 「ここに何がある？」への回答と、利用者の報告。1人1候補1件の最新の意見
CREATE TABLE answers (
    id            bigserial PRIMARY KEY,
    candidate_id  bigint NOT NULL REFERENCES candidates ON DELETE CASCADE,
    session_id    uuid NOT NULL REFERENCES sessions ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN (
                      'water', 'food', 'goods', 'car_sleeping', 'shelter', 'other',
                      'none', 'ended')),
    note          text CHECK (char_length(note) <= 100),
    answered_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, session_id)
);

-- 備考の「不適切」通報。1人1備考1回
CREATE TABLE flags (
    answer_id   bigint NOT NULL REFERENCES answers ON DELETE CASCADE,
    session_id  uuid NOT NULL REFERENCES sessions ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (answer_id, session_id)
);

-- 公式の拠点。取り込みは scripts/import_bodik.py
CREATE TABLE official_spots (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,  -- 取り込み元。例: bodik-kumamoto-r8
    source_key  text NOT NULL,  -- 取り込み元の中で一意なキー。BODIK は「市町村コード:名前」
    name        text NOT NULL,
    address     text,
    kind        text NOT NULL CHECK (kind IN ('shelter', 'water')),
    geom        geography(Point, 4326) NOT NULL,
    opened_at   timestamptz,  -- 開設されなかった拠点は NULL
    closed_at   timestamptz,  -- 開設中は NULL
    UNIQUE (source, source_key)
);
CREATE INDEX ON official_spots USING gist (geom);
