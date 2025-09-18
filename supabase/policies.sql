alter table public.users enable row level security;
alter table public.guesses enable row level security;
alter table public.results enable row level security;
alter table public.streaks enable row level security;

create policy "anon read results" on public.results for select using (true);
create policy "anon insert results" on public.results for insert with check (true);

create policy "anon read guesses" on public.guesses for select using (true);
create policy "anon insert guesses" on public.guesses for insert with check (true);

create policy "anon upsert users" on public.users for insert with check (true);
create policy "anon read users" on public.users for select using (true);

create policy "anon upsert streaks" on public.streaks for insert with check (true);
create policy "anon update streaks" on public.streaks for update using (true) with check (true);
create policy "anon read streaks" on public.streaks for select using (true);
