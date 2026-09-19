import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

def generate_and_store_embeddings():
    load_dotenv()
    db_url = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    engine = create_engine(db_url)
    
    # 1. Load the BioClinical ModernBERT embedding model
    print("⏳ Loading local BioClinical ModernBERT model...")
    model = SentenceTransformer('NeuML/bioclinical-modernbert-base-embeddings')
    
    # Guardrail: Programmatically verify the output dimension is 768
    if model.get_sentence_embedding_dimension() != 768:
        raise ValueError("CRITICAL DIMENSION MISMATCH: Model output does not match database vector(768).")

    # Increase batch size for network efficiency
    batch_size = 2
    # For a demo, 10,000 records is perfect to prove scale without waiting hours
    demo_limit = 100
    
    with engine.begin() as conn:
        print(f"🚀 Generating batched embeddings for up to {demo_limit} patient records...")
        
        with tqdm(total=demo_limit, desc="Vectorizing PHI", unit="rows") as pbar:
            processed = 0
            
            while processed < demo_limit:
                # Fetch a batch of records
                select_query = text("""
                    SELECT id, admission_type, drug, test_name, drg_severity, description, comments 
                    FROM patient_encounters 
                    WHERE clinical_embedding IS NULL 
                    LIMIT :batch_size
                """)
                batch = conn.execute(select_query, {"batch_size": batch_size}).mappings().fetchall()
                
                if not batch:
                    break # No more NULL records
                
                # 1. Extract strings into a single list for parallel processing
                clinical_texts = []
                ids = []
                for row in batch:
                    components = []
                    if row['admission_type']: components.append(f"Admission: {row['admission_type']}")
                    if row['drug']: components.append(f"Prescribed: {row['drug']}")
                    if row['test_name']: components.append(f"Lab Test: {row['test_name']}")
                    if row['drg_severity']: components.append(f"Severity Level: {row['drg_severity']}")
                    if row['description']: components.append(f"Diagnosis: {row['description']}")
                    if row['comments']: components.append(f"Notes: {row['comments'][:250]}")
                    
                    clinical_texts.append(" | ".join(components))
                    ids.append(row['id'])
                
                # 2. BATCHED ENCODING: Feed the entire list to the model at once!
                # This engages PyTorch's parallel processing.
                embeddings = model.encode(clinical_texts, batch_size=batch_size).tolist()
                
                # 3. Prepare BULK update parameters
                update_params = [
                    {"id": record_id, "embedding": str(emb)} 
                    for record_id, emb in zip(ids, embeddings)
                ]
                
                # 4. BULK UPDATE: execute all 250 rows in a single network round-trip
                update_query = text("""
                    UPDATE patient_encounters 
                    SET clinical_embedding = :embedding 
                    WHERE id = :id
                """)
                conn.execute(update_query, update_params)
                
                processed += len(batch)
                pbar.update(len(batch))

    print("\n✅ Phase 3 Complete: Clinical records are vectorized and ready for hybrid search.")

if __name__ == "__main__":
    generate_and_store_embeddings()