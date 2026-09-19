import os

from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text


def generate_and_store_embeddings():
    load_dotenv()

    db_url = (
        f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )

    engine = create_engine(db_url)

    print("Loading BioClinical ModernBERT model...")

    model = SentenceTransformer(
        "NeuML/bioclinical-modernbert-base-embeddings"
    )

    embedding_dimension = model.get_sentence_embedding_dimension()

    if embedding_dimension != 768:
        raise ValueError(
            f"Expected 768 embedding dimensions, received "
            f"{embedding_dimension}."
        )

    # Generate 100 searchable records for every patient.
    records_per_patient = 100

    select_query = text("""
        WITH ranked_records AS (
            SELECT
                id,
                subject_id,
                admission_type,
                drug,
                dose_val_rx,
                dose_unit_rx,
                route,
                eventtype,
                test_name,
                drg_severity,
                description,
                comments,
                ROW_NUMBER() OVER (
                    PARTITION BY subject_id
                    ORDER BY id
                ) AS patient_row_number
            FROM patient_encounters
            WHERE subject_id IS NOT NULL
        )
        SELECT
            id,
            subject_id,
            admission_type,
            drug,
            dose_val_rx,
            dose_unit_rx,
            route,
            eventtype,
            test_name,
            drg_severity,
            description,
            comments
        FROM ranked_records
        WHERE patient_row_number <= :records_per_patient
        ORDER BY subject_id, patient_row_number;
    """)

    with engine.connect() as connection:
        records = connection.execute(
            select_query,
            {"records_per_patient": records_per_patient},
        ).mappings().fetchall()

    if not records:
        print("No patient records were found.")
        return

    patient_ids = sorted({row["subject_id"] for row in records})

    print(f"Patients found: {patient_ids}")
    print(f"Generating embeddings for {len(records)} records...")

    clinical_texts = []
    record_ids = []

    for row in records:
        components = []

        if row["admission_type"]:
            components.append(
                f"Admission type: {row['admission_type']}"
            )

        if row["drug"]:
            medication = f"Medication: {row['drug']}"

            if row["dose_val_rx"]:
                medication += f", dose {row['dose_val_rx']}"

            if row["dose_unit_rx"]:
                medication += f" {row['dose_unit_rx']}"

            if row["route"]:
                medication += f", route {row['route']}"

            components.append(medication)

        if row["eventtype"]:
            components.append(f"Event: {row['eventtype']}")

        if row["test_name"]:
            components.append(f"Lab test: {row['test_name']}")

        if row["drg_severity"]:
            components.append(
                f"Severity: {row['drg_severity']}"
            )

        if row["description"]:
            components.append(
                f"Diagnosis: {row['description']}"
            )

        if row["comments"]:
            components.append(
                f"Clinical notes: {str(row['comments'])[:500]}"
            )

        clinical_text = " | ".join(components)

        if not clinical_text:
            clinical_text = (
                f"Clinical record for patient {row['subject_id']}"
            )

        clinical_texts.append(clinical_text)
        record_ids.append(row["id"])

    embeddings = model.encode(
        clinical_texts,
        batch_size=32,
        show_progress_bar=True,
    ).tolist()

    update_query = text("""
        UPDATE patient_encounters
        SET clinical_embedding = CAST(:embedding AS vector)
        WHERE id = :id;
    """)

    update_parameters = [
        {
            "id": record_id,
            "embedding": str(embedding),
        }
        for record_id, embedding in zip(record_ids, embeddings)
    ]

    with engine.begin() as connection:
        connection.execute(update_query, update_parameters)

    print(
        f"Successfully embedded {len(records)} records "
        f"across {len(patient_ids)} patients."
    )


if __name__ == "__main__":
    generate_and_store_embeddings()