import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["JWT_SECRET_KEY"] = "test-only-secret-with-at-least-32-bytes"
os.environ["GROQ_API_KEY"] = "test-only-not-a-real-groq-key"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pymupdf

from database import Base, get_db
from models import Document
import study_materials
import llm_service
import main


class ProductionVerificationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

        def test_database():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        self.test_database = test_database
        main.app.dependency_overrides[get_db] = self.test_database
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def csrf(client):
        res = client.get("/auth/csrf")
        return res.json().get("csrf_token") if res.status_code == 200 else None

    def mutate(self, client, method, path, csrf, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["X-CSRF-Token"] = csrf
        return client.request(method, path, headers=headers, **kwargs)

    # -------------------------------------------------------------
    # A. CORS Verification
    # -------------------------------------------------------------
    def test_cors_production_origin_preflight_and_headers(self):
        prod_origin = "https://eduguide-frontend.onrender.com"
        headers = {
            "Origin": prod_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-CSRF-Token",
        }
        response = self.client.options("/auth/login", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), prod_origin)
        self.assertEqual(response.headers.get("access-control-allow-credentials"), "true")
        self.assertIn("POST", response.headers.get("access-control-allow-methods", ""))

    def test_cors_disallowed_origin_rejected(self):
        disallowed_origin = "https://malicious-site.example.com"
        headers = {
            "Origin": disallowed_origin,
            "Access-Control-Request-Method": "POST",
        }
        response = self.client.options("/auth/login", headers=headers)
        self.assertNotEqual(response.headers.get("access-control-allow-origin"), disallowed_origin)

    def test_cors_headers_present_on_unhandled_exception(self):
        prod_origin = "https://eduguide-frontend.onrender.com"

        def failing_db():
            raise RuntimeError("Simulated unhandled server failure")

        main.app.dependency_overrides[get_db] = failing_db
        test_client = TestClient(main.app, raise_server_exceptions=False)
        try:
            response = test_client.get("/health/ready", headers={"Origin": prod_origin})
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.headers.get("access-control-allow-origin"), prod_origin)
            self.assertEqual(response.headers.get("access-control-allow-credentials"), "true")
        finally:
            test_client.close()
            main.app.dependency_overrides[get_db] = self.test_database

    # -------------------------------------------------------------
    # B. Liveness & Readiness Checks
    # -------------------------------------------------------------
    def test_liveness_endpoint_responds_without_external_dependencies(self):
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "alive")
        self.assertIn("timestamp", data)

    def test_readiness_endpoint_succeeds_when_db_healthy(self):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "ready")
        self.assertEqual(data.get("database"), "available")

    # -------------------------------------------------------------
    # C. Authentication & Session Security
    # -------------------------------------------------------------
    def test_auth_full_lifecycle_and_csrf_protection(self):
        client = TestClient(main.app)
        csrf_token = self.csrf(client)
        self.assertIsNotNone(csrf_token)

        # 1. Register
        reg_resp = self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf_token,
            json={
                "name": "Production Verification Student",
                "email": "pv_student@sairam.edu",
                "password": "CorrectPassword123!",
            },
        )
        self.assertEqual(reg_resp.status_code, 201)
        self.assertIn("csrf_token", reg_resp.json())

        # 2. Get /auth/me authenticated
        me_resp = client.get("/auth/me")
        self.assertEqual(me_resp.status_code, 200)
        self.assertEqual(me_resp.json()["user"]["email"], "pv_student@sairam.edu")

        # 3. Invalid CSRF token on mutating route -> 403
        bad_csrf_resp = client.post(
            "/conversations",
            headers={"X-CSRF-Token": "invalid-token"},
            json={"title": "Test Chat"},
        )
        self.assertEqual(bad_csrf_resp.status_code, 403)

        # 4. Logout
        logout_csrf = self.csrf(client)
        logout_resp = self.mutate(client, "POST", "/auth/logout", logout_csrf)
        self.assertEqual(logout_resp.status_code, 200)

        # 5. Subsequent /auth/me returns 401
        after_logout_me = client.get("/auth/me")
        self.assertEqual(after_logout_me.status_code, 401)

        # 6. Invalid login
        login_csrf = self.csrf(client)
        bad_login = self.mutate(
            client,
            "POST",
            "/auth/login",
            login_csrf,
            json={"email": "pv_student@sairam.edu", "password": "WrongPassword"},
        )
        self.assertEqual(bad_login.status_code, 401)

        client.close()

    # -------------------------------------------------------------
    # D. Document Text Extraction & Database Persistence
    # -------------------------------------------------------------
    def test_pdf_upload_persists_extracted_text_in_database_and_recovers_after_disk_loss(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Doc Persistence User",
                "email": "doc_user@sairam.edu",
                "password": "DocPassword123!",
            },
        )

        # Create a small valid test PDF with distinctive content
        doc = pymupdf.open()
        page = doc.new_page()
        distinctive_sentence = "The unique formula for EduGuide Quantum Leap is QL-9988-ALPHA."
        page.insert_text((50, 72), distinctive_sentence)
        pdf_bytes = doc.tobytes()
        doc.close()

        # Upload PDF
        csrf = self.csrf(client)
        upload_resp = self.mutate(
            client,
            "POST",
            "/documents/upload",
            csrf,
            files={"file": ("quantum_guide.pdf", pdf_bytes, "application/pdf")},
        )
        self.assertEqual(upload_resp.status_code, 201)
        doc_data = upload_resp.json()
        doc_id = doc_data["id"]
        self.assertEqual(doc_data["status"], "indexed")

        # Verify database record has content_text and pages_json populated
        with self.session_factory() as db:
            db_doc = db.query(Document).filter(Document.id == doc_id).first()
            self.assertIsNotNone(db_doc)
            self.assertIsNotNone(db_doc.content_text)
            self.assertIn("QL-9988-ALPHA", db_doc.content_text)
            self.assertIsNotNone(db_doc.pages_json)

            # SIMULATE RENDER CONTAINER RESTART:
            # Delete file on disk (ephemeral storage wipe)
            storage_path = Path(db_doc.storage_path)
            if storage_path.exists():
                storage_path.unlink()
            self.assertFalse(storage_path.exists(), "Ephemeral file should be deleted")

            # Clear memory cache in study_materials
            study_materials._materials.pop((doc_id, db_doc.user_id), None)

            # Now call load_material: it should restore from DB content_text / pages_json
            material = study_materials.load_material(db_doc, db_doc.user_id)
            self.assertIsNotNone(material, "Material must be restored from database content_text")
            self.assertTrue(any("QL-9988-ALPHA" in chunk for chunk in material.chunks))
            self.assertIsNotNone(material.index, "FAISS index must be recreated from database text")

        client.close()

    def test_empty_or_malformed_pdf_rejected_cleanly(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Invalid Doc User",
                "email": "invalid_doc@sairam.edu",
                "password": "Password123!",
            },
        )

        csrf = self.csrf(client)
        # Empty file
        empty_resp = self.mutate(
            client,
            "POST",
            "/documents/upload",
            csrf,
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )
        self.assertIn(empty_resp.status_code, (400, 422))

        # Malformed PDF (garbage bytes)
        csrf = self.csrf(client)
        corrupt_resp = self.mutate(
            client,
            "POST",
            "/documents/upload",
            csrf,
            files={"file": ("corrupt.pdf", b"%PDF-not-a-valid-pdf-content", "application/pdf")},
        )
        self.assertIn(corrupt_resp.status_code, (400, 422))

        client.close()

    # -------------------------------------------------------------
    # E. AI Question Answering & Grounding
    # -------------------------------------------------------------
    def test_chat_grounded_answer_includes_material_facts(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Grounded Chat User",
                "email": "grounded@sairam.edu",
                "password": "Password123!",
            },
        )

        # Upload document with distinctive fact
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 72), "Supervised learning uses labeled training examples such as ground truth pairs.")
        pdf_bytes = doc.tobytes()
        doc.close()

        csrf = self.csrf(client)
        upload_resp = self.mutate(
            client,
            "POST",
            "/documents/upload",
            csrf,
            files={"file": ("supervised.pdf", pdf_bytes, "application/pdf")},
        )
        self.assertEqual(upload_resp.status_code, 201)
        doc_id = upload_resp.json()["id"]

        # Create conversation with attached document
        csrf = self.csrf(client)
        conv_resp = self.mutate(
            client,
            "POST",
            "/conversations",
            csrf,
            json={"title": "Supervised Learning Study"},
        )
        self.assertEqual(conv_resp.status_code, 201)
        conv_id = conv_resp.json()["id"]

        # Capture prompt passed to mock LLM
        captured_prompts = []

        def mock_llm_create(*args, **kwargs):
            messages = kwargs.get("messages", [])
            for m in messages:
                captured_prompts.append(m.get("content", ""))
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Based on your material, supervised learning uses labeled training examples."
                        )
                    )
                ]
            )

        with patch.object(llm_service.client.chat.completions, "create", side_effect=mock_llm_create):
            csrf = self.csrf(client)
            chat_resp = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={
                    "conversation_id": conv_id,
                    "question": "What does supervised learning use?",
                    "document_ids": [doc_id],
                },
            )
            self.assertEqual(chat_resp.status_code, 200)
            data = chat_resp.json()
            self.assertIn("labeled training examples", data["answer"])
            self.assertEqual(len(data.get("sources", [])), 1)

            # Verify that the LLM received the context containing the distinctive sentence
            prompt_text = " ".join(captured_prompts)
            self.assertIn("labeled training examples", prompt_text)

        client.close()

    def test_provider_error_classification(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Error Classification User",
                "email": "err_user@sairam.edu",
                "password": "Password123!",
            },
        )

        csrf = self.csrf(client)
        conv_resp = self.mutate(
            client,
            "POST",
            "/conversations",
            csrf,
            json={"title": "Error Chat"},
        )
        conv_id = conv_resp.json()["id"]

        # Simulate provider 429 rate limit
        with patch.object(main, "answer_question", side_effect=RuntimeError("429 rate limit exceeded")):
            csrf = self.csrf(client)
            rate_resp = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={"conversation_id": conv_id, "question": "Trigger 429"},
            )
            self.assertEqual(rate_resp.status_code, 429)
            self.assertIn("rate limit", rate_resp.json()["detail"]["message"].lower())

        # Simulate provider timeout
        with patch.object(main, "answer_question", side_effect=TimeoutError("Groq request timed out")):
            csrf = self.csrf(client)
            timeout_resp = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={"conversation_id": conv_id, "question": "Trigger timeout"},
            )
            self.assertEqual(timeout_resp.status_code, 504)
            self.assertIn("timed out", timeout_resp.json()["detail"]["message"].lower())

        client.close()

    def test_normal_question_without_selected_documents_has_no_bda_contamination(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "General Question User",
                "email": "general_q@sairam.edu",
                "password": "Password123!",
            },
        )

        captured_messages = []

        def mock_llm_create(*args, **kwargs):
            messages = kwargs.get("messages", [])
            captured_messages.extend(messages)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Supervised learning is an algorithm trained on labeled data."
                        )
                    )
                ]
            )

        with patch.object(llm_service.client.chat.completions, "create", side_effect=mock_llm_create):
            csrf = self.csrf(client)
            chat_resp = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={"question": "What is supervised learning?"},
            )
            self.assertEqual(chat_resp.status_code, 200)
            data = chat_resp.json()
            self.assertIn("Supervised learning", data["answer"])
            # Ensure no BDA chunks or study materials were injected into the prompt
            prompt_content = " ".join([m.get("content", "") for m in captured_messages])
            self.assertNotIn("bda 2 marks", prompt_content.lower())
            self.assertIn("no study materials were selected", prompt_content.lower())

        client.close()

    def test_two_different_questions_produce_different_answers(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Two Question User",
                "email": "two_q@sairam.edu",
                "password": "Password123!",
            },
        )

        def mock_llm_create(*args, **kwargs):
            messages = kwargs.get("messages", [])
            last_prompt = messages[-1].get("content", "")
            if "calculus" in last_prompt.lower():
                answer = "Calculus is the mathematical study of continuous change."
            else:
                answer = "Photosynthesis is the process by which green plants synthesize nutrients."
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=answer))]
            )

        with patch.object(llm_service.client.chat.completions, "create", side_effect=mock_llm_create):
            csrf = self.csrf(client)
            resp1 = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={"question": "What is calculus?"},
            )
            self.assertEqual(resp1.status_code, 200)

            csrf = self.csrf(client)
            resp2 = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={"question": "What is photosynthesis?"},
            )
            self.assertEqual(resp2.status_code, 200)

            self.assertNotEqual(resp1.json()["answer"], resp2.json()["answer"])
            self.assertIn("Calculus", resp1.json()["answer"])
            self.assertIn("Photosynthesis", resp2.json()["answer"])

        client.close()

    def test_question_not_present_in_selected_pdf_signals_limitation_in_prompt(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Missing Fact User",
                "email": "missing_fact@sairam.edu",
                "password": "Password123!",
            },
        )

        # Upload a document about Database Systems
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 72), "Relational databases use SQL for querying structured tables.")
        pdf_bytes = doc.tobytes()
        doc.close()

        csrf = self.csrf(client)
        upload_resp = self.mutate(
            client,
            "POST",
            "/documents/upload",
            csrf,
            files={"file": ("sql_guide.pdf", pdf_bytes, "application/pdf")},
        )
        doc_id = upload_resp.json()["id"]

        captured_messages = []

        def mock_llm_create(*args, **kwargs):
            messages = kwargs.get("messages", [])
            captured_messages.extend(messages)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="The selected study material does not contain information about photosynthesis.\n\n### General Academic Explanation\nPhotosynthesis converts light into energy."
                        )
                    )
                ]
            )

        with patch.object(llm_service.client.chat.completions, "create", side_effect=mock_llm_create):
            csrf = self.csrf(client)
            chat_resp = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={
                    "question": "What is photosynthesis and the Calvin cycle?",
                    "document_ids": [doc_id],
                },
            )
            self.assertEqual(chat_resp.status_code, 200)
            data = chat_resp.json()
            self.assertIn("does not contain information", data["answer"])
            prompt_content = " ".join([m.get("content", "") for m in captured_messages])
            self.assertIn("no matching or relevant excerpts were found", prompt_content)

        client.close()

    def test_multiple_selected_pdfs_retrieve_respective_content(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Multi PDF User",
                "email": "multi_pdf@sairam.edu",
                "password": "Password123!",
            },
        )

        # Upload PDF 1: Physics
        doc1 = pymupdf.open()
        page1 = doc1.new_page()
        page1.insert_text((50, 72), "Newton's first law states that an object remains at rest unless acted upon.")
        pdf1_bytes = doc1.tobytes()
        doc1.close()

        csrf = self.csrf(client)
        upload1 = self.mutate(
            client,
            "POST",
            "/documents/upload",
            csrf,
            files={"file": ("physics.pdf", pdf1_bytes, "application/pdf")},
        )
        doc1_id = upload1.json()["id"]

        # Upload PDF 2: Biology
        doc2 = pymupdf.open()
        page2 = doc2.new_page()
        page2.insert_text((50, 72), "Mitochondria generates ATP through oxidative phosphorylation.")
        pdf2_bytes = doc2.tobytes()
        doc2.close()

        csrf = self.csrf(client)
        upload2 = self.mutate(
            client,
            "POST",
            "/documents/upload",
            csrf,
            files={"file": ("biology.pdf", pdf2_bytes, "application/pdf")},
        )
        doc2_id = upload2.json()["id"]

        with patch.object(
            llm_service.client.chat.completions,
            "create",
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Grounded response"))]
            ),
        ):
            # Query about physics with both attached
            csrf = self.csrf(client)
            resp_physics = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={
                    "question": "What does Newton's first law state?",
                    "document_ids": [doc1_id, doc2_id],
                },
            )
            self.assertEqual(resp_physics.status_code, 200)
            sources = resp_physics.json()["sources"]
            source_ids = [s["id"] for s in sources]
            self.assertIn(doc1_id, source_ids)

        client.close()

    def test_follow_up_question_receives_prior_conversation_history(self):
        client = TestClient(main.app)
        csrf = self.csrf(client)
        self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Followup User",
                "email": "followup@sairam.edu",
                "password": "Password123!",
            },
        )

        captured_calls = []

        def mock_llm_create(*args, **kwargs):
            messages = kwargs.get("messages", [])
            captured_calls.append(list(messages))
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Machine learning is algorithms finding patterns."
                        )
                    )
                ]
            )

        with patch.object(llm_service.client.chat.completions, "create", side_effect=mock_llm_create):
            # First question
            csrf = self.csrf(client)
            first_resp = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={"question": "What is machine learning?"},
            )
            self.assertEqual(first_resp.status_code, 200)
            conv_id = first_resp.json()["conversation_id"]

            # Second question (follow-up)
            csrf = self.csrf(client)
            second_resp = self.mutate(
                client,
                "POST",
                "/chat",
                csrf,
                json={
                    "conversation_id": conv_id,
                    "question": "Can you give a math formula for it?",
                },
            )
            self.assertEqual(second_resp.status_code, 200)

            # Check that the second call received the prior user and assistant messages
            self.assertGreaterEqual(len(captured_calls), 2)
            second_call_messages = captured_calls[1]
            roles = [m.get("role") for m in second_call_messages]
            self.assertIn("user", roles)
            self.assertIn("assistant", roles)
            history_text = " ".join([m.get("content", "") for m in second_call_messages])
            self.assertIn("What is machine learning?", history_text)

        client.close()


if __name__ == "__main__":
    unittest.main()

