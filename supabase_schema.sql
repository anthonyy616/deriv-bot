-- Create Profiles table
CREATE TABLE "Profiles" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL, -- Note: In production, store HASHED passwords, not plain text.
    selected_mt5_login TEXT, -- Stores the user's preferred MT5 account login ID
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create Profiles_API table
CREATE TABLE "Profiles_API" (
    "API" TEXT PRIMARY KEY, -- The API token itself is the PK
    user_id UUID NOT NULL REFERENCES "Profiles"(id) ON DELETE CASCADE
);

-- Create an index on user_id for faster lookups
CREATE INDEX idx_profiles_api_user_id ON "Profiles_API"(user_id);

-- RLS Policies (Optional but recommended if accessing from frontend)
ALTER TABLE "Profiles" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "Profiles_API" ENABLE ROW LEVEL SECURITY;

-- Allow users to read their own profile (This requires Supabase Auth usually, 
-- but since we are using custom tables, we might need custom logic or open access for the backend)
-- For now, we assume the backend (service role) will handle these queries.
