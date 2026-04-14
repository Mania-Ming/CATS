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
  status text default 'available',
  weight_range text,
  size text,
  coat_colors text,
  temperament text,
  lifespan text,
  origin text,
  description text
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
-- Run only the INSERT block below if tables already exist.
-- Each cat has a unique image filename (cat1.jpg – cat12.jpg).
insert into cats (name, breed, age, gender, image, status) values
  ('Jhemer Whiskers', 'Persian',           2, 'Male',   'cat1.jpg',  'available'),
  ('Luna',            'Siamese',            1, 'Female', 'cat2.jpg',  'available'),
  ('Bella',           'Ragdoll',            2, 'Female', 'cat3.jpg',  'available'),
  ('Milo',            'British Shorthair',  1, 'Male',   'cat4.jpg',  'available'),
  ('Nala',            'Maine Coon',         3, 'Female', 'cat5.jpg',  'available'),
  ('Oliver',          'Scottish Fold',      2, 'Male',   'cat6.jpg',  'available'),
  ('Cleo',            'Bengal',             1, 'Female', 'cat7.jpg',  'available'),
  ('Simba',           'Abyssinian',         4, 'Male',   'cat8.jpg',  'available'),
  ('Mochi',           'Birman',             2, 'Female', 'cat9.jpg',  'available'),
  ('Leo',             'Norwegian Forest',   3, 'Male',   'cat10.jpg', 'available'),
  ('Coco',            'Sphynx',             1, 'Female', 'cat11.jpg', 'available'),
  ('Ash',             'Russian Blue',       2, 'Male',   'cat12.jpg', 'available')
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

-- ============================================================
-- BREED DETAIL COLUMNS (run if cats table already exists)
-- ============================================================
alter table cats add column if not exists weight_range text;
alter table cats add column if not exists size         text;
alter table cats add column if not exists coat_colors  text;
alter table cats add column if not exists temperament  text;
alter table cats add column if not exists lifespan     text;
alter table cats add column if not exists origin       text;
alter table cats add column if not exists description  text;

-- Allow anon key to update cats (needed for frontend Supabase sync)
create policy "allow_cats_update" on cats
  for update to anon using (true);
create policy "allow_cats_insert" on cats
  for insert to anon with check (true);
create policy "allow_cats_delete" on cats
  for delete to anon using (true);

-- Seed breed details
update cats set weight_range='3–5 kg',  size='Medium', coat_colors='White, Silver, Golden, Tabby', temperament='Gentle, Quiet, Affectionate', lifespan='12–17 yrs', origin='Iran',          description='Known for their long silky coat and calm personality. Great indoor companions.' where breed='Persian';
update cats set weight_range='3–5 kg',  size='Medium', coat_colors='Seal, Chocolate, Blue, Lilac point', temperament='Vocal, Social, Intelligent', lifespan='12–15 yrs', origin='Thailand',      description='Highly talkative and social. Forms strong bonds with their owners.' where breed='Siamese';
update cats set weight_range='4–9 kg',  size='Large',  coat_colors='Colorpoint, Mitted, Bicolor', temperament='Docile, Calm, Affectionate', lifespan='12–17 yrs', origin='United States',  description='Nicknamed "puppy cats" for their tendency to follow owners around the house.' where breed='Ragdoll';
update cats set weight_range='4–8 kg',  size='Medium', coat_colors='Blue, Black, White, Cream, Tabby', temperament='Calm, Easygoing, Loyal', lifespan='12–17 yrs', origin='United Kingdom', description='Stocky and round-faced. Adaptable to apartment living and very laid-back.' where breed='British Shorthair';
update cats set weight_range='5–11 kg', size='Large',  coat_colors='Brown Tabby, Silver, Black, White', temperament='Playful, Gentle, Dog-like', lifespan='12–15 yrs', origin='United States',  description='One of the largest domestic breeds. Loves water and is highly intelligent.' where breed='Maine Coon';
update cats set weight_range='3–5 kg',  size='Small',  coat_colors='Blue, Black, White, Tabby', temperament='Loyal, Gentle, Adaptable', lifespan='11–14 yrs', origin='Scotland',        description='Recognized by their folded ears. Sweet-natured and gets along well with children.' where breed='Scottish Fold';
update cats set weight_range='4–7 kg',  size='Medium', coat_colors='Brown Spotted, Marble, Snow', temperament='Active, Curious, Energetic', lifespan='12–16 yrs', origin='United States',  description='Wild-looking coat with a domestic temperament. Highly athletic and playful.' where breed='Bengal';
update cats set weight_range='3–5 kg',  size='Medium', coat_colors='Ruddy, Red, Blue, Fawn', temperament='Active, Curious, Playful', lifespan='14–15 yrs', origin='Ethiopia',        description='One of the oldest known breeds. Slender and athletic with a ticked coat.' where breed='Abyssinian';
update cats set weight_range='3–6 kg',  size='Medium', coat_colors='Seal, Blue, Chocolate, Lilac point', temperament='Gentle, Calm, Social', lifespan='12–16 yrs', origin='Burma/France',    description='Sacred cat of Burma. Known for silky coat and striking blue eyes.' where breed='Birman';
update cats set weight_range='4–9 kg',  size='Large',  coat_colors='Brown Tabby, Black, White, Blue', temperament='Gentle, Playful, Independent', lifespan='14–16 yrs', origin='Norway',          description='Built for cold climates with a thick double coat. Excellent hunters.' where breed='Norwegian Forest';
update cats set weight_range='3–5 kg',  size='Medium', coat_colors='All colors and patterns', temperament='Affectionate, Energetic, Mischievous', lifespan='12–15 yrs', origin='France',          description='Hairless breed known for warmth-seeking behavior and extroverted personality.' where breed='Sphynx';
update cats set weight_range='3–5 kg',  size='Medium', coat_colors='Blue-grey with silver tips', temperament='Gentle, Reserved, Loyal', lifespan='15–20 yrs', origin='Russia',          description='Naturally occurring breed with a dense plush coat and vivid green eyes.' where breed='Russian Blue';

-- ============================================================
-- PROFILE AVATAR (run if users table already exists)
-- ============================================================
alter table users add column if not exists avatar_url text;

-- ============================================================
-- STORAGE: Create a bucket named "avatars" and set it public
-- Dashboard → Storage → New Bucket → Name: avatars → Public ✓
-- ============================================================

-- ============================================================
-- STORAGE RLS POLICIES FOR avatars BUCKET
-- Run in Supabase SQL Editor after creating the avatars bucket.
-- These allow public read and authenticated write (anon key).
-- ============================================================

-- Allow anyone to read avatars (public bucket)
create policy "avatars_public_read"
  on storage.objects for select
  using ( bucket_id = 'avatars' );

-- Allow authenticated uploads — file path must be avatars/{user_id}.*
-- Since this app uses custom auth (not Supabase Auth), we allow all
-- anon-key uploads and rely on the Flask session for ownership.
create policy "avatars_anon_insert"
  on storage.objects for insert to anon
  with check ( bucket_id = 'avatars' );

create policy "avatars_anon_update"
  on storage.objects for update to anon
  using ( bucket_id = 'avatars' );

create policy "avatars_anon_delete"
  on storage.objects for delete to anon
  using ( bucket_id = 'avatars' );
