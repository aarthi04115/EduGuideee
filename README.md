# 🎓 EduGuide – Academic Learning Assistant

EduGuide is an **LLM-powered academic learning assistant** that uses **Retrieval-Augmented Generation (RAG)** to answer student questions using pre-loaded academic learning materials.

Instead of relying only on the LLM's general knowledge, EduGuide retrieves relevant content from academic documents and provides that content to the LLM as context before generating an answer.

---

## 🚀 Current Features

* 📚 Academic PDF-based knowledge retrieval
* 🔎 Semantic search using vector embeddings
* 🧠 FAISS-based similarity search
* 🤖 LLM-powered answer generation
* 🔗 Retrieval-Augmented Generation (RAG)
* ⚡ FastAPI backend
* 📖 Context-grounded academic responses
* 🔐 Environment-based API key management
* 👤 Argon2 student accounts with revocable, HttpOnly-cookie sessions
* 💬 PostgreSQL conversation and message history
* 🔒 Account-owned conversations and private study materials
* 🧱 Alembic-managed additive database migrations

---

## 🏗️ System Architecture

```text
                    Student
                       │
                       ▼
                Chat / API Request
                       │
                       ▼
                  FastAPI
                       │
                       ▼
                RAG Pipeline
                       │
             ┌─────────┴─────────┐
             │                   │
             ▼                   ▼
       Query Embedding      Academic PDF
             │                   │
             ▼                   ▼
          FAISS            Text Extraction
             │                   │
             ▼                   ▼
     Relevant Chunks          Chunking
             │
             └─────────┬─────────┘
                       │
                       ▼
                  Context
                       │
                       ▼
                     LLM
                       │
                       ▼
                  Answer
                       │
                       ▼
                   Student
```

---

## 🔄 How RAG Works in EduGuide

EduGuide follows these steps when a student asks a question:

### 1. Student Question

Example:

```text
What is Big Data?
```

### 2. Query Embedding

The question is converted into a numerical vector using:

```text
all-MiniLM-L6-v2
```

The resulting embedding contains **384 numerical values**.

### 3. Semantic Retrieval

FAISS compares the question embedding with the embeddings of the academic document chunks.

The most relevant chunks are retrieved based on vector similarity.

### 4. Context Construction

The retrieved academic chunks are combined into a context.

```text
Student Question
       +
Retrieved Academic Content
       ↓
     Context
```

### 5. LLM Generation

The question and retrieved context are sent to the LLM.

The LLM generates an answer based primarily on the provided academic material.

---

## 🧩 Technology Stack

| Component            | Technology            |
| -------------------- | --------------------- |
| Programming Language | Python                |
| Backend Framework    | FastAPI               |
| API Server           | Uvicorn               |
| PDF Processing       | PyMuPDF               |
| Text Embeddings      | Sentence Transformers |
| Embedding Model      | all-MiniLM-L6-v2      |
| Vector Search        | FAISS                 |
| LLM Access           | Groq                  |
| LLM Client           | OpenAI Python SDK     |
| Database             | PostgreSQL + SQLAlchemy |
| Migrations           | Alembic               |
| Authentication       | Argon2 + signed HttpOnly cookies |
| API Documentation    | Swagger / OpenAPI     |
| Version Control      | Git + GitHub          |

---

## 📁 Project Structure

```text
EduGuide/
│
├── .env
├── .env.example
├── .gitignore
├── README.md
│
└── backend/
    │
    ├── main.py
    ├── auth.py
    ├── database.py
    ├── models.py
    ├── schemas.py
    ├── security.py
    ├── study_materials.py
    ├── alembic/
    ├── alembic.ini
    ├── requirements.txt
    ├── llm_service.py
    ├── rag_pipeline.py
    ├── document_processor.py
    ├── chunker.py
    ├── embeddings.py
    ├── retriever.py
    │
    ├── data/
    │   └── documents/
    │       └── BDA 2 marks.pdf
    │
    └── test files
        ├── test_embedding.py
        ├── test_llm_context.py
        ├── test_rag.py
        ├── test_rag_pipeline.py
        └── test_retrieval.py
```

---

## ⚙️ Core Components

### `document_processor.py`

Extracts text from academic PDF documents using PyMuPDF.

```text
PDF
 ↓
Extracted Text
```

### `chunker.py`

Splits the extracted document into smaller overlapping chunks.

Current configuration:

```text
Chunk Size: 1000 characters
Overlap: 200 characters
```

### `embeddings.py`

Generates semantic embeddings using:

```text
all-MiniLM-L6-v2
```

Each chunk is represented as a **384-dimensional vector**.

### `retriever.py`

Creates a FAISS vector index and performs similarity search.

The current implementation uses:

```text
FAISS IndexFlatL2
```

### `llm_service.py`

Connects EduGuide to Groq using its OpenAI-compatible API.

The LLM receives:

```text
Academic Context
+
Student Question
```

and generates the final response.

### `rag_pipeline.py`

Connects the complete retrieval and generation process:

```text
Question
 ↓
Embedding
 ↓
FAISS Search
 ↓
Top-k Chunks
 ↓
Context
 ↓
LLM
 ↓
Answer
```

### `main.py`

Provides the FastAPI endpoints.

Current endpoints:

```text
GET  /
POST /chat
```

---

## 🧪 Example

### Request

```json
{
  "question": "What is Big Data?"
}
```

### Response

```json
{
  "question": "What is Big Data?",
  "answer": "Based on the academic context, Big Data describes extremely complex, massive datasets..."
}
```

The response is generated using information retrieved from the academic document.

---

## ▶️ Running the Project

### 1. Clone the repository

```bash
git clone https://github.com/aarthi04115/EduGuide.git
cd EduGuide
```

### 2. Create and activate a virtual environment

Windows:

```powershell
python -m venv venv
venv\Scripts\activate
```

### 3. Install dependencies

Install the backend dependencies:

```bash
pip install -r backend/requirements.txt
```

### 4. Configure the application

Copy the example configuration into a root `.env` file, then set the Groq API key, a strong random JWT secret, and the connection string for the **existing** `eduguide_db` PostgreSQL database. Configuration lookup checks the process environment first, then the repository-root `.env`, then `backend/.env`; use one authoritative `DATABASE_URL` to avoid ambiguity.

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the generated value in `JWT_SECRET_KEY` in `.env`. `DATABASE_URL` uses the `postgresql+psycopg://` scheme and must point to an already-created database; the application and migration do not create or recreate the database. URL-encode reserved characters in the database username/password (for example, `@` becomes `%40`) rather than placing raw reserved characters in the URL. Never commit `.env`, expose the Groq key, or use a predictable JWT secret. The example defaults are for local HTTP development; set `COOKIE_SECURE=true` when serving over HTTPS. Keep the frontend and backend on the same hostname locally (`localhost`) so the browser sends the session cookie consistently.

`GROQ_MODEL` is optional and defaults to `openai/gpt-oss-120b`. `ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 30. `COOKIE_SAMESITE` defaults to `lax`, and `EDUGUIDE_CORS_ORIGINS` defaults to the local Vite origin. For a database password embedded in `DATABASE_URL`, percent-encode reserved characters in the password before assembling the URL (`@` → `%40`, `:` → `%3A`, `/` → `%2F`, `?` → `%3F`, `#` → `%23`, `%` → `%25`, and space → `%20`). Encode the password exactly once; do not share or log the resulting credential.

### 5. Apply the database migration

After confirming that `DATABASE_URL` targets the intended existing database and that a current backup is available, apply the additive Alembic migration once from the repository root:

```powershell
Push-Location .\backend
python -m alembic upgrade head
python -m alembic current
Pop-Location
```

The migrations create the `users`, `auth_sessions`, `conversations`, `messages`, `documents`, `conversation_documents`, `college_sources`, and `college_chunks` tables and add persisted source metadata to messages. They do not create the PostgreSQL database. Revision `20261004_0002` adds conversation-scoped document associations and message source metadata; revision `20261004_0003` adds the shared official-college knowledge base. Both revisions are additive and preserve existing records.

### 6. Start the backend

From the repository root:

```powershell
Push-Location .\backend
python -m uvicorn main:app --reload
Pop-Location
```

The API includes `GET /` and `GET /health/ready`, authentication endpoints (`POST /auth/register`, `POST /auth/login`, `GET /auth/me`, `POST /auth/logout`), conversation/history endpoints (`GET` and `POST /conversations`, `GET /conversations/{id}/messages`, `GET` and `POST /conversations/{id}/documents`, `DELETE /conversations/{id}/documents/{document_id}`, `PATCH` and `DELETE /conversations/{id}`), `POST /chat`, and authenticated document endpoints (`GET /documents`, `POST /documents/upload`, `DELETE /documents/{id}`). Mutating requests use the CSRF token bootstrapped from `GET /auth/csrf`. Uploads accept PDF and TXT files up to 10 MB, are privately stored, indexed synchronously, and associated with the active conversation.

An uploaded PDF is extracted page by page; extracted text is chunked and indexed with the existing local FAISS architecture. Its generated storage path, owner, indexing status, and conversation association are stored in PostgreSQL. Startup only counts indexed study materials; a user's document is restored when selected for chat. The shared college index and bundled academic PDF index are also built on first use. Indexes are encoded in batches and cached by content hash, so unchanged content can reuse a persisted FAISS index. The in-memory study-material index cache holds at most two documents. Set `EDUGUIDE_INDEX_CACHE_DIR` to a persistent location to retain indexes across service restarts; `EDUGUIDE_UPLOAD_DIR` should also point to persistent storage because PostgreSQL stores upload paths, not file contents. Chat retrieval still filters to the active conversation's associated documents and authenticated owner, and message source metadata (including page numbers when available) is persisted with the assistant response. Apply database changes with `python -m alembic upgrade head` before starting the backend.

### Render deployment memory and storage

For a Render web service, configure `DATABASE_URL`, `JWT_SECRET_KEY`, `GROQ_API_KEY`, `COOKIE_SECURE=true`, and the production `EDUGUIDE_CORS_ORIGINS` in the service's environment settings. Use one Uvicorn worker on a 512 MiB instance so each worker does not load a separate PyTorch model and FAISS indexes. Run migrations against the existing database after taking a backup; do not drop or recreate it. If the Render service supports a persistent disk, mount it (for example at `/var/data`) and set `EDUGUIDE_UPLOAD_DIR=/var/data/uploads` and `EDUGUIDE_INDEX_CACHE_DIR=/var/data/index_cache`. Without persistent upload storage, existing database document rows can point to files lost on redeploy; without persistent index storage, indexes are safely rebuilt when first needed, but embeddings must be recomputed. After deployment, check startup logs, `GET /health/ready`, and Render's memory graph, then test a chat that uses college content and a separately uploaded document.

### 8. Import official college website information

The bounded crawler imports relevant public HTML pages from `https://sairam.edu.in/` into the shared `college_sources` and `college_chunks` tables. It checks each approved host's `robots.txt`, follows sitemap entries when available, falls back to relevant links on the homepage, restricts requests to the official apex/`www` hosts, and defaults to at most 100 pages with a one-second per-request delay. It does not bypass access controls. Existing college content is retained as stale if a later fetch fails; changed content replaces that URL's old chunks, and exact duplicate page text is not indexed twice.

Run these commands from the repository root after applying Alembic migrations. First inspect the capped dry-run bundle; dry runs make no database writes:

```powershell
Push-Location .\backend
python -m ingestion.ingest_college_website --max-pages 50 --delay-seconds 1 --dry-run --export-dir .\data\college-review
Pop-Location
```

Review `backend\data\college-review\manifest.json` and `college-pages.jsonl`. If the discovered pages and extracted text are appropriate, run the same capped crawl without `--dry-run` to index them:

```powershell
python -m ingestion.ingest_college_website --max-pages 50 --delay-seconds 1 --export-dir .\data\college-review
python -m ingestion.ingest_college_website --stats
Pop-Location
```

Use `--include-pdfs` to include linked public PDFs; each file remains subject to the crawler's 15 MiB limit and robots checks. To refresh the indexed content, rerun the import command. Retrieval keeps official college sources separate from private student uploads and attaches a source title/URL only when that page contributed retrieved context. Pages blocked by robots, inaccessible pages, JavaScript-only content, and PDFs unless explicitly enabled are not imported; inspect the manifest's `failures` list. The crawler is capped and intended for repeatable updates, not an unrestricted full-site mirror.

After import, test questions such as “Which undergraduate programmes are offered?”, “What contact information does the college publish?”, and “Where can I find the academic calendar?” The answer should include a source link when relevant indexed college content is retrieved; it should not invent unavailable policies or details.

### 9. Start the frontend

In another terminal:

```powershell
Set-Location frontend
Copy-Item .env.example .env
npm ci
npm run dev
```

The default backend URL is `http://localhost:8000`. Override it in `frontend/.env` with `VITE_API_BASE_URL` if needed. `VITE_API_BASE_URL` is a public URL only; do not put Groq keys, database credentials, or JWT secrets in frontend environment variables.

### 10. Open the API documentation

Open:

```text
http://localhost:8000/docs
```

The React application restores the session using `/auth/me`, lists conversation summaries after sign-in, and fetches message history only when a conversation is selected. Chat history and uploaded document metadata are persisted in PostgreSQL and restricted to their owner. The Groq API key remains server-side; authentication uses HttpOnly cookies and CSRF protection.

---

## 🧪 Current Testing

The RAG pipeline has been tested using an academic Big Data Analytics PDF.

For the query:

```text
What is Volume in Big Data?
```

FAISS successfully retrieved relevant academic chunks containing information about:

* Volume
* Velocity
* Variety
* Big Data characteristics
* Examples of large-scale data

The retrieved context was then passed to the LLM, which generated an academic answer grounded in the retrieved material.

---

## 🎯 Project Goal

The long-term goal of EduGuide is to evolve from a basic academic RAG chatbot into an **adaptive learning assistant**.

Planned capabilities include:

```text
Student Question
       ↓
Intent Detection
       ↓
Learning Mode
       ↓
Academic Retrieval
       ↓
Context-Aware LLM
       ↓
Personalized Explanation
       ↓
Student Feedback
       ↓
Adaptive Explanation
```

Planned features include:

* Intent detection
* Beginner learning mode
* Intermediate learning mode
* Exam-answer mode
* Interview/viva preparation mode
* Adaptive explanation based on student feedback
* Quiz and practice generation
* Learning-gap detection
* Academic source metadata

These features will be added incrementally as the project develops.



## 👩‍💻 Project

**EduGuide – Adaptive Academic Learning Assistant**

Built using Python, FastAPI, FAISS, Sentence Transformers, RAG, and LLM technology.

---

## 📄 License

This project is intended for academic and educational purposes.
