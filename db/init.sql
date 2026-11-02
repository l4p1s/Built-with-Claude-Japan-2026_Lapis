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

-- 検知した「いつもと違う集まり」の候補
CREATE TABLE candidates (
    id            bigserial PRIMARY KEY,
    cell_code     text NOT NULL,  -- 1/2 地域メッシュ（500m）の番号
    center        geography(Point, 4326) NOT NULL,
    window_start  timestamptz NOT NULL,
    observed      integer NOT NULL,
    expected      double precision NOT NULL,
    p_value       double precision NOT NULL,
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'confirmed', 'rejected')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cell_code, window_start)
);
CREATE INDEX ON candidates USING gist (center);

-- 「ここに何がある？」への回答。1人1候補1回答
CREATE TABLE answers (
    candidate_id  bigint NOT NULL REFERENCES candidates ON DELETE CASCADE,
    session_id    uuid NOT NULL REFERENCES sessions ON DELETE CASCADE,
    kind          text NOT NULL
                  CHECK (kind IN ('water', 'food', 'goods', 'car_sleeping', 'shelter', 'none', 'other')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (candidate_id, session_id)
);
