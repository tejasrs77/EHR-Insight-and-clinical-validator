# uvicorn src.api.main:app --reload
import asyncio
import os
from typing import List

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from pydantic import BaseModel

from nemoguardrails import RailsConfig, LLMRails
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

from src.pii_redaction.presidio_service import ClinicalPIIRedactor

load_dotenv()

app = FastAPI(title="Zero-Trust Clinical RAG API")

# Global state to hold heavy ML models so they load only once
middleware = {}


@app.on_event("startup")
async def startup_event():
    print("⏳ Booting Enterprise AI Middlewares...")

    # 1. Load Presidio (spaCy)
    middleware["redactor"] = ClinicalPIIRedactor()

    # 2. Load NeMo Guardrails
    config = RailsConfig.from_path("./src/guardrails")
    middleware["rails"] = LLMRails(config)

    # 3. Load the embedding model
    print("⏳ Loading local BioClinical ModernBERT embedding model...")
    middleware["embedder"] = SentenceTransformer(
        "NeuML/bioclinical-modernbert-base-embeddings"
    )

    print("✅ System Ready on port 8000.")


# --- DATABASE CONNECTION HELPER ---
def get_db_engine():
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT")
    db_name = os.getenv("DB_NAME")

    if db_host:
        db_url = (
            f"postgresql+psycopg://{db_user}:{db_pass}"
            f"@{db_host}:{db_port}/{db_name}"
        )
    else:
        db_url = os.getenv(
            "POSTGRES_URL",
            "postgresql+psycopg://postgres:password@localhost:5432/clinical_db",
        )

    return create_engine(db_url)


# --- REQUEST MODELS ---
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    patient_id: str
    messages: List[ChatMessage]


class ClinicalQuery(BaseModel):
    patient_id: str
    prompt: str


# --- ENDPOINT 1: HEALTH CHECK (MOCK DB) ---
@app.post("/api/v1/clinical-query")
async def process_clinical_query(query: ClinicalQuery):
    try:
        raw_db_context = f"""
        Patient John Doe (ID: {query.patient_id}) was admitted on March 15th.
        Last recorded Furosemide dosage was 40mg IV.
        Attending physician: Dr. Gregory House, ID: 20043.
        """

        redactor = middleware["redactor"]
        safe_context = redactor.redact_clinical_context(
            raw_text=raw_db_context
        )

        augmented_prompt = (
            f"Clinical Context:\n{safe_context}\n\n"
            f"User Question: {query.prompt}"
        )

        rails = middleware["rails"]
        response = await rails.generate_async(
            messages=[
                {
                    "role": "user",
                    "content": augmented_prompt,
                }
            ]
        )

        return {
            "status": "success",
            "redacted_context_used": safe_context.strip(),
            "llm_response": response["content"],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- ENDPOINT 2: PRODUCTION CHAT (NATIVE PGVECTOR DB) ---
@app.post("/api/v1/chat")
async def process_chat(request: ChatRequest):
    try:
        latest_question = request.messages[-1].content
        engine = get_db_engine()

        # 1. Embed the user's question into a 768-dimensional vector
        embedder = middleware["embedder"]
        query_vector = embedder.encode(latest_question).tolist()

        # 2. Search the selected patient's embedded records
        with engine.connect() as conn:
            query = text("""
                SELECT
                    drug,
                    dose_val_rx,
                    dose_unit_rx,
                    route,
                    eventtype,
                    test_name,
                    comments,
                    description
                FROM patient_encounters
                WHERE subject_id = :subject_id
                  AND clinical_embedding IS NOT NULL
                ORDER BY clinical_embedding
                    <=> CAST(:query_embedding AS vector)
                LIMIT 5;
            """)

            result = conn.execute(
                query,
                {
                    "subject_id": int(request.patient_id),
                    "query_embedding": str(query_vector),
                },
            )
            rows = result.fetchall()

            if not rows:
                real_db_context = (
                    f"No embedded historical records found for patient "
                    f"{request.patient_id}."
                )
            else:
                context_lines = []

                for row in rows:
                    context_lines.append(
                        f"Drug: {row.drug} "
                        f"({row.dose_val_rx} {row.dose_unit_rx}), "
                        f"Route: {row.route}, "
                        f"Event: {row.eventtype}, "
                        f"Test: {row.test_name}, "
                        f"Comments: {row.comments}, "
                        f"Diagnosis: {row.description}"
                    )

                real_db_context = (
                    f"[Records for Patient ID: {request.patient_id}]\n"
                    + "\n".join(context_lines)
                )

        # 3. Redact the database context using Presidio
        redactor = middleware["redactor"]
        safe_context = redactor.redact_clinical_context(
            raw_text=real_db_context
        )

        # 4. Assemble the augmented prompt
        augmented_prompt = (
            f"Clinical Context:\n{safe_context}\n\n"
            f"User Question: {latest_question}"
        )

        # 5. Format conversation history for NeMo Guardrails
        nemo_history = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in request.messages[:-1]
        ]

        nemo_history.append(
            {
                "role": "user",
                "content": augmented_prompt,
            }
        )

        # 6. Route the request through Guardrails
        rails = middleware["rails"]
        response = await rails.generate_async(messages=nemo_history)

        return {
            "status": "success",
            "llm_response": response["content"],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- ENDPOINT 3: FETCH ALL UNIQUE PATIENTS ---
@app.get("/api/v1/patients")
async def get_unique_patients():
    try:
        engine = get_db_engine()

        with engine.connect() as conn:
            query = text("""
    SELECT DISTINCT subject_id
    FROM patient_encounters
    WHERE subject_id IS NOT NULL
      AND clinical_embedding IS NOT NULL
    ORDER BY subject_id;
""")

            result = conn.execute(query)
            patients = [str(row[0]) for row in result]

            return {
                "patients": patients if patients else ["No patients found"]
            }

    except Exception as e:
        return {
            "patients": [],
            "error": str(e),
        }