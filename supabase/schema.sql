create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  username text unique not null,
  created_at timestamptz not null default now()
);

create table if not exists public.players (
  id uuid primary key default gen_random_uuid(),
  player_slug text unique not null,
  full_name text not null,
  position text not null
);

create table if not exists public.player_seasons (
  id uuid primary key default gen_random_uuid(),
  player_id uuid not null references public.players(id) on delete cascade,
  season int not null,
  team text not null,
  stat1_name text not null,
  stat1_value numeric not null,
  stat2_name text not null,
  stat2_value numeric not null,
  stat3_name text not null,
  stat3_value numeric not null,
  stat4_name text,
  stat4_value numeric,
  stat5_name text,
  stat5_value numeric,
  unique(player_id, season)
);

create table if not exists public.daily_game (
  game_date date primary key,
  player_id uuid not null references public.players(id),
  created_at timestamptz not null default now()
);

create table if not exists public.guesses (
  id uuid primary key default gen_random_uuid(),
  game_date date not null,
  user_id uuid not null references public.users(id) on delete cascade,
  guess_text text not null,
  is_correct boolean not null,
  reveal_index int not null,
  created_at timestamptz not null default now(),
  unique(game_date, user_id, reveal_index)
);

create table if not exists public.results (
  id uuid primary key default gen_random_uuid(),
  game_date date not null,
  user_id uuid not null references public.users(id) on delete cascade,
  correct_attempts int not null default 0,
  revealed int not null default 1,
  score int not null,
  finished_at timestamptz not null default now(),
  unique(game_date, user_id)
);

create table if not exists public.streaks (
  user_id uuid primary key references public.users(id) on delete cascade,
  current_streak int not null default 0,
  best_streak int not null default 0,
  updated_at timestamptz not null default now()
);

create index if not exists idx_results_date_score on public.results(game_date, score desc);
create index if not exists idx_guesses_user_date on public.guesses(user_id, game_date);
