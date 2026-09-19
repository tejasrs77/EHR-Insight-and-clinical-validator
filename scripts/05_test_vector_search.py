import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

def test_vector_search():
    load_dotenv()
    db_url = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    engine = create_engine(db_url)
    
    print("⏳ Loading local BioClinical ModernBERT model...")
    model = SentenceTransformer('NeuML/bioclinical-modernbert-base-embeddings')
    
    # 1. Define a complex, natural language medical query
    query_text = "Patient presenting with severe liver disease and fluid retention needing diuretics"
    print(f"\n🔍 Semantic Query: '{query_text}'")
    
    # 2. Vectorize the query locally
    query_vector = model.encode(query_text).tolist()
    
    # 3. Search AWS using pgvector's cosine distance operator (<=>)
    search_sql = text("""
        SELECT 
            id, 
            description, 
            drug, 
            clinical_embedding <=> CAST(:query_vector AS vector(768)) AS cosine_distance
        FROM patient_encounters
        WHERE clinical_embedding IS NOT NULL
        ORDER BY cosine_distance ASC
        LIMIT 3;
    """)
    
    with engine.connect() as conn:
        # Pass the vector as a string-formatted array
        results = conn.execute(search_sql, {"query_vector": str(query_vector)}).mappings().fetchall()
        
        print("\n🏆 Top 3 Semantic Matches:")
        for rank, row in enumerate(results, 1):
            print("-" * 65)
            print(f"Rank {rank} (Distance: {row['cosine_distance']:.4f})")
            print(f"Diagnosis : {row['description']}")
            print(f"Drug      : {row['drug']}")
            print(f"Record ID : {row['id']}")
            
if __name__ == "__main__":
    test_vector_search()