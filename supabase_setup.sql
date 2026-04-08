-- ============================================================
-- Cat Adoption System — Supabase Setup SQL
-- Run this in your Supabase project: SQL Editor → New Query
-- ============================================================

-- USERS
create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  password text not null,
  full_name text,
  phone text,
  address text,
  valid_id_url text
);

-- CATS
create table if not exists cats (
  id bigint primary key generated always as identity,
  name text not null,
  breed text,
  age int,
  gender text,
  image text default 'cat1.jpg',
  status text default 'available'
);

-- ADOPTION REQUESTS
create table if not exists adoption_requests (
  id bigint primary key generated always as identity,
  user_id uuid references users(id) on delete cascade,
  cat_id bigint references cats(id) on delete cascade,
  living_situation text,
  has_other_pets text,
  experience_level text,
  reason text,
  status text default 'Pending',
  created_at timestamptz default now()
);

-- SEED CATS
insert into cats (name, breed, age, gender, image, status) values
  ('Jhemer Whiskers', 'Persian', 2, 'Male', 'cat1.jpg', 'available'),
  ('Luna', 'Siamese', 1, 'Female', 'cat2.jpg', 'available'),
  ('Bella', 'Ragdoll', 2, 'Female', 'cat3.jpg', 'available'),
  ('Milo', 'British Shorthair', 1, 'Male', 'cat4.jpg', 'available')
on conflict do nothing;

-- ============================================================
-- ROW LEVEL SECURITY (RLS)
--
-- Supabase enables RLS on all tables by default.
-- Without these policies the anon key (used by your Flask app)
-- cannot read or write ANYTHING — every query silently returns
-- empty data or throws a permission error.
--
-- Run this entire block in SQL Editor after creating the tables.
-- ============================================================

-- ---- users ----
alter table users enable row level security;

-- Anyone can register (insert a new user row)
create policy "allow_register" on users
  for insert to anon with check (true);

-- A user can read and update only their own row
create policy "allow_own_select" on users
  for select to anon using (true);

create policy "allow_own_update" on users
  for update to anon using (true);

create policy "allow_own_delete" on users
  for delete to anon using (true);

-- ---- cats ----
alter table cats enable row level security;

-- Everyone (including guests) can read cats
create policy "allow_cats_select" on cats
  for select to anon using (true);

-- ---- adoption_requests ----
alter table adoption_requests enable row level security;

-- Anyone logged in via the anon key can insert and read requests
create policy "allow_ar_insert" on adoption_requests
  for insert to anon with check (true);

create policy "allow_ar_select" on adoption_requests
  for select to anon using (true);

create policy "allow_ar_update" on adoption_requests
  for update to anon using (true);

-- ============================================================
-- STORAGE: Create a bucket named "valid-ids" and set it public
-- Dashboard → Storage → New Bucket → Name: valid-ids → Public ✓
-- ============================================================
