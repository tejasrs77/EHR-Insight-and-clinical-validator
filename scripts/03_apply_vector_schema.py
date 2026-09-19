import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def apply_pgvector():
    load_dotenv()
    
    db_url = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    engine = create_engine(db_url)
    
    print("🚀 Connecting to AWS to apply pgvector schema upgrade...")
    
    try:
        with engine.begin() as conn:
            # Note: Extension was activated via DBA superuser. 
            # We only alter the application table here.
            print("Adding 'clinical_embedding' column (768 dimensions)...")
            conn.execute(text("ALTER TABLE patient_encounters ADD COLUMN IF NOT EXISTS clinical_embedding vector(768);"))
            
            # Verify the column was added
            verify = conn.execute(text("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'patient_encounters' AND column_name = 'clinical_embedding';
            """)).fetchone()
            
            if verify:
                print(f"✅ Schema upgrade complete. Confirmed column: {verify[0]} ({verify[1]})")
            else:
                print("❌ Column not found after alter attempt.")
            
    except Exception as e:
        print(f"❌ Error applying schema: {e}")

if __name__ == "__main__":
    apply_pgvector()